package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/deployment"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/review"
)

type DeploymentRepository struct {
	Pool    *pgxpool.Pool
	Store   artifact.Store
	Targets []deployment.Target
}

const deploymentSelect = `SELECT id::text,model_version_id::text,runtime,precision,target_profile,max_batch,status,source_descriptor,compiled_descriptor,validation,error,COALESCE(worker_id,''),updated_at::text FROM model_deployments`

func scanDeployment(row pgx.Row) (deployment.Record, error) {
	var r deployment.Record
	var source, compiled, validation []byte
	err := row.Scan(&r.ID, &r.ModelVersionID, &r.Runtime, &r.Precision, &r.TargetProfile, &r.MaxBatch, &r.Status, &source, &compiled, &validation, &r.Error, &r.WorkerID, &r.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return r, review.ErrNotFound
	}
	if err != nil {
		return r, err
	}
	if err = json.Unmarshal(source, &r.Source); err != nil {
		return r, err
	}
	if len(compiled) > 0 {
		if err = json.Unmarshal(compiled, &r.Compiled); err != nil {
			return r, err
		}
	}
	err = json.Unmarshal(validation, &r.Validation)
	return r, err
}
func (s *DeploymentRepository) Records(ctx context.Context, modelID string) ([]deployment.Record, error) {
	rows, err := s.Pool.Query(ctx, deploymentSelect+` WHERE model_version_id=$1 ORDER BY created_at,id`, modelID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []deployment.Record{}
	for rows.Next() {
		r, e := scanDeployment(rows)
		if e != nil {
			return nil, e
		}
		result = append(result, r)
	}
	return result, rows.Err()
}
func (s *DeploymentRepository) List(ctx context.Context, modelID string) ([]deployment.Record, error) {
	v, err := NewModelRegistryRepository(s.Pool).Get(ctx, modelID)
	if err != nil {
		return nil, err
	}
	_, portable := deployment.PortableVariants(v)
	records, err := s.Records(ctx, v.ID)
	return append(portable, records...), err
}
func (s *DeploymentRepository) RuntimeTargets() []deployment.Target { return s.Targets }
func (s *DeploymentRepository) Create(ctx context.Context, modelID string, input deployment.Create) (deployment.Record, error) {
	if err := input.Validate(); err != nil {
		return deployment.Record{}, err
	}
	profile := ""
	for _, t := range s.Targets {
		if t.Runtime == input.Runtime && t.Address != "" {
			profile = t.Profile
		}
	}
	if profile == "" {
		return deployment.Record{}, fmt.Errorf("该硬件 Worker 尚未配置: %w", review.ErrConflict)
	}
	v, err := NewModelRegistryRepository(s.Pool).Get(ctx, modelID)
	if err != nil {
		return deployment.Record{}, err
	}
	_, variants := deployment.PortableVariants(v)
	var source artifact.Descriptor
	for _, r := range variants {
		if r.Precision == "FP32" && r.Source.ArtifactType == "full_onnx" {
			source = r.Source
			break
		}
	}
	if source.ArtifactID == "" {
		return deployment.Record{}, fmt.Errorf("请先发布完整模型 ONNX FP32: %w", review.ErrConflict)
	}
	raw, err := json.Marshal(source)
	if err != nil {
		return deployment.Record{}, err
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return deployment.Record{}, err
	}
	defer tx.Rollback(ctx)
	var status string
	if err = tx.QueryRow(ctx, `SELECT status FROM model_versions WHERE id=$1 FOR SHARE`, v.ID).Scan(&status); err != nil {
		return deployment.Record{}, err
	}
	if status != "production" {
		return deployment.Record{}, review.ErrConflict
	}
	id, token := uuid.NewString(), uuid.NewString()
	err = tx.QueryRow(ctx, `INSERT INTO model_deployments(id,model_version_id,source_artifact_id,runtime,precision,target_profile,max_batch,status,source_descriptor,build_token,actor)
 VALUES($1,$2,$3,$4,$5,$6,$7,'queued',$8,$9,$10)
 ON CONFLICT(model_version_id,source_artifact_id,runtime,precision,target_profile,max_batch) DO UPDATE SET actor=model_deployments.actor
 RETURNING id::text,build_token::text,status`, id, v.ID, source.ArtifactID, input.Runtime, input.Precision, profile, input.MaxBatch, raw, token, input.Actor).Scan(&id, &token, &status)
	if err != nil {
		return deployment.Record{}, err
	}
	if status == "queued" {
		_, err = tx.Exec(ctx, `INSERT INTO deployment_outbox(id,deployment_id,build_token,runtime,trace_context) VALUES($1,$2,$3,$4,$5) ON CONFLICT(deployment_id,build_token) DO NOTHING`, uuid.NewString(), id, token, input.Runtime, serializedTraceContext(ctx))
		if err != nil {
			return deployment.Record{}, err
		}
		_, err = tx.Exec(ctx, `INSERT INTO model_deployment_events(id,deployment_id,event_type,actor,to_status,payload)
		 VALUES($1,$2,'queued',$3,'queued',jsonb_build_object('runtime',$4,'precision',$5,'target_profile',$6))`, uuid.NewString(), id, input.Actor, input.Runtime, input.Precision, profile)
		if err != nil {
			return deployment.Record{}, err
		}
	}
	if err = tx.Commit(ctx); err != nil {
		return deployment.Record{}, err
	}
	return scanDeployment(s.Pool.QueryRow(ctx, deploymentSelect+` WHERE id=$1`, id))
}
func (s *DeploymentRepository) Retry(ctx context.Context, id string) (deployment.Record, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return deployment.Record{}, err
	}
	defer tx.Rollback(ctx)
	var runtime string
	token := uuid.NewString()
	err = tx.QueryRow(ctx, `UPDATE model_deployments d SET status='queued',build_token=$2,worker_id=NULL,lease_expires_at=NULL,error='',updated_at=now()
 WHERE d.id=$1 AND d.status='failed' AND EXISTS(SELECT 1 FROM model_versions m WHERE m.id=d.model_version_id AND m.status='production') RETURNING runtime`, id, token).Scan(&runtime)
	if errors.Is(err, pgx.ErrNoRows) {
		return deployment.Record{}, review.ErrConflict
	}
	if err != nil {
		return deployment.Record{}, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO deployment_outbox(id,deployment_id,build_token,runtime,trace_context) VALUES($1,$2,$3,$4,$5)`, uuid.NewString(), id, token, runtime, serializedTraceContext(ctx))
	if err != nil {
		return deployment.Record{}, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO model_deployment_events(id,deployment_id,event_type,actor,from_status,to_status)
	 VALUES($1,$2,'retried','operator','failed','queued')`, uuid.NewString(), id)
	if err != nil {
		return deployment.Record{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return deployment.Record{}, err
	}
	return scanDeployment(s.Pool.QueryRow(ctx, deploymentSelect+` WHERE id=$1`, id))
}
func (s *DeploymentRepository) Claim(ctx context.Context, id, token, worker, runtime, profile string) (deployment.Record, error) {
	if worker == "" || len(worker) > 200 {
		return deployment.Record{}, review.ErrInvalid
	}
	tag, err := s.Pool.Exec(ctx, `UPDATE model_deployments d SET status='building',worker_id=$3,lease_expires_at=now()+interval '2 minutes',updated_at=now()
 WHERE id=$1 AND build_token=$2 AND runtime=$4 AND target_profile=$5 AND (status='queued' OR (status='building' AND worker_id=$3 AND lease_expires_at>now()))
 AND EXISTS(SELECT 1 FROM model_versions m WHERE m.id=d.model_version_id AND m.status='production')`, id, token, worker, runtime, profile)
	if err != nil {
		return deployment.Record{}, err
	}
	if tag.RowsAffected() != 1 {
		return deployment.Record{}, review.ErrConflict
	}
	_, _ = s.Pool.Exec(ctx, `INSERT INTO model_deployment_events(id,deployment_id,event_type,actor,from_status,to_status,payload)
	 VALUES($1,$2,'claimed',$3,'queued','building',jsonb_build_object('runtime',$4,'target_profile',$5))`, uuid.NewString(), id, worker, runtime, profile)
	r, err := scanDeployment(s.Pool.QueryRow(ctx, deploymentSelect+` WHERE id=$1`, id))
	if err == nil {
		err = s.Pool.QueryRow(ctx, `SELECT dataset_id::text FROM model_versions WHERE id=$1`, r.ModelVersionID).Scan(&r.DatasetID)
	}
	r.BuildToken = token
	return r, err
}
func (s *DeploymentRepository) Heartbeat(ctx context.Context, id, token, worker string) error {
	tag, err := s.Pool.Exec(ctx, `UPDATE model_deployments SET lease_expires_at=now()+interval '2 minutes',updated_at=now() WHERE id=$1 AND build_token=$2 AND worker_id=$3 AND status='building' AND lease_expires_at>now()`, id, token, worker)
	if err == nil && tag.RowsAffected() != 1 {
		return review.ErrConflict
	}
	return err
}
func (s *DeploymentRepository) Complete(ctx context.Context, id, token, worker string, compiled *artifact.Descriptor, validation map[string]any, failure string) error {
	r, err := scanDeployment(s.Pool.QueryRow(ctx, deploymentSelect+` WHERE id=$1`, id))
	if err != nil {
		return err
	}
	if failure == "" {
		if compiled == nil || compiled.Metadata["deployment_id"] != id || compiled.Metadata["source_onnx_sha256"] != r.Source.SHA256 || compiled.Metadata["precision"] != r.Precision || compiled.Metadata["target_profile"] != r.TargetProfile || compiled.Metadata["runtime"] != r.Runtime || compiled.Metadata["parity_passed"] != true || validation["parity_passed"] != true || compiled.DatasetVersionID != r.Source.DatasetVersionID || compiled.TrainingRunID != r.Source.TrainingRunID || compiled.SizeBytes <= 0 || !strings.HasPrefix(compiled.URI, "s3://") {
			return fmt.Errorf("部署产物校验不通过: %w", review.ErrInvalid)
		}
		if _, e := uuid.Parse(compiled.ArtifactID); e != nil {
			return review.ErrInvalid
		}
		if compiled.Metadata["runtime_fingerprint"] == nil {
			return review.ErrInvalid
		}
		kind := "tensorrt_engine"
		if r.Runtime == "ascend_acl" {
			kind = "ascend_om"
		}
		if compiled.ArtifactType != kind {
			return review.ErrInvalid
		}
		if err = s.Store.Verify(ctx, *compiled); err != nil {
			return err
		}
	}
	var diagnostic *artifact.Descriptor
	if rawDiagnostic, ok := validation["diagnostic_log"]; ok {
		raw, marshalErr := json.Marshal(rawDiagnostic)
		if marshalErr != nil {
			return review.ErrInvalid
		}
		var candidate artifact.Descriptor
		if json.Unmarshal(raw, &candidate) != nil || candidate.ArtifactType != "deployment_log" || candidate.Metadata["deployment_id"] != id || candidate.DatasetVersionID != r.Source.DatasetVersionID || candidate.TrainingRunID != r.Source.TrainingRunID || candidate.SizeBytes < 0 || !strings.HasPrefix(candidate.URI, "s3://") {
			return fmt.Errorf("部署诊断日志校验不通过: %w", review.ErrInvalid)
		}
		if err = s.Store.Verify(ctx, candidate); err != nil {
			return err
		}
		diagnostic = &candidate
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	// Match inference/create lock ordering: model, then deployment.
	var modelStatus string
	if err = tx.QueryRow(ctx, `SELECT status FROM model_versions WHERE id=$1 FOR SHARE`, r.ModelVersionID).Scan(&modelStatus); err != nil {
		return err
	}
	var state, actualToken, owner string
	var lease bool
	err = tx.QueryRow(ctx, `SELECT status,build_token::text,COALESCE(worker_id,''),COALESCE(lease_expires_at>now(),false) FROM model_deployments WHERE id=$1 FOR UPDATE`, id).Scan(&state, &actualToken, &owner, &lease)
	if err != nil {
		return err
	}
	if actualToken != token || owner != worker {
		return review.ErrConflict
	}
	if state == "ready" && failure == "" {
		if r.Compiled == nil || r.Compiled.ArtifactID != compiled.ArtifactID || r.Compiled.SHA256 != compiled.SHA256 {
			return review.ErrConflict
		}
		return nil
	}
	if state == "failed" && failure != "" {
		return nil
	}
	if state != "building" || !lease {
		return review.ErrConflict
	}
	if modelStatus != "production" {
		failure = "模型已下架，部署未激活"
	}
	if len([]rune(failure)) > 2000 {
		failure = string([]rune(failure)[:2000])
	}
	if failure != "" {
		_, err = tx.Exec(ctx, `UPDATE model_deployments SET status='failed',error=$2,lease_expires_at=NULL,updated_at=now() WHERE id=$1`, id, failure)
	} else {
		metadata, _ := json.Marshal(compiled.Metadata)
		_, err = tx.Exec(ctx, `INSERT INTO artifacts(id,artifact_key,artifact_type,dataset_id,dataset_version_id,training_run_id,uri,checksum,size_bytes,content_type,storage_version,producer,schema_version,verified_at,artifact_metadata,created_at)
   SELECT $1::uuid,$1::text,$2,dataset_id,$3,$4,$5,$6,$7,$8,$9,$10,1,now(),$11,now() FROM model_versions WHERE id=$12`, compiled.ArtifactID, compiled.ArtifactType, compiled.DatasetVersionID, compiled.TrainingRunID, compiled.URI, compiled.SHA256, compiled.SizeBytes, compiled.ContentType, compiled.StorageVersion, compiled.Producer, metadata, r.ModelVersionID)
		if err != nil {
			return err
		}
		raw, _ := json.Marshal(compiled)
		report, _ := json.Marshal(validation)
		_, err = tx.Exec(ctx, `UPDATE model_deployments SET status='ready',compiled_artifact_id=$2,compiled_descriptor=$3,validation=$4,lease_expires_at=NULL,updated_at=now() WHERE id=$1`, id, compiled.ArtifactID, raw, report)
	}
	if err != nil {
		return err
	}
	if diagnostic != nil {
		metadata, _ := json.Marshal(diagnostic.Metadata)
		_, err = tx.Exec(ctx, `INSERT INTO artifacts(id,artifact_key,artifact_type,dataset_id,dataset_version_id,training_run_id,uri,checksum,size_bytes,content_type,storage_version,producer,schema_version,verified_at,artifact_metadata,created_at)
		 SELECT $1::uuid,$1::text,$2,dataset_id,$3,$4,$5,$6,$7,$8,$9,$10,1,now(),$11,now() FROM model_versions WHERE id=$12 ON CONFLICT(id) DO NOTHING`, diagnostic.ArtifactID, diagnostic.ArtifactType, diagnostic.DatasetVersionID, diagnostic.TrainingRunID, diagnostic.URI, diagnostic.SHA256, diagnostic.SizeBytes, diagnostic.ContentType, diagnostic.StorageVersion, diagnostic.Producer, metadata, r.ModelVersionID)
		if err != nil {
			return err
		}
	}
	targetStatus, eventType := "ready", "completed"
	if failure != "" {
		targetStatus, eventType = "failed", "failed"
	}
	_, err = tx.Exec(ctx, `INSERT INTO model_deployment_events(id,deployment_id,event_type,actor,from_status,to_status,payload)
	 VALUES($1,$2,$3,$4,'building',$5,jsonb_build_object('error',$6))`, uuid.NewString(), id, eventType, worker, targetStatus, failure)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

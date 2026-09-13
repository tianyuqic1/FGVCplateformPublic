package postgres

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	computev1 "github.com/tianyuqic1/FGVCplateformPublic/go/api/proto/finevision/compute/v1"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/deployment"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/review"
	_ "golang.org/x/image/bmp"
	_ "golang.org/x/image/webp"
	"google.golang.org/protobuf/types/known/structpb"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// InferenceService keeps scope, storage and feedback orchestration in Go;
// only image/model computation crosses the gRPC boundary.
type InferenceService struct {
	LegacyUploadRoot string
	Pool             *pgxpool.Pool
	Store            artifact.Store
	Preview          *dataset.PreviewService
	Client           computev1.InferenceRuntimeClient
	Deployments      *DeploymentRepository
	RuntimeClients   map[string]computev1.InferenceRuntimeClient
}

func protoArtifact(a artifact.Descriptor) *computev1.ArtifactDescriptor {
	return &computev1.ArtifactDescriptor{ArtifactId: a.ArtifactID, ArtifactType: a.ArtifactType, Uri: a.URI, Sha256: a.SHA256, SizeBytes: a.SizeBytes, ContentType: a.ContentType, DatasetVersionId: a.DatasetVersionID, TrainingRunId: a.TrainingRunID, SchemaVersion: int32(a.SchemaVersion)}
}
func (s *InferenceService) Predict(ctx context.Context, input map[string]any, data []byte) (map[string]any, error) {
	model, err := NewModelRegistryRepository(s.Pool).Get(ctx, textInput(input, "model_version_id"))
	if err != nil {
		return nil, err
	}
	if model.Status != "production" {
		return nil, fmt.Errorf("模型尚未发布，不能推理: %w", review.ErrConflict)
	}
	version := textInput(input, "dataset_version_id")
	if version != model.DatasetVersionID && version != model.DatasetVersionKey {
		return nil, fmt.Errorf("模型与数据集版本不匹配: %w", review.ErrInvalid)
	}
	deploymentID := textInput(input, "deployment_id")
	var records []deployment.Record
	if deploymentID != "" && !strings.HasPrefix(deploymentID, "onnx:") && s.Deployments != nil {
		records, err = s.Deployments.Records(ctx, model.ID)
		if err != nil {
			return nil, err
		}
	}
	bundle, selected, err := deployment.Resolve(model, records, deploymentID)
	if err != nil {
		return nil, err
	}
	published := selected.Source
	client := s.Client
	if selected.Runtime != "onnx_cpu" {
		client = s.RuntimeClients[selected.Runtime]
	}
	if client == nil {
		return nil, fmt.Errorf("所选推理 Worker 未配置: %w", review.ErrConflict)
	}
	policy := map[string]any{"top_k": 3., "evidence_k": 3.}
	for _, key := range []string{"top_k", "evidence_k", "accept_threshold", "margin_threshold", "ood_distance_threshold"} {
		if value, exists := input[key]; exists && value != nil {
			n, ok := value.(float64)
			if !ok || math.IsNaN(n) || math.IsInf(n, 0) || n < 0 {
				return nil, review.ErrInvalid
			}
			if (key == "top_k" || key == "evidence_k") && (n > 100 || n != math.Trunc(n) || (key == "top_k" && n < 1)) {
				return nil, review.ErrInvalid
			}
			if (key == "accept_threshold" || key == "margin_threshold") && n > 1 {
				return nil, review.ErrInvalid
			}
			policy[key] = n
		}
	}
	var policyKey string
	var conf, margin float64
	var ood *float64
	err = s.Pool.QueryRow(ctx, `SELECT policy_key,tau_conf,tau_margin,tau_ood FROM abstention_policy_versions WHERE model_version_id=$1 AND dataset_version_id=$2 AND status='active'`, model.ID, model.DatasetVersionID).Scan(&policyKey, &conf, &margin, &ood)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return nil, err
	}
	if err == nil {
		policy["accept_threshold"] = conf
		policy["margin_threshold"] = margin
		if ood != nil {
			policy["ood_distance_threshold"] = *ood
		}
	}
	sample := textInput(input, "sample_id")
	if nested, ok := input["image_input"].(map[string]any); ok && sample == "" {
		sample = textInput(nested, "sample_id")
	}
	inputType := "upload"
	if len(data) == 0 {
		if sample == "" {
			return nil, review.ErrInvalid
		}
		inputType = "sample"
		data, _, err = s.Preview.Image(ctx, model.DatasetVersionID, sample)
		if err != nil {
			return nil, err
		}
	}
	cfg, _, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil || len(data) > 20<<20 || cfg.Width < 1 || cfg.Height < 1 || int64(cfg.Width)*int64(cfg.Height) > 40_000_000 {
		return nil, fmt.Errorf("仅支持有效 JPEG/PNG 图片（不超过 20 MiB、4000 万像素）: %w", review.ErrInvalid)
	}
	f, err := os.CreateTemp("", "inference-image-")
	if err != nil {
		return nil, err
	}
	defer os.Remove(f.Name())
	_, err = f.Write(data)
	closeErr := f.Close()
	if err != nil {
		return nil, err
	}
	if closeErr != nil {
		return nil, closeErr
	}
	a, err := s.Store.PutFile(ctx, f.Name(), artifact.PutRequest{ArtifactID: uuid.NewString(), ArtifactType: "inference_image", ContentType: http.DetectContentType(data), Producer: "go-inference", DatasetVersionID: model.DatasetVersionID})
	if err != nil {
		return nil, err
	}
	encoded, _ := json.Marshal(published)
	var onnx map[string]any
	if err = json.Unmarshal(encoded, &onnx); err != nil {
		return nil, err
	}
	policy["published_onnx"] = onnx
	if selected.Compiled != nil {
		raw, _ := json.Marshal(selected)
		var configured map[string]any
		if err = json.Unmarshal(raw, &configured); err != nil {
			return nil, err
		}
		policy["deployment"] = configured
	}
	policy["deployment_id"] = selected.ID
	p, err := structpb.NewStruct(policy)
	if err != nil {
		return nil, err
	}
	started := time.Now()
	prediction, err := client.Predict(ctx, &computev1.PredictRequest{RequestId: uuid.NewString(), ModelBundle: protoArtifact(bundle), InputImage: protoArtifact(a), Policy: p})
	if err != nil {
		return nil, err
	}
	top := []map[string]any{}
	runtimeMetadata := map[string]any{"requested_runtime": selected.Runtime, "actual_runtime": selected.Runtime, "precision": selected.Precision, "deployment_id": selected.ID, "latency_ms": float64(time.Since(started).Microseconds()) / 1000, "fallback_reason": nil}
	if actual, ok := prediction.Evidence.AsMap()["runtime"].(map[string]any); ok {
		if actual["backend"] != selected.Runtime || actual["deployment_id"] != selected.ID || actual["precision"] != selected.Precision {
			return nil, fmt.Errorf("推理 Worker 返回的部署身份不匹配: %w", review.ErrConflict)
		}
		runtimeMetadata["worker"] = actual
	} else if selected.Runtime != "onnx_cpu" {
		return nil, fmt.Errorf("加速 Worker 未返回实际运行后端: %w", review.ErrConflict)
	} else {
		runtimeMetadata["actual_runtime"] = "unreported"
	}
	for _, v := range prediction.TopK {
		top = append(top, map[string]any{"label": v.Label, "score": v.Score})
	}
	decision := prediction.Decision
	if decision != "accept" && decision != "abstain" && decision != "reject_ood" {
		return nil, fmt.Errorf("invalid compute decision")
	}
	reasons := prediction.Reasons
	if reasons == nil {
		reasons = []string{}
	}
	thresholds := map[string]any{}
	for _, k := range []string{"accept_threshold", "margin_threshold", "ood_distance_threshold"} {
		if v, ok := policy[k]; ok {
			thresholds[k] = v
		}
	}
	if v, ok := prediction.Evidence.AsMap()["thresholds"]; ok {
		thresholds = v.(map[string]any)
	}
	runID, eventID := uuid.NewString(), uuid.NewString()
	imageURL := "/api/uploads/" + a.ArtifactID
	inputRef := a.URI
	contextValue := map[string]any{"top_k": top, "decision": map[string]any{"decision": decision, "confidence": prediction.Confidence, "margin": prediction.Margin, "ood_score": prediction.OodScore, "reasons": reasons, "thresholds": thresholds}, "nearest_neighbors": prediction.Evidence.AsMap()["nearest_neighbors"], "image_url": imageURL}
	result := map[string]any{"inference_event_id": eventID, "inference_run_id": runID, "dataset_id": model.DatasetID, "dataset_version_id": model.DatasetVersionID, "model_version_id": model.ID, "model_artifact_id": published.ArtifactID, "input": map[string]any{"sample_id": sample, "image_url": imageURL, "input_ref": inputRef}, "result": contextValue}
	result["deployment_id"], result["runtime"] = selected.ID, runtimeMetadata
	if selected.Compiled != nil {
		published = *selected.Compiled
		result["model_artifact_id"] = published.ArtifactID
	}
	reviewID := ""
	if decision != "accept" || input["force_review"] == true {
		reviewID = "review-" + uuid.NewString()
		result["review_item_id"] = reviewID
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	// Lock release state through persistence: a concurrently retired model cannot
	// silently produce a newly recorded production inference.
	var current string
	var datasetID string
	err = tx.QueryRow(ctx, `SELECT status,dataset_id::text FROM model_versions WHERE id=$1 FOR SHARE`, model.ID).Scan(&current, &datasetID)
	if err != nil {
		return nil, err
	}
	if current != "production" {
		return nil, review.ErrConflict
	}
	if selected.Compiled != nil {
		var ready bool
		if err = tx.QueryRow(ctx, `SELECT status='ready' FROM model_deployments WHERE id=$1 FOR SHARE`, selected.ID).Scan(&ready); err != nil {
			return nil, err
		}
		if !ready {
			return nil, review.ErrConflict
		}
	}
	metadata, _ := json.Marshal(a.Metadata)
	_, err = tx.Exec(ctx, `INSERT INTO artifacts(id,artifact_key,artifact_type,dataset_id,dataset_version_id,uri,checksum,size_bytes,content_type,storage_version,producer,schema_version,verified_at,artifact_metadata,created_at) VALUES($1::uuid,$1::text,$2,$3,$4,$5,$6,$7,$8,$9,$10,1,now(),$11,now())`, a.ArtifactID, a.ArtifactType, datasetID, model.DatasetVersionID, a.URI, a.SHA256, a.SizeBytes, a.ContentType, a.StorageVersion, a.Producer, metadata)
	if err != nil {
		return nil, err
	}
	rawInput, _ := json.Marshal(input)
	rawResult, _ := json.Marshal(result)
	rawContext, _ := json.Marshal(contextValue)
	rawReasons, _ := json.Marshal(reasons)
	rawThresholds, _ := json.Marshal(thresholds)
	reviewCount := 0
	if reviewID != "" {
		reviewCount = 1
	}
	policySource := "model_default"
	if policyKey != "" {
		policySource = "active_policy"
	}
	_, err = tx.Exec(ctx, `INSERT INTO inference_runs(id,run_key,dataset_id,dataset_version_id,model_version_id,run_type,status,item_count,review_item_count,applied_policy_key,applied_policy_source,threshold_snapshot,request_payload,summary,created_at,updated_at,finished_at) VALUES($1::uuid,$1::text,$2,$3,$4,$5,'succeeded',1,$6,NULLIF($7,''),$8,$9,$10,'{}',now(),now(),now())`, runID, datasetID, model.DatasetVersionID, model.ID, map[bool]string{true: "upload", false: "single"}[inputType == "upload"], reviewCount, policyKey, policySource, rawThresholds, rawInput)
	if err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO inference_events(id,event_key,inference_run_id,dataset_id,dataset_version_id,model_version_id,model_status,model_artifact_id,input_type,input_ref,sample_id,decision,confidence,margin,ood_score,reasons,request_payload,result_payload,created_at) VALUES($1::uuid,$1::text,$2,$3,$4,$5,'production',$6,$7,$8,NULLIF($9,''),$10,$11,$12,$13,$14,$15,$16,now())`, eventID, runID, datasetID, model.DatasetVersionID, model.ID, published.ArtifactID, inputType, inputRef, sample, decision, prediction.Confidence, prediction.Margin, prediction.OodScore, rawReasons, rawInput, rawResult)
	if err != nil {
		return nil, err
	}
	if reviewID != "" {
		risk := "low_confidence"
		if decision == "reject_ood" {
			risk = "ood_candidate"
		}
		_, err = tx.Exec(ctx, `INSERT INTO review_items(id,review_key,inference_event_id,inference_run_id,dataset_id,dataset_version_id,model_version_id,sample_id,input_ref,status,risk_type,priority,reason,reason_codes,context,assistance_metadata,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,NULLIF($8,''),$9,'pending',$10,50,'待人工确认',$11,$12,'{}',now(),now())`, uuid.NewString(), reviewID, eventID, runID, datasetID, model.DatasetVersionID, model.ID, sample, inputRef, risk, rawReasons, rawContext)
		if err != nil {
			return nil, err
		}
	}
	_, err = tx.Exec(ctx, `INSERT INTO abstention_shadow_decisions(id,policy_version_id,inference_event_id,current_decision,shadow_decision,decision_diff,score_snapshot,created_at)
 SELECT gen_random_uuid(),id,$1,$2,shadow,CASE WHEN shadow=$2 THEN 'same' WHEN shadow='reject_ood' THEN 'new_rejects_ood' WHEN shadow='accept' AND $2='abstain' THEN 'new_accepts_old_abstains' WHEN shadow='abstain' AND $2='accept' THEN 'new_abstains_old_accepts' ELSE 'other_change' END,jsonb_build_object('confidence',$3::float,'margin',$4::float,'ood_score',$5::float),now()
 FROM (SELECT p.id,CASE WHEN p.tau_ood IS NOT NULL AND $5::float>p.tau_ood THEN 'reject_ood' WHEN $3::float<p.tau_conf OR $4::float<p.tau_margin THEN 'abstain' ELSE 'accept' END shadow FROM abstention_policy_versions p WHERE p.model_version_id=$6 AND p.dataset_version_id=$7 AND p.status IN ('shadow','candidate')) x`, eventID, decision, prediction.Confidence, prediction.Margin, prediction.OodScore, model.ID, model.DatasetVersionID)
	if err != nil {
		return nil, err
	}
	rawRuntime, _ := json.Marshal(runtimeMetadata)
	if _, err = tx.Exec(ctx, `UPDATE inference_events SET deployment_id=$2,runtime_metadata=$3 WHERE id=$1`, eventID, selected.ID, rawRuntime); err != nil {
		return nil, err
	}
	if err = tx.Commit(ctx); err != nil {
		return nil, err
	}
	return result, nil
}
func (s *InferenceService) Image(ctx context.Context, id string) ([]byte, string, error) {
	if _, err := uuid.Parse(id); err != nil {
		if s.LegacyUploadRoot == "" || !regexp.MustCompile(`^[a-fA-F0-9]{32}\.(png|jpg|jpeg|webp|bmp)$`).MatchString(id) {
			return nil, "", review.ErrNotFound
		}
		var exists bool
		if err = s.Pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM review_items WHERE input_ref=$1)`, "/data/uploads/"+id).Scan(&exists); err != nil {
			return nil, "", err
		}
		if !exists {
			return nil, "", review.ErrNotFound
		}
		root, err := os.OpenRoot(s.LegacyUploadRoot)
		if err != nil {
			return nil, "", review.ErrNotFound
		}
		defer root.Close()
		file, err := root.Open(filepath.Base(id))
		if err != nil {
			return nil, "", review.ErrNotFound
		}
		defer file.Close()
		info, err := file.Stat()
		if err != nil || !info.Mode().IsRegular() || info.Size() > 20<<20 {
			return nil, "", review.ErrNotFound
		}
		data, err := io.ReadAll(io.LimitReader(file, (20<<20)+1))
		return data, http.DetectContentType(data), err
	}
	var a artifact.Descriptor
	err := s.Pool.QueryRow(ctx, `SELECT id::text,uri,checksum,size_bytes,content_type FROM artifacts WHERE id=$1 AND artifact_type='inference_image'`, id).Scan(&a.ArtifactID, &a.URI, &a.SHA256, &a.SizeBytes, &a.ContentType)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, "", review.ErrNotFound
	}
	if err != nil {
		return nil, "", err
	}
	if a.SizeBytes > 20<<20 {
		return nil, "", review.ErrInvalid
	}
	root, err := os.MkdirTemp("", "inference-preview-")
	if err != nil {
		return nil, "", err
	}
	defer os.RemoveAll(root)
	path, err := s.Store.MaterializeVerified(ctx, a, root)
	if err != nil {
		return nil, "", err
	}
	data, err := os.ReadFile(path)
	return data, a.ContentType, err
}

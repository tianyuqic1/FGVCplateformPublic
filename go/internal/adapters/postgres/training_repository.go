package postgres

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
)

type TrainingRepository struct {
	pool *pgxpool.Pool
}

func NewTrainingRepository(pool *pgxpool.Pool) *TrainingRepository {
	return &TrainingRepository{pool: pool}
}

func (repository *TrainingRepository) Create(ctx context.Context, aggregate *training.Aggregate) error {
	return pgx.BeginFunc(ctx, repository.pool, func(tx pgx.Tx) error {
		payload, err := json.Marshal(aggregate.Job.Payload)
		if err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `
INSERT INTO jobs (
  id, job_key, job_type, status, payload, priority, attempt_count, max_attempts,
  dispatch_generation, execution_epoch, available_at, created_at, queued_at, updated_at
) VALUES ($1, $2, 'train_classifier', $3, $4, 100, 0, $5, $6, 0, $7, $8, $8, $8)`,
			aggregate.Job.ID, "job-"+aggregate.Job.ID, aggregate.Job.Status, payload,
			aggregate.Job.MaxAttempts, aggregate.Job.DispatchGeneration, aggregate.Job.AvailableAt, aggregate.Job.CreatedAt)
		if err != nil {
			return err
		}
		extractorConfig, headConfig := nestedConfig(aggregate.Job.Payload, "extractor_config"), nestedConfig(aggregate.Job.Payload, "head_config")
		_, err = tx.Exec(ctx, `
INSERT INTO training_runs (
  id, run_key, job_id, dataset_id, dataset_version_id, status, backbone_id,
  extractor_config, head_config, metrics, created_at, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, '{}'::jsonb, $10, $10)`,
			aggregate.Run.ID, "run-"+aggregate.Run.ID, aggregate.Job.ID, aggregate.Run.DatasetID,
			aggregate.Run.DatasetVersionID, aggregate.Run.Status, aggregate.Run.BackboneID,
			extractorConfig, headConfig, aggregate.Run.CreatedAt)
		if err != nil {
			return err
		}
		if err := insertEvents(ctx, tx, aggregate.Job.ID, aggregate.Events); err != nil {
			return err
		}
		return insertOutbox(ctx, tx, aggregate.Outbox)
	})
}

func (repository *TrainingRepository) Get(ctx context.Context, id string) (*training.Aggregate, error) {
	var aggregate *training.Aggregate
	err := pgx.BeginFunc(ctx, repository.pool, func(tx pgx.Tx) error {
		loaded, err := loadAggregate(ctx, tx, id, false)
		aggregate = loaded
		return err
	})
	return aggregate, err
}

func (repository *TrainingRepository) Update(ctx context.Context, id string, update func(*training.Aggregate) error) error {
	return pgx.BeginFunc(ctx, repository.pool, func(tx pgx.Tx) error {
		aggregate, err := loadAggregate(ctx, tx, id, true)
		if err != nil {
			return err
		}
		oldEvents, oldOutbox := len(aggregate.Events), len(aggregate.Outbox)
		oldAttempts := make(map[string]struct{}, len(aggregate.Attempts))
		for attemptID := range aggregate.Attempts {
			oldAttempts[attemptID] = struct{}{}
		}
		oldArtifacts := make(map[string]struct{}, len(aggregate.Artifacts))
		for _, descriptor := range aggregate.Artifacts {
			oldArtifacts[descriptor.ArtifactID] = struct{}{}
		}
		previousStatus := aggregate.Job.Status
		if err := update(aggregate); err != nil {
			return err
		}
		if err := persistAggregate(ctx, tx, aggregate, oldAttempts, oldArtifacts); err != nil {
			return err
		}
		if err := insertEvents(ctx, tx, aggregate.Job.ID, aggregate.Events[oldEvents:]); err != nil {
			return err
		}
		if err := insertOutbox(ctx, tx, aggregate.Outbox[oldOutbox:]); err != nil {
			return err
		}
		if previousStatus != training.StatusSucceeded && aggregate.Job.Status == training.StatusSucceeded {
			return insertModelVersion(ctx, tx, aggregate)
		}
		return nil
	})
}

func (repository *TrainingRepository) Expired(ctx context.Context, now time.Time, limit int) ([]string, error) {
	rows, err := repository.pool.Query(ctx, `
SELECT id::text
FROM jobs
WHERE status = 'running' AND lease_expires_at < $1
ORDER BY lease_expires_at
LIMIT $2`, now, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	ids := make([]string, 0)
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		ids = append(ids, id)
	}
	return ids, rows.Err()
}

func loadAggregate(ctx context.Context, tx pgx.Tx, id string, lock bool) (*training.Aggregate, error) {
	query := `
SELECT id::text, status, payload, max_attempts, attempt_count, dispatch_generation,
       execution_epoch, COALESCE(active_attempt_id::text, ''), available_at,
       COALESCE(last_heartbeat_at, created_at), created_at, updated_at,
       COALESCE(result, '{}'::jsonb), COALESCE(error_message, '')
FROM jobs WHERE id = $1`
	if lock {
		query += " FOR UPDATE"
	}
	var job training.Job
	var status string
	var payload, resultJSON []byte
	if err := tx.QueryRow(ctx, query, id).Scan(
		&job.ID, &status, &payload, &job.MaxAttempts, &job.AttemptCount, &job.DispatchGeneration,
		&job.ExecutionEpoch, &job.ActiveAttemptID, &job.AvailableAt, &job.LastHeartbeatAt,
		&job.CreatedAt, &job.UpdatedAt, &resultJSON, &job.ErrorMessage,
	); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, &training.DomainError{Code: training.CodeNotFound, Message: "training job not found"}
		}
		return nil, err
	}
	job.Status = training.Status(status)
	if err := json.Unmarshal(payload, &job.Payload); err != nil {
		return nil, err
	}
	_ = json.Unmarshal(resultJSON, &job.Result)

	var run training.Run
	var runStatus string
	var progress []byte
	if err := tx.QueryRow(ctx, `
SELECT id::text, job_id::text, dataset_id::text, dataset_version_id::text, backbone_id,
       status, COALESCE((metrics->'progress'), '{}'::jsonb), created_at, updated_at
FROM training_runs WHERE job_id = $1`, id).Scan(
		&run.ID, &run.JobID, &run.DatasetID, &run.DatasetVersionID, &run.BackboneID,
		&runStatus, &progress, &run.CreatedAt, &run.UpdatedAt,
	); err != nil {
		return nil, err
	}
	run.Status = training.Status(runStatus)
	_ = json.Unmarshal(progress, &run.Progress)

	aggregate := &training.Aggregate{Job: job, Run: run, Attempts: map[string]*training.Attempt{}}
	if err := loadAttempts(ctx, tx, id, aggregate); err != nil {
		return nil, err
	}
	if err := loadEvents(ctx, tx, id, aggregate); err != nil {
		return nil, err
	}
	if err := loadOutbox(ctx, tx, id, aggregate); err != nil {
		return nil, err
	}
	if err := loadArtifacts(ctx, tx, id, aggregate); err != nil {
		return nil, err
	}
	if err := loadMetricPoints(ctx, tx, aggregate.Run.ID, aggregate); err != nil {
		return nil, err
	}
	return aggregate, nil
}

func loadMetricPoints(ctx context.Context, tx pgx.Tx, runID string, aggregate *training.Aggregate) error {
	rows, err := tx.Query(ctx, `
SELECT id, training_run_id::text, attempt_id::text, execution_epoch, metric_name, step,
       value, recorded_at, context
FROM training_metric_points
WHERE training_run_id=$1
ORDER BY id`, runID)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var point training.MetricPoint
		var contextJSON []byte
		if err := rows.Scan(&point.ID, &point.TrainingRunID, &point.AttemptID, &point.ExecutionEpoch,
			&point.Name, &point.Step, &point.Value, &point.RecordedAt, &contextJSON); err != nil {
			return err
		}
		_ = json.Unmarshal(contextJSON, &point.Context)
		aggregate.Metrics = append(aggregate.Metrics, point)
	}
	return rows.Err()
}

func loadAttempts(ctx context.Context, tx pgx.Tx, jobID string, aggregate *training.Aggregate) error {
	rows, err := tx.Query(ctx, `
SELECT id::text, attempt_number, execution_epoch, worker_id, status, lease_expires_at,
       last_heartbeat_at, started_at, COALESCE(finished_at, started_at),
       COALESCE(completion_key, ''), COALESCE(result_digest, ''), COALESCE(result, '{}'::jsonb),
       COALESCE(error_code, ''), COALESCE(error_message, '')
FROM job_attempts WHERE job_id = $1 ORDER BY attempt_number`, jobID)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var attempt training.Attempt
		var status string
		var result []byte
		if err := rows.Scan(&attempt.ID, &attempt.Number, &attempt.ExecutionEpoch, &attempt.WorkerID, &status,
			&attempt.LeaseExpiresAt, &attempt.LastHeartbeatAt, &attempt.StartedAt, &attempt.FinishedAt,
			&attempt.CompletionKey, &attempt.ResultDigest, &result, &attempt.ErrorCode, &attempt.ErrorMessage); err != nil {
			return err
		}
		attempt.Status = training.AttemptStatus(status)
		_ = json.Unmarshal(result, &attempt.Result)
		aggregate.Attempts[attempt.ID] = &attempt
	}
	return rows.Err()
}

func loadEvents(ctx context.Context, tx pgx.Tx, jobID string, aggregate *training.Aggregate) error {
	rows, err := tx.Query(ctx, `SELECT event_type, COALESCE(message, ''), created_at FROM job_events WHERE job_id = $1 ORDER BY created_at`, jobID)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var event training.AuditEvent
		if err := rows.Scan(&event.EventType, &event.Message, &event.CreatedAt); err != nil {
			return err
		}
		aggregate.Events = append(aggregate.Events, event)
	}
	return rows.Err()
}

func loadOutbox(ctx context.Context, tx pgx.Tx, jobID string, aggregate *training.Aggregate) error {
	rows, err := tx.Query(ctx, `
SELECT message_id, event_type, schema_version, aggregate_id::text,
       (payload->>'dispatch_generation')::bigint, created_at
FROM outbox_events WHERE aggregate_id = $1 ORDER BY created_at`, jobID)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var event training.OutboxEvent
		if err := rows.Scan(&event.MessageID, &event.EventType, &event.SchemaVersion, &event.JobID,
			&event.DispatchGeneration, &event.OccurredAt); err != nil {
			return err
		}
		aggregate.Outbox = append(aggregate.Outbox, event)
	}
	return rows.Err()
}

func loadArtifacts(ctx context.Context, tx pgx.Tx, jobID string, aggregate *training.Aggregate) error {
	rows, err := tx.Query(ctx, `
SELECT a.id::text, a.artifact_type, a.uri, COALESCE(a.checksum, ''), COALESCE(a.size_bytes, 0),
       COALESCE(a.content_type, ''), a.storage_version, a.producer,
       COALESCE(a.dataset_version_id::text, ''), COALESCE(a.training_run_id::text, ''),
       COALESCE(a.attempt_id::text, ''), a.schema_version, a.created_at,
       COALESCE(a.verified_at, a.created_at), a.artifact_metadata
FROM artifacts a WHERE a.job_id = $1 ORDER BY a.created_at`, jobID)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var descriptor artifact.Descriptor
		var metadata []byte
		if err := rows.Scan(&descriptor.ArtifactID, &descriptor.ArtifactType, &descriptor.URI,
			&descriptor.SHA256, &descriptor.SizeBytes, &descriptor.ContentType, &descriptor.StorageVersion,
			&descriptor.Producer, &descriptor.DatasetVersionID, &descriptor.TrainingRunID,
			&descriptor.AttemptID, &descriptor.SchemaVersion, &descriptor.CreatedAt, &descriptor.VerifiedAt, &metadata); err != nil {
			return err
		}
		_ = json.Unmarshal(metadata, &descriptor.Metadata)
		aggregate.Artifacts = append(aggregate.Artifacts, descriptor)
	}
	return rows.Err()
}

func persistAggregate(
	ctx context.Context,
	tx pgx.Tx,
	aggregate *training.Aggregate,
	oldAttempts map[string]struct{},
	oldArtifacts map[string]struct{},
) error {
	// Insert newly-created attempts before jobs.active_attempt_id is updated. The
	// latter has a foreign key to job_attempts and both writes intentionally stay
	// inside the same lifecycle transaction.
	for id, attempt := range aggregate.Attempts {
		if _, exists := oldAttempts[id]; exists {
			continue
		}
		attemptResult, _ := json.Marshal(attempt.Result)
		if _, err := tx.Exec(ctx, `
INSERT INTO job_attempts (
 id, job_id, attempt_number, execution_epoch, worker_id, status, lease_expires_at,
 last_heartbeat_at, started_at, completion_key, result_digest, result
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NULLIF($10,''),NULLIF($11,''),$12)`,
			id, aggregate.Job.ID, attempt.Number, attempt.ExecutionEpoch, attempt.WorkerID, attempt.Status,
			attempt.LeaseExpiresAt, attempt.LastHeartbeatAt, attempt.StartedAt,
			attempt.CompletionKey, attempt.ResultDigest, attemptResult); err != nil {
			return err
		}
	}

	payload, _ := json.Marshal(aggregate.Job.Payload)
	result, _ := json.Marshal(aggregate.Job.Result)
	var activeAttempt any
	if aggregate.Job.ActiveAttemptID != "" {
		activeAttempt = aggregate.Job.ActiveAttemptID
	}
	var leaseOwner any
	var leaseExpires any
	if attempt := aggregate.Attempts[aggregate.Job.ActiveAttemptID]; attempt != nil && attempt.Status == training.AttemptRunning {
		leaseOwner, leaseExpires = attempt.WorkerID, attempt.LeaseExpiresAt
	}
	var heartbeat any
	if !aggregate.Job.LastHeartbeatAt.IsZero() {
		heartbeat = aggregate.Job.LastHeartbeatAt
	}
	_, err := tx.Exec(ctx, `
UPDATE jobs SET status=$2, payload=$3, result=$4, error_message=NULLIF($5,''), attempt_count=$6,
  dispatch_generation=$7, execution_epoch=$8, available_at=$9, active_attempt_id=$10,
  last_heartbeat_at=$11, lease_owner=$12, lease_expires_at=$13,
  started_at=CASE WHEN $2='running' AND started_at IS NULL THEN $14 ELSE started_at END,
  finished_at=CASE WHEN $2 IN ('succeeded','failed','cancelled') THEN $14 ELSE NULL END,
  updated_at=$14
WHERE id=$1`, aggregate.Job.ID, aggregate.Job.Status, payload, result, aggregate.Job.ErrorMessage,
		aggregate.Job.AttemptCount, aggregate.Job.DispatchGeneration, aggregate.Job.ExecutionEpoch,
		aggregate.Job.AvailableAt, activeAttempt, heartbeat, leaseOwner, leaseExpires, aggregate.Job.UpdatedAt)
	if err != nil {
		return err
	}
	progress, _ := json.Marshal(aggregate.Run.Progress)
	metrics, _ := json.Marshal(aggregate.Job.Result.Metrics)
	_, err = tx.Exec(ctx, `
UPDATE training_runs SET status=$2,
  metrics=COALESCE($3::jsonb, '{}'::jsonb) || jsonb_build_object('progress', COALESCE($4::jsonb, '{}'::jsonb)),
  error_message=NULLIF($5,''),
  started_at=CASE WHEN $2='running' AND started_at IS NULL THEN $6 ELSE started_at END,
  finished_at=CASE WHEN $2 IN ('succeeded','failed','cancelled') THEN $6 ELSE NULL END,
  updated_at=$6
WHERE id=$1`, aggregate.Run.ID, aggregate.Run.Status, metrics, progress, aggregate.Job.ErrorMessage, aggregate.Run.UpdatedAt)
	if err != nil {
		return err
	}
	for id, attempt := range aggregate.Attempts {
		if _, exists := oldAttempts[id]; !exists {
			continue
		}
		attemptResult, _ := json.Marshal(attempt.Result)
		var finishedAt any
		if attempt.Status != training.AttemptRunning && !attempt.FinishedAt.IsZero() {
			finishedAt = attempt.FinishedAt
		}
		_, err = tx.Exec(ctx, `
UPDATE job_attempts SET status=$2, lease_expires_at=$3, last_heartbeat_at=$4,
  finished_at=$5, error_code=NULLIF($6,''), error_message=NULLIF($7,''),
  completion_key=NULLIF($8,''), result_digest=NULLIF($9,''), result=$10
WHERE id=$1`, id, attempt.Status, attempt.LeaseExpiresAt, attempt.LastHeartbeatAt, finishedAt,
			attempt.ErrorCode, attempt.ErrorMessage, attempt.CompletionKey, attempt.ResultDigest, attemptResult)
		if err != nil {
			return err
		}
	}
	for _, descriptor := range aggregate.Artifacts {
		if _, exists := oldArtifacts[descriptor.ArtifactID]; exists {
			continue
		}
		metadata, _ := json.Marshal(descriptor.Metadata)
		_, err = tx.Exec(ctx, `
INSERT INTO artifacts (
 id, artifact_key, artifact_type, dataset_version_id, job_id, uri, checksum, content_type,
 size_bytes, storage_version, producer, training_run_id, attempt_id, schema_version,
 verified_at, artifact_metadata, created_at
) VALUES ($1::uuid,$1::text,$2,NULLIF($3,'')::uuid,$4,$5,$6,$7,$8,$9,$10,NULLIF($11,'')::uuid,
          NULLIF($12,'')::uuid,$13,$14,$15,$16)`,
			descriptor.ArtifactID, descriptor.ArtifactType, descriptor.DatasetVersionID,
			aggregate.Job.ID, descriptor.URI, descriptor.SHA256, descriptor.ContentType,
			descriptor.SizeBytes, descriptor.StorageVersion, descriptor.Producer,
			descriptor.TrainingRunID, descriptor.AttemptID, descriptor.SchemaVersion,
			descriptor.VerifiedAt, metadata, descriptor.CreatedAt)
		if err != nil {
			return err
		}
	}
	for _, point := range aggregate.Metrics {
		contextJSON, _ := json.Marshal(point.Context)
		if _, err := tx.Exec(ctx, `
INSERT INTO training_metric_points (
 training_run_id, attempt_id, execution_epoch, metric_name, step, value, recorded_at, context
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
ON CONFLICT (training_run_id, attempt_id, metric_name, step) DO NOTHING`,
			point.TrainingRunID, point.AttemptID, point.ExecutionEpoch, point.Name, point.Step,
			point.Value, point.RecordedAt, contextJSON); err != nil {
			return err
		}
	}
	return nil
}

func insertEvents(ctx context.Context, tx pgx.Tx, jobID string, events []training.AuditEvent) error {
	for _, event := range events {
		if _, err := tx.Exec(ctx, `
INSERT INTO job_events (id, job_id, event_type, message, payload, created_at)
VALUES ($1,$2,$3,$4,'{}'::jsonb,$5)`, randomUUID(), jobID, event.EventType, event.Message, event.CreatedAt); err != nil {
			return err
		}
	}
	return nil
}

func insertOutbox(ctx context.Context, tx pgx.Tx, events []training.OutboxEvent) error {
	for _, event := range events {
		payload, _ := json.Marshal(map[string]any{
			"message_id": event.MessageID, "event_type": event.EventType, "schema_version": event.SchemaVersion,
			"job_id": event.JobID, "dispatch_generation": event.DispatchGeneration, "occurred_at": event.OccurredAt,
		})
		if _, err := tx.Exec(ctx, `
INSERT INTO outbox_events (
 id, message_id, aggregate_type, aggregate_id, aggregate_version, event_type,
 schema_version, payload, available_at, created_at
) VALUES ($1,$2,'training_job',$3,$4,$5,$6,$7,$8,$8)`,
			randomUUID(), event.MessageID, event.JobID, event.DispatchGeneration,
			event.EventType, event.SchemaVersion, payload, event.OccurredAt); err != nil {
			return err
		}
	}
	return nil
}

func insertModelVersion(ctx context.Context, tx pgx.Tx, aggregate *training.Aggregate) error {
	var modelArtifactID string
	var featureArtifactID, reportArtifactID, calibrationArtifactID, thresholdArtifactID any
	for _, descriptor := range aggregate.Artifacts {
		switch descriptor.ArtifactType {
		case "model":
			modelArtifactID = descriptor.ArtifactID
		case "features":
			featureArtifactID = descriptor.ArtifactID
		case "report":
			reportArtifactID = descriptor.ArtifactID
		case "calibration":
			calibrationArtifactID = descriptor.ArtifactID
		case "threshold_strategy":
			thresholdArtifactID = descriptor.ArtifactID
		}
	}
	if modelArtifactID == "" {
		return &training.DomainError{Code: training.CodeValidationFailed, Message: "completion requires a model artifact"}
	}
	metrics, _ := json.Marshal(aggregate.Job.Result.Metrics)
	extractorConfig := nestedMap(aggregate.Job.Payload, "extractor_config")
	headConfig := nestedMap(aggregate.Job.Payload, "head_config")
	evaluationContext := map[string]any{
		"dataset_version_id": aggregate.Run.DatasetVersionID,
		"evaluation_split":   "test",
		"protocol_fingerprint": protocolFingerprint(aggregate.Run.DatasetVersionID, map[string]any{
			"head_config":           headConfig,
			"target_selective_risk": aggregate.Job.Payload["target_selective_risk"],
			"review_cost_per_item":  aggregate.Job.Payload["review_cost_per_item"],
		}),
	}
	evaluationJSON, _ := json.Marshal(evaluationContext)
	backboneKey := stringValue(extractorConfig, "backbone_key", aggregate.Run.BackboneID)
	architecture := stringValue(extractorConfig, "architecture", "")
	pretrainingMethod := stringValue(extractorConfig, "pretraining_method", "")
	pretrainingDataset := stringValue(extractorConfig, "pretraining_dataset", "")
	pooling := stringValue(extractorConfig, "feature_pool", "")
	headType := stringValue(headConfig, "head_type", "ridge_linear")
	inputSize := integerValue(extractorConfig["image_size"])
	featureDim := integerValue(extractorConfig["feature_dim"])
	parameterCount := integerValue(extractorConfig["parameter_count"])
	modelName := "Model " + aggregate.Job.Result.ModelVersionID[:8]
	_, err := tx.Exec(ctx, `
INSERT INTO model_versions (
 id, model_key, dataset_id, dataset_version_id, training_run_id, status,
 model_artifact_id, calibration_artifact_id, threshold_strategy_artifact_id,
 metrics, name, backbone_key, architecture, pretraining_method, pretraining_dataset,
 input_size, feature_dim, parameter_count, pooling, head_type, evaluation_context,
 created_at, updated_at
) VALUES ($1,$2,$3,$4,$5,'candidate',$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$21)
ON CONFLICT (id) DO NOTHING`, aggregate.Job.Result.ModelVersionID, "model-"+aggregate.Job.Result.ModelVersionID,
		aggregate.Run.DatasetID, aggregate.Run.DatasetVersionID, aggregate.Run.ID, modelArtifactID,
		calibrationArtifactID, thresholdArtifactID, metrics, modelName, backboneKey, architecture,
		pretrainingMethod, pretrainingDataset, inputSize, featureDim, parameterCount, pooling, headType,
		evaluationJSON, aggregate.Job.UpdatedAt)
	if err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO model_version_events (id,model_version_id,event_type,to_status,actor,reason,payload,created_at)
SELECT $1,$2,'created','candidate','training-lifecycle','training completed',jsonb_build_object('training_run_id',$3::text),$4
WHERE NOT EXISTS (
  SELECT 1 FROM model_version_events WHERE model_version_id=$2 AND event_type='created'
)`, randomUUID(), aggregate.Job.Result.ModelVersionID, aggregate.Run.ID, aggregate.Job.UpdatedAt); err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `
UPDATE training_runs SET feature_artifact_id=$2, model_artifact_id=$3, report_artifact_id=$4,
 calibration_artifact_id=$5, threshold_strategy_artifact_id=$6 WHERE id=$1`,
		aggregate.Run.ID, featureArtifactID, modelArtifactID, reportArtifactID, calibrationArtifactID, thresholdArtifactID)
	return err
}

func nestedMap(payload map[string]any, key string) map[string]any {
	value, _ := payload[key].(map[string]any)
	return value
}

func stringValue(source map[string]any, key, fallback string) string {
	value, _ := source[key].(string)
	if value == "" {
		return fallback
	}
	return value
}

func integerValue(value any) any {
	switch typed := value.(type) {
	case int:
		return int64(typed)
	case int64:
		return typed
	case float64:
		return int64(typed)
	case json.Number:
		parsed, err := typed.Int64()
		if err == nil {
			return parsed
		}
	}
	return nil
}

func protocolFingerprint(datasetVersionID string, values map[string]any) string {
	encoded, _ := json.Marshal(values)
	digest := sha256.Sum256(append([]byte(datasetVersionID+":"), encoded...))
	return hex.EncodeToString(digest[:])
}

func nestedConfig(payload map[string]any, key string) []byte {
	value, ok := payload[key]
	if !ok {
		return []byte("{}")
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return []byte("{}")
	}
	return encoded
}

func randomUUID() string {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err != nil {
		panic(err)
	}
	value[6] = (value[6] & 0x0f) | 0x40
	value[8] = (value[8] & 0x3f) | 0x80
	encoded := hex.EncodeToString(value)
	return fmt.Sprintf("%s-%s-%s-%s-%s", encoded[:8], encoded[8:12], encoded[12:16], encoded[16:20], encoded[20:])
}

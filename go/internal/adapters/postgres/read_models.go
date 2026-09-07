package postgres

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/httpapi"
)

type ReadModels struct {
	pool *pgxpool.Pool
}

func NewReadModels(pool *pgxpool.Pool) *ReadModels { return &ReadModels{pool: pool} }

func (models *ReadModels) ListDatasets(ctx context.Context) ([]map[string]any, error) {
	return queryObjects(ctx, models.pool, `
SELECT jsonb_build_object(
  'id', d.dataset_key, 'dataset_id', d.dataset_key, 'name', d.name,
  'description', COALESCE(d.description, ''), 'domain', COALESCE(d.domain, ''), 'status', d.status,
  'dataset_version_id', latest.id, 'latest_version_id', latest.id,
  'sample_count', COALESCE(latest.sample_count, 0), 'class_count', COALESCE(latest.class_count, 0),
  'readiness', COALESCE(latest.readiness_report, '{}'::jsonb),
  'split_counts', COALESCE(latest.split_summary, '{}'::jsonb),
  'created_at', d.created_at, 'updated_at', d.updated_at
)
FROM datasets d
LEFT JOIN LATERAL (
  SELECT id::text, sample_count, class_count, readiness_report, split_summary
  FROM dataset_versions WHERE dataset_id=d.id ORDER BY created_at DESC LIMIT 1
) latest ON true
ORDER BY d.updated_at DESC`)
}

func (models *ReadModels) GetDataset(ctx context.Context, id string) (map[string]any, error) {
	return queryObject(ctx, models.pool, `
SELECT jsonb_build_object(
  'id', d.dataset_key, 'dataset_id', d.dataset_key, 'name', d.name,
  'description', COALESCE(d.description, ''), 'domain', COALESCE(d.domain, ''), 'status', d.status,
  'dataset_version_id', latest.id, 'latest_version_id', latest.id,
  'sample_count', COALESCE(latest.sample_count, 0), 'class_count', COALESCE(latest.class_count, 0),
  'readiness', COALESCE(latest.readiness_report, '{}'::jsonb),
  'split_counts', COALESCE(latest.split_summary, '{}'::jsonb),
  'created_at', d.created_at, 'updated_at', d.updated_at
)
FROM datasets d
LEFT JOIN LATERAL (
  SELECT id::text, sample_count, class_count, readiness_report, split_summary
  FROM dataset_versions WHERE dataset_id=d.id ORDER BY created_at DESC LIMIT 1
) latest ON true
WHERE d.dataset_key=$1 OR d.id::text=$1`, id)
}

func (models *ReadModels) ListJobs(ctx context.Context) ([]map[string]any, error) {
	return queryObjects(ctx, models.pool, `
SELECT jsonb_build_object(
  'id', j.id, 'job_id', j.id, 'job_type', j.job_type, 'status', j.status,
  'payload', j.payload, 'result', j.result, 'error', j.error_message,
  'attempt_count', j.attempt_count, 'max_attempts', j.max_attempts,
  'dispatch_generation', j.dispatch_generation, 'execution_epoch', j.execution_epoch,
  'created_at', j.created_at, 'updated_at', j.updated_at
)
FROM jobs j ORDER BY j.created_at DESC`)
}

func (models *ReadModels) GetJob(ctx context.Context, id string) (map[string]any, error) {
	return queryObject(ctx, models.pool, `
SELECT jsonb_build_object(
  'id', j.id, 'job_id', j.id, 'job_type', j.job_type, 'status', j.status,
  'payload', j.payload, 'result', j.result, 'error', j.error_message,
  'attempt_count', j.attempt_count, 'max_attempts', j.max_attempts,
  'dispatch_generation', j.dispatch_generation, 'execution_epoch', j.execution_epoch,
  'active_attempt_id', j.active_attempt_id, 'last_heartbeat_at', j.last_heartbeat_at,
  'created_at', j.created_at, 'updated_at', j.updated_at
)
FROM jobs j WHERE j.id=$1`, id)
}

func (models *ReadModels) ListTrainingRuns(ctx context.Context) ([]map[string]any, error) {
	return queryObjects(ctx, models.pool, trainingRunSelect+` ORDER BY tr.created_at DESC`)
}

func (models *ReadModels) GetTrainingRun(ctx context.Context, id string) (map[string]any, error) {
	return queryObject(ctx, models.pool, trainingRunSelect+` WHERE tr.id=$1`, id)
}

func (models *ReadModels) JobIDForRun(ctx context.Context, id string) (string, error) {
	var jobID string
	err := models.pool.QueryRow(ctx, `SELECT job_id::text FROM training_runs WHERE id=$1`, id).Scan(&jobID)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", httpapi.ErrReadModelNotFound
	}
	return jobID, err
}

func (models *ReadModels) ResolveDatasetScope(ctx context.Context, datasetID, versionID string) (string, string, string, error) {
	var resolvedDatasetID, resolvedVersionID, resolvedDatasetKey string
	err := models.pool.QueryRow(ctx, `
SELECT d.id::text, dv.id::text, d.dataset_key
FROM datasets d JOIN dataset_versions dv ON dv.dataset_id=d.id
WHERE (NULLIF($1, '') IS NULL OR d.dataset_key=$1 OR d.id::text=$1)
  AND (dv.version_key=$2 OR dv.id::text=$2)
	AND dv.readiness_status='ready'`, datasetID, versionID).Scan(&resolvedDatasetID, &resolvedVersionID, &resolvedDatasetKey)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", "", "", httpapi.ErrReadModelNotFound
	}
	return resolvedDatasetID, resolvedVersionID, resolvedDatasetKey, err
}

const trainingRunSelect = `
SELECT jsonb_build_object(
  'id', tr.id, 'run_id', tr.id, 'job_id', tr.job_id,
  'dataset_id', d.dataset_key, 'dataset_version_id', tr.dataset_version_id,
  'status', tr.status, 'backbone_id', tr.backbone_id,
  'extractor_config', tr.extractor_config, 'head_config', tr.head_config,
  'feature_artifact_id', tr.feature_artifact_id, 'model_artifact_id', tr.model_artifact_id,
  'report_artifact_id', tr.report_artifact_id, 'calibration_artifact_id', tr.calibration_artifact_id,
  'threshold_strategy_artifact_id', tr.threshold_strategy_artifact_id,
  'metrics', tr.metrics, 'error', tr.error_message,
  'created_at', tr.created_at, 'started_at', tr.started_at,
  'finished_at', tr.finished_at, 'updated_at', tr.updated_at
)
FROM training_runs tr JOIN datasets d ON d.id=tr.dataset_id`

type rowQuerier interface {
	Query(context.Context, string, ...any) (pgx.Rows, error)
	QueryRow(context.Context, string, ...any) pgx.Row
}

func queryObjects(ctx context.Context, querier rowQuerier, query string, arguments ...any) ([]map[string]any, error) {
	rows, err := querier.Query(ctx, query, arguments...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]map[string]any, 0)
	for rows.Next() {
		var encoded []byte
		if err := rows.Scan(&encoded); err != nil {
			return nil, err
		}
		item := map[string]any{}
		if err := json.Unmarshal(encoded, &item); err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return items, rows.Err()
}

func queryObject(ctx context.Context, querier rowQuerier, query string, arguments ...any) (map[string]any, error) {
	var encoded []byte
	err := querier.QueryRow(ctx, query, arguments...).Scan(&encoded)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, httpapi.ErrReadModelNotFound
	}
	if err != nil {
		return nil, err
	}
	item := map[string]any{}
	return item, json.Unmarshal(encoded, &item)
}

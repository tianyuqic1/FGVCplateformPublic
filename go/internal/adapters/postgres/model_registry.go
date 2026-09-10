package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
)

type ModelRegistryRepository struct {
	pool *pgxpool.Pool
}

func NewModelRegistryRepository(pool *pgxpool.Pool) *ModelRegistryRepository {
	return &ModelRegistryRepository{pool: pool}
}

func (repository *ModelRegistryRepository) List(ctx context.Context, filter modelregistry.Filter) ([]modelregistry.Version, error) {
	rows, err := repository.pool.Query(ctx, modelVersionSelect+`
WHERE (NULLIF($1, '') IS NULL OR d.dataset_key=$1 OR d.id::text=$1)
  AND (NULLIF($2, '') IS NULL OR dv.version_key=$2 OR mv.dataset_version_id::text=$2)
  AND (NULLIF($3, '') IS NULL OR mv.architecture=$3)
  AND (NULLIF($4, '') IS NULL OR mv.pretraining_method=$4 OR mv.pretraining_dataset=$4)
  AND (NULLIF($5, '') IS NULL OR mv.status=$5)
ORDER BY mv.created_at DESC`, filter.DatasetID, filter.DatasetVersionID, filter.Architecture, filter.Pretraining, string(filter.Status))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	versions := make([]modelregistry.Version, 0)
	for rows.Next() {
		version, err := scanModelVersion(rows)
		if err != nil {
			return nil, err
		}
		if err := repository.loadVersionRelations(ctx, &version); err != nil {
			return nil, err
		}
		versions = append(versions, version)
	}
	return versions, rows.Err()
}

func (repository *ModelRegistryRepository) Get(ctx context.Context, id string) (modelregistry.Version, error) {
	row := repository.pool.QueryRow(ctx, modelVersionSelect+`
WHERE mv.id::text=$1 OR mv.model_key=$1`, id)
	version, err := scanModelVersion(row)
	if errors.Is(err, pgx.ErrNoRows) {
		return modelregistry.Version{}, modelregistry.ErrNotFound
	}
	if err != nil {
		return modelregistry.Version{}, err
	}
	if err := repository.loadVersionRelations(ctx, &version); err != nil {
		return modelregistry.Version{}, err
	}
	return version, nil
}

func (repository *ModelRegistryRepository) Transition(
	ctx context.Context,
	id string,
	from modelregistry.Status,
	target modelregistry.Status,
	actor string,
	reason string,
) (modelregistry.Version, error) {
	err := pgx.BeginFunc(ctx, repository.pool, func(tx pgx.Tx) error {
		var current string
		if err := tx.QueryRow(ctx, `SELECT status FROM model_versions WHERE id::text=$1 OR model_key=$1 FOR UPDATE`, id).Scan(&current); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return modelregistry.ErrNotFound
			}
			return err
		}
		if current != string(from) {
			return modelregistry.ErrConflict
		}
		var versionID string
		if err := tx.QueryRow(ctx, `
UPDATE model_versions SET status=$2, updated_at=now()
WHERE (id::text=$1 OR model_key=$1) AND status=$3
RETURNING id::text`, id, string(target), string(from)).Scan(&versionID); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return modelregistry.ErrConflict
			}
			return err
		}
		_, err := tx.Exec(ctx, `
INSERT INTO model_version_events (
 id, model_version_id, event_type, from_status, to_status, actor, reason, payload, created_at
) VALUES ($1,$2,'status_changed',$3,$4,$5,$6,'{}'::jsonb,now())`,
			uuid.New(), versionID, string(from), string(target), actor, reason)
		return err
	})
	if err != nil {
		return modelregistry.Version{}, err
	}
	return repository.Get(ctx, id)
}

func (repository *ModelRegistryRepository) SetAlias(
	ctx context.Context,
	datasetRef string,
	alias string,
	versionID string,
	actor string,
	reason string,
) (modelregistry.AliasResult, error) {
	var resolvedDatasetID, resolvedDatasetKey string
	err := pgx.BeginFunc(ctx, repository.pool, func(tx pgx.Tx) error {
		if err := tx.QueryRow(ctx, `SELECT id::text, dataset_key FROM datasets WHERE id::text=$1 OR dataset_key=$1 FOR UPDATE`, datasetRef).Scan(&resolvedDatasetID, &resolvedDatasetKey); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return modelregistry.ErrNotFound
			}
			return err
		}
		var actualVersionID, versionDatasetID, status string
		if err := tx.QueryRow(ctx, `SELECT id::text, dataset_id::text, status FROM model_versions WHERE id::text=$1 OR model_key=$1 FOR UPDATE`, versionID).Scan(&actualVersionID, &versionDatasetID, &status); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return modelregistry.ErrNotFound
			}
			return err
		}
		if versionDatasetID != resolvedDatasetID {
			return fmt.Errorf("%w: Model Version belongs to a different Dataset", modelregistry.ErrConflict)
		}
		if alias == "champion" && status != string(modelregistry.StatusProduction) {
			return fmt.Errorf("%w: champion must reference production", modelregistry.ErrConflict)
		}
		if alias == "challenger" && status != string(modelregistry.StatusCandidate) && status != string(modelregistry.StatusStaging) {
			return fmt.Errorf("%w: challenger must reference candidate or staging", modelregistry.ErrConflict)
		}
		now := time.Now().UTC()
		_, err := tx.Exec(ctx, `
INSERT INTO model_version_aliases (id,dataset_id,alias,model_version_id,updated_by,reason,created_at,updated_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$7)
ON CONFLICT (dataset_id,alias) DO UPDATE
SET model_version_id=excluded.model_version_id, updated_by=excluded.updated_by,
    reason=excluded.reason, updated_at=excluded.updated_at`,
			uuid.New(), resolvedDatasetID, alias, actualVersionID, actor, reason, now)
		if err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `
INSERT INTO model_version_events (id,model_version_id,event_type,alias,actor,reason,payload,created_at)
VALUES ($1,$2,'alias_set',$3,$4,$5,jsonb_build_object('dataset_id',$6::text),$7)`,
			uuid.New(), actualVersionID, alias, actor, reason, resolvedDatasetID, now)
		return err
	})
	if err != nil {
		return modelregistry.AliasResult{}, err
	}
	version, err := repository.Get(ctx, versionID)
	if err != nil {
		return modelregistry.AliasResult{}, err
	}
	return modelregistry.AliasResult{
		DatasetID: resolvedDatasetKey, Alias: alias, ModelVersion: version,
		UpdatedBy: actor, Reason: reason, UpdatedAt: version.UpdatedAt,
	}, nil
}

const modelVersionSelect = `
SELECT mv.id::text, mv.model_key, COALESCE(NULLIF(j.payload->>'name',''),mv.name,mv.model_key), COALESCE(mv.description,''),
       d.dataset_key, d.name, mv.dataset_version_id::text, dv.version_key,
       mv.training_run_id::text, mv.status, COALESCE(mv.backbone_key,tr.backbone_id),
       COALESCE(mv.architecture,''), COALESCE(mv.pretraining_method,''),
       COALESCE(mv.pretraining_dataset,''), mv.input_size, mv.feature_dim,
       mv.parameter_count, COALESCE(mv.pooling,''), COALESCE(mv.head_type,''),
       mv.metrics, mv.evaluation_context, mv.created_at, mv.updated_at,
       COALESCE(mv.release_version,''), COALESCE(mv.release_sequence,0),
       COALESCE(mv.release_reason,''), COALESCE(mv.release_signature,''),
       COALESCE(dv.version_number,1), COALESCE(tr.extractor_config,'{}'::jsonb) || COALESCE(tr.head_config,'{}'::jsonb)
FROM model_versions mv
JOIN datasets d ON d.id=mv.dataset_id
JOIN dataset_versions dv ON dv.id=mv.dataset_version_id
JOIN training_runs tr ON tr.id=mv.training_run_id
LEFT JOIN jobs j ON j.id=tr.job_id
`

type rowScanner interface {
	Scan(...any) error
}

func scanModelVersion(row rowScanner) (modelregistry.Version, error) {
	var version modelregistry.Version
	var status string
	var metricsJSON, contextJSON, configJSON []byte
	err := row.Scan(
		&version.ID, &version.ModelKey, &version.Name, &version.Description,
		&version.DatasetID, &version.DatasetName, &version.DatasetVersionID, &version.DatasetVersionKey,
		&version.TrainingRunID, &status, &version.BackboneKey, &version.Architecture,
		&version.PretrainingMethod, &version.PretrainingDataset, &version.InputSize, &version.FeatureDim,
		&version.ParameterCount, &version.Pooling, &version.HeadType, &metricsJSON, &contextJSON,
		&version.CreatedAt, &version.UpdatedAt,
		&version.ReleaseVersion, &version.ReleaseSequence, &version.ReleaseReason, &version.ReleaseSignature,
		&version.DatasetVersionNumber, &configJSON,
	)
	if err != nil {
		return modelregistry.Version{}, err
	}
	version.Status = modelregistry.Status(status)
	_ = json.Unmarshal(metricsJSON, &version.Metrics)
	_ = json.Unmarshal(contextJSON, &version.EvaluationContext)
	_ = json.Unmarshal(configJSON, &version.TrainingConfig)
	return version, nil
}

func (repository *ModelRegistryRepository) loadVersionRelations(ctx context.Context, version *modelregistry.Version) error {
	aliasRows, err := repository.pool.Query(ctx, `SELECT alias FROM model_version_aliases WHERE model_version_id=$1 ORDER BY alias`, version.ID)
	if err != nil {
		return err
	}
	for aliasRows.Next() {
		var alias string
		if err := aliasRows.Scan(&alias); err != nil {
			aliasRows.Close()
			return err
		}
		version.Aliases = append(version.Aliases, alias)
	}
	if err := aliasRows.Err(); err != nil {
		aliasRows.Close()
		return err
	}
	aliasRows.Close()

	artifactRows, err := repository.pool.Query(ctx, `
SELECT id::text, artifact_type, uri, COALESCE(checksum,''), COALESCE(size_bytes,0),
       COALESCE(content_type,''), storage_version, producer,
       COALESCE(dataset_version_id::text,''), COALESCE(training_run_id::text,''),
       COALESCE(attempt_id::text,''), schema_version, created_at,
       COALESCE(verified_at,created_at), artifact_metadata
FROM artifacts WHERE training_run_id=$1 ORDER BY created_at`, version.TrainingRunID)
	if err != nil {
		return err
	}
	for artifactRows.Next() {
		var descriptor artifact.Descriptor
		var metadataJSON []byte
		if err := artifactRows.Scan(&descriptor.ArtifactID, &descriptor.ArtifactType, &descriptor.URI,
			&descriptor.SHA256, &descriptor.SizeBytes, &descriptor.ContentType, &descriptor.StorageVersion,
			&descriptor.Producer, &descriptor.DatasetVersionID, &descriptor.TrainingRunID,
			&descriptor.AttemptID, &descriptor.SchemaVersion, &descriptor.CreatedAt,
			&descriptor.VerifiedAt, &metadataJSON); err != nil {
			artifactRows.Close()
			return err
		}
		_ = json.Unmarshal(metadataJSON, &descriptor.Metadata)
		version.Artifacts = append(version.Artifacts, descriptor)
	}
	if err := artifactRows.Err(); err != nil {
		artifactRows.Close()
		return err
	}
	artifactRows.Close()

	eventRows, err := repository.pool.Query(ctx, `
SELECT id::text,event_type,COALESCE(from_status,''),COALESCE(to_status,''),COALESCE(alias,''),
       actor,COALESCE(reason,''),payload,created_at
FROM model_version_events WHERE model_version_id=$1 ORDER BY created_at`, version.ID)
	if err != nil {
		return err
	}
	defer eventRows.Close()
	for eventRows.Next() {
		var event modelregistry.Event
		var payloadJSON []byte
		if err := eventRows.Scan(&event.ID, &event.EventType, &event.FromStatus, &event.ToStatus,
			&event.Alias, &event.Actor, &event.Reason, &payloadJSON, &event.CreatedAt); err != nil {
			return err
		}
		_ = json.Unmarshal(payloadJSON, &event.Payload)
		version.Events = append(version.Events, event)
	}
	return eventRows.Err()
}

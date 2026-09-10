package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
)

// Atomically register verified output and publish. Expired concurrent exports cannot overwrite archive or another publication.
func (r *ModelRegistryRepository) CompletePublication(ctx context.Context, expected modelregistry.Version, artifacts []artifact.Descriptor, actor, reason string) (modelregistry.Version, error) {
	err := pgx.BeginFunc(ctx, r.pool, func(tx pgx.Tx) error {
		// Lock the dataset before reading its last release. Different models of
		// the same dataset therefore cannot allocate the same semantic version.
		var datasetID string
		if err := tx.QueryRow(ctx, `SELECT d.id::text FROM datasets d JOIN model_versions mv ON mv.dataset_id=d.id WHERE mv.id=$1 FOR UPDATE OF d`, expected.ID).Scan(&datasetID); err != nil {
			return err
		}
		var previous modelregistry.Version
		err := tx.QueryRow(ctx, `SELECT release_version,release_sequence,release_signature,dataset_version_id::text FROM model_versions WHERE dataset_id=$1 AND release_version IS NOT NULL ORDER BY release_sequence DESC LIMIT 1`, datasetID).Scan(&previous.ReleaseVersion, &previous.ReleaseSequence, &previous.ReleaseSignature, &previous.DatasetVersionID)
		var latest *modelregistry.Version
		if err == nil {
			latest = &previous
		} else if !errors.Is(err, pgx.ErrNoRows) {
			return err
		}
		version, upgradeReason, sequence := modelregistry.NextRelease(expected, latest)
		result, err := tx.Exec(ctx, `UPDATE model_versions SET status='production', updated_at=now(), release_version=$4, release_sequence=$5, release_reason=$6, release_signature=$7 WHERE id=$1 AND status=$2 AND updated_at=$3`, expected.ID, string(expected.Status), expected.UpdatedAt, version, sequence, upgradeReason, modelregistry.ModelSignature(expected))
		if err != nil {
			return err
		}
		if result.RowsAffected() != 1 {
			return modelregistry.ErrConflict
		}
		for _, a := range artifacts {
			metadata, err := json.Marshal(a.Metadata)
			if err != nil {
				return err
			}
			_, err = tx.Exec(ctx, `INSERT INTO artifacts (id,artifact_key,artifact_type,dataset_version_id,training_run_id,uri,checksum,size_bytes,content_type,storage_version,producer,schema_version,verified_at,artifact_metadata,created_at)
    VALUES ($1::uuid,$1::text,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,now(),$12,now())`, a.ArtifactID, a.ArtifactType, a.DatasetVersionID, a.TrainingRunID, a.URI, a.SHA256, a.SizeBytes, a.ContentType, a.StorageVersion, a.Producer, a.SchemaVersion, metadata)
			if err != nil {
				return err
			}
		}
		payload, err := json.Marshal(map[string]any{"artifacts": artifacts, "format": "onnx", "model_format": expected.HeadType, "release_version": version, "upgrade_reason": upgradeReason, "precision": expected.ReleasePrecision})
		if err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `INSERT INTO model_version_events(id,model_version_id,event_type,from_status,to_status,actor,reason,payload,created_at) VALUES($1,$2,'published',$3,'production',$4,$5,$6,now())`, uuid.New(), expected.ID, string(expected.Status), actor, reason, payload)
		return err
	})
	if err != nil {
		return modelregistry.Version{}, err
	}
	return r.Get(ctx, expected.ID)
}

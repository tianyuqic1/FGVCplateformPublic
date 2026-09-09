package postgres

import (
	"context"
	"encoding/json"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
)

// Atomically register verified output and publish. Expired concurrent exports cannot overwrite archive or another publication.
func (r *ModelRegistryRepository) CompletePublication(ctx context.Context, expected modelregistry.Version, artifacts []artifact.Descriptor, actor, reason string) (modelregistry.Version, error) {
	err := pgx.BeginFunc(ctx, r.pool, func(tx pgx.Tx) error {
		result, err := tx.Exec(ctx, `UPDATE model_versions SET status='production', updated_at=now() WHERE id=$1 AND status=$2 AND updated_at=$3`, expected.ID, string(expected.Status), expected.UpdatedAt)
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
		payload, err := json.Marshal(map[string]any{"artifacts": artifacts, "format": "onnx", "scope": "classification_head"})
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

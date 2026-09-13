package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/jackc/pgx/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
)

func (r DatasetRepository) PreviewSnapshot(ctx context.Context, id string) (dataset.Snapshot, error) {
	var result dataset.Snapshot
	var metadata []byte
	err := r.Pool.QueryRow(ctx, `SELECT d.id::text,d.dataset_key,d.name,v.id::text,v.version_number,a.artifact_metadata
 FROM dataset_versions v JOIN datasets d ON d.id=v.dataset_id JOIN artifacts a ON a.id=v.manifest_artifact_id
 WHERE v.id::text=$1 OR v.version_key=$1`, id).Scan(&result.DatasetID, &result.DatasetKey, &result.Name, &result.VersionID, &result.Number, &metadata)
	if errors.Is(err, pgx.ErrNoRows) {
		return result, dataset.ErrNotFound
	}
	if err != nil {
		return result, err
	}
	var stored struct {
		Manifest map[string]any      `json:"manifest"`
		Archive  artifact.Descriptor `json:"archive"`
	}
	err = json.Unmarshal(metadata, &stored)
	result.Manifest, result.Archive = stored.Manifest, stored.Archive
	return result, err
}

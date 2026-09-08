package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
)

type DatasetRepository struct{ Pool *pgxpool.Pool }

func (r DatasetRepository) Save(ctx context.Context, key, versionKey, versionID string, archive, manifest artifact.Descriptor, data map[string]any) error {
	readiness, _ := data["readiness"].(map[string]any)
	state := "needs_attention"
	datasetState := "draft"
	if readiness["ready"] == true {
		state = "ready"
		datasetState = "ready"
	}
	report, _ := json.Marshal(readiness)
	splits, _ := json.Marshal(data["split_counts"])
	metadata, _ := json.Marshal(map[string]any{"manifest": data, "archive": archive})
	err := pgx.BeginFunc(ctx, r.Pool, func(tx pgx.Tx) error {
		var datasetID string
		err := tx.QueryRow(ctx, `INSERT INTO datasets(id,dataset_key,name,status,created_at,updated_at) VALUES($1,$2,$2,$3,now(),now()) ON CONFLICT(dataset_key) DO UPDATE SET updated_at=now() RETURNING id::text`, uuid.NewString(), key, datasetState).Scan(&datasetID)
		if err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `INSERT INTO dataset_versions(id,dataset_id,version_key,root_uri,sample_count,class_count,split_summary,readiness_status,readiness_report,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,now())`, versionID, datasetID, versionKey, archive.URI, int(readiness["sample_count"].(float64)), int(readiness["class_count"].(float64)), splits, state, report)
		if err != nil {
			return err
		}
		for _, a := range []artifact.Descriptor{archive, manifest} {
			_, err = tx.Exec(ctx, `INSERT INTO artifacts(id,artifact_key,artifact_type,dataset_id,dataset_version_id,uri,checksum,size_bytes,content_type,storage_version,producer,schema_version,verified_at,artifact_metadata,created_at) VALUES($1::uuid,$1::text,$2,$3,$4,$5,$6,$7,$8,'s3-v1',$9,1,now(),$10,now())`, a.ArtifactID, a.ArtifactType, datasetID, versionID, a.URI, a.SHA256, a.SizeBytes, a.ContentType, a.Producer, metadata)
			if err != nil {
				return err
			}
		}
		_, err = tx.Exec(ctx, `UPDATE dataset_versions SET manifest_artifact_id=$2 WHERE id=$1`, versionID, manifest.ArtifactID)
		return err
	})
	var pgerr *pgconn.PgError
	if errors.As(err, &pgerr) && pgerr.Code == "23505" {
		return dataset.ErrConflict
	}
	return err
}

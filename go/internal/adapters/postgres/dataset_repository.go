package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
)

type DatasetRepository struct{ Pool *pgxpool.Pool }

func (r DatasetRepository) FindPublication(ctx context.Context, id, fingerprint string) (map[string]any, error) {
	var p dataset.Publication
	var stored string
	var manifest, changes []byte
	err := r.Pool.QueryRow(ctx, `SELECT d.dataset_key,d.name,v.id::text,coalesce(v.parent_version_id::text,''),v.version_number,v.source_type,v.import_fingerprint,a.artifact_metadata->'manifest',v.change_summary FROM dataset_versions v JOIN datasets d ON d.id=v.dataset_id JOIN artifacts a ON a.id=v.manifest_artifact_id WHERE v.import_request_id=$1`, id).Scan(&p.DatasetKey, &p.Name, &p.VersionID, &p.ParentID, &p.Number, &p.SourceType, &stored, &manifest, &changes)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if stored != fingerprint {
		return nil, dataset.ErrConflict
	}
	if err = json.Unmarshal(manifest, &p.Manifest); err != nil {
		return nil, err
	}
	if err = json.Unmarshal(changes, &p.Changes); err != nil {
		return nil, err
	}
	return dataset.Result(p), nil
}
func (r DatasetRepository) Base(ctx context.Context, datasetID, versionID string) (dataset.Snapshot, error) {
	var b dataset.Snapshot
	var metadata []byte
	err := r.Pool.QueryRow(ctx, `SELECT d.id::text,d.dataset_key,d.name,v.id::text,v.version_number,a.artifact_metadata FROM datasets d JOIN dataset_versions v ON v.dataset_id=d.id JOIN artifacts a ON a.id=v.manifest_artifact_id WHERE (d.id::text=$1 OR d.dataset_key=$1) AND (v.id::text=$2 OR v.version_key=$2)`, datasetID, versionID).Scan(&b.DatasetID, &b.DatasetKey, &b.Name, &b.VersionID, &b.Number, &metadata)
	if errors.Is(err, pgx.ErrNoRows) {
		return b, dataset.ErrNotFound
	}
	if err != nil {
		return b, err
	}
	var data struct {
		Manifest map[string]any      `json:"manifest"`
		Archive  artifact.Descriptor `json:"archive"`
	}
	err = json.Unmarshal(metadata, &data)
	b.Manifest = data.Manifest
	b.Archive = data.Archive
	if err == nil && b.Archive.URI == "" {
		return b, dataset.ErrInvalid
	}
	return b, err
}
func (r DatasetRepository) Candidates(ctx context.Context, id string) ([]dataset.Feedback, error) {
	rows, err := r.Pool.Query(ctx, `SELECT f.id::text,f.final_label,coalesce(ri.input_ref,''),f.dataset_version_id::text,f.model_version_id::text,ri.review_key,coalesce(ir.run_key,''),a.descriptor
 FROM feedback_items f JOIN datasets d ON d.id=f.dataset_id JOIN review_items ri ON ri.id=f.review_item_id LEFT JOIN inference_runs ir ON ir.id=f.inference_run_id
 LEFT JOIN LATERAL (SELECT jsonb_build_object('artifact_id',id,'artifact_type',artifact_type,'uri',uri,'sha256',checksum,'size_bytes',size_bytes,'content_type',content_type,'storage_version',storage_version,'schema_version',schema_version) descriptor FROM artifacts WHERE uri=ri.input_ref AND verified_at IS NOT NULL LIMIT 1) a ON true
 WHERE (d.id::text=$1 OR d.dataset_key=$1) AND f.destination='training_candidate' AND f.final_outcome IN ('confirmed_label','corrected_label') AND f.final_label IS NOT NULL AND ri.status='feedbacked'
 AND NOT EXISTS (SELECT 1 FROM dataset_version_feedback used WHERE used.dataset_id=f.dataset_id AND used.feedback_item_id=f.id)
 ORDER BY f.created_at,f.id LIMIT 10000`, id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := make([]dataset.Feedback, 0)
	for rows.Next() {
		var f dataset.Feedback
		var descriptor []byte
		if err = rows.Scan(&f.ID, &f.Label, &f.InputRef, &f.SourceVersionID, &f.ModelVersionID, &f.ReviewID, &f.InferenceRunID, &descriptor); err != nil {
			return nil, err
		}
		if len(descriptor) > 0 {
			if err = json.Unmarshal(descriptor, &f.ImageArtifact); err != nil {
				return nil, err
			}
		}
		result = append(result, f)
	}
	return result, rows.Err()
}
func (r DatasetRepository) Save(ctx context.Context, p dataset.Publication) (map[string]any, error) {
	readiness, _ := p.Manifest["readiness"].(map[string]any)
	state, datasetState := "needs_attention", "draft"
	if readiness["ready"] == true {
		state, datasetState = "ready", "ready"
	}
	report, _ := json.Marshal(readiness)
	splits, _ := json.Marshal(p.Manifest["split_counts"])
	metadata, _ := json.Marshal(map[string]any{"manifest": p.Manifest, "archive": p.Archive})
	changes, _ := json.Marshal(p.Changes)
	err := pgx.BeginFunc(ctx, r.Pool, func(tx pgx.Tx) error {
		if p.ParentID == "" {
			if _, err := tx.Exec(ctx, `INSERT INTO datasets(id,dataset_key,name,status,created_at,updated_at) VALUES($1,$2,$3,$4,now(),now())`, p.DatasetID, p.DatasetKey, p.Name, datasetState); err != nil {
				return err
			}
		} else {
			var locked string
			if err := tx.QueryRow(ctx, `SELECT id::text FROM datasets WHERE id=$1 FOR UPDATE`, p.DatasetID).Scan(&locked); err != nil {
				return err
			}
			var latest string
			var number int
			if err := tx.QueryRow(ctx, `SELECT id::text,version_number FROM dataset_versions WHERE dataset_id=$1 ORDER BY version_number DESC LIMIT 1`, p.DatasetID).Scan(&latest, &number); err != nil {
				return err
			}
			if latest != p.ParentID || number+1 != p.Number {
				return dataset.ErrConflict
			}
		}
		_, err := tx.Exec(ctx, `INSERT INTO dataset_versions(id,dataset_id,version_key,version_number,parent_version_id,source_type,change_summary,import_request_id,import_fingerprint,root_uri,sample_count,class_count,split_summary,readiness_status,readiness_report,created_at) VALUES($1::uuid,$2,$1::text,$3,NULLIF($4,'')::uuid,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,now())`, p.VersionID, p.DatasetID, p.Number, p.ParentID, p.SourceType, changes, p.RequestID, p.Fingerprint, p.Archive.URI, int(readiness["sample_count"].(float64)), int(readiness["class_count"].(float64)), splits, state, report)
		if err != nil {
			return err
		}
		for _, a := range []artifact.Descriptor{p.Archive, p.ManifestArtifact} {
			_, err = tx.Exec(ctx, `INSERT INTO artifacts(id,artifact_key,artifact_type,dataset_id,dataset_version_id,uri,checksum,size_bytes,content_type,storage_version,producer,schema_version,verified_at,artifact_metadata,created_at) VALUES($1::uuid,$1::text,$2,$3,$4,$5,$6,$7,$8,$9,$10,1,now(),$11,now())`, a.ArtifactID, a.ArtifactType, p.DatasetID, p.VersionID, a.URI, a.SHA256, a.SizeBytes, a.ContentType, a.StorageVersion, a.Producer, metadata)
			if err != nil {
				return err
			}
		}
		for _, f := range p.Feedback {
			// Recheck eligibility under row locks at commit, not just at preview time.
			var id string
			err = tx.QueryRow(ctx, `SELECT f.id::text FROM feedback_items f JOIN review_items ri ON ri.id=f.review_item_id WHERE f.id=$1 AND f.dataset_id=$2 AND f.final_label=$3 AND f.destination='training_candidate' AND f.final_outcome IN ('confirmed_label','corrected_label') AND ri.status='feedbacked' FOR UPDATE OF f,ri`, f.ID, p.DatasetID, f.Label).Scan(&id)
			if errors.Is(err, pgx.ErrNoRows) {
				return dataset.ErrConflict
			}
			if err != nil {
				return err
			}
			if _, err = tx.Exec(ctx, `INSERT INTO dataset_version_feedback(dataset_id,feedback_item_id,dataset_version_id) VALUES($1,$2,$3)`, p.DatasetID, f.ID, p.VersionID); err != nil {
				return err
			}
		}
		if _, err = tx.Exec(ctx, `UPDATE dataset_versions SET manifest_artifact_id=$2 WHERE id=$1`, p.VersionID, p.ManifestArtifact.ArtifactID); err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `UPDATE datasets SET status=$2,updated_at=now() WHERE id=$1`, p.DatasetID, datasetState)
		return err
	})
	if err != nil {
		// A concurrent retry may have committed the identical request while we scanned.
		if result, findErr := r.FindPublication(ctx, p.RequestID, p.Fingerprint); findErr == nil && result != nil {
			return result, nil
		}
		var pgerr *pgconn.PgError
		if errors.As(err, &pgerr) && pgerr.Code == "23505" {
			return nil, dataset.ErrConflict
		}
		return nil, err
	}
	return dataset.Result(p), nil
}

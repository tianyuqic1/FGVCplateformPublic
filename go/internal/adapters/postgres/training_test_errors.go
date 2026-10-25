package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/testerrors"
	"io"
	"os"
)

type TrainingTestErrors struct {
	Pool  *pgxpool.Pool
	Store artifact.Store
	slots chan struct{}
}

func NewTrainingTestErrors(pool *pgxpool.Pool, store artifact.Store) *TrainingTestErrors {
	return &TrainingTestErrors{Pool: pool, Store: store, slots: make(chan struct{}, 2)}
}
func (a *TrainingTestErrors) Analyze(ctx context.Context, id string, maxPairs, maxExamples int) (testerrors.Report, error) {
	out := testerrors.Report{RunID: id, Availability: "unavailable", Findings: []testerrors.Finding{}, Limitations: []string{}}
	parsed, e := uuid.Parse(id)
	if e != nil {
		return out, testerrors.ErrNotFound
	}
	id = parsed.String()
	select {
	case a.slots <- struct{}{}:
		defer func() { <-a.slots }()
	case <-ctx.Done():
		return out, ctx.Err()
	}
	var runKey, versionID, versionKey, status string
	var descriptor artifact.Descriptor
	var verified bool
	var trainRaw []byte
	e = a.Pool.QueryRow(ctx, `SELECT r.run_key,v.id::text,v.version_key,r.status,
 COALESCE(a.id::text,''),COALESCE(a.uri,''),COALESCE(a.checksum,''),COALESCE(a.size_bytes,0),a.verified_at IS NOT NULL,
 COALESCE(v.split_summary->'train','{}'::jsonb)
 FROM training_runs r JOIN dataset_versions v ON v.id=r.dataset_version_id
 LEFT JOIN artifacts a ON a.id=r.report_artifact_id WHERE r.id=$1`, id).Scan(&runKey, &versionID, &versionKey, &status, &descriptor.ArtifactID, &descriptor.URI, &descriptor.SHA256, &descriptor.SizeBytes, &verified, &trainRaw)
	if errors.Is(e, pgx.ErrNoRows) {
		return out, testerrors.ErrNotFound
	}
	if e != nil {
		return out, e
	}
	out.DatasetVersionID = versionID
	if status != "succeeded" {
		out.Limitations = append(out.Limitations, "训练尚未成功完成，没有可分析的最终测试报告。")
		return out, nil
	}
	if descriptor.ArtifactID == "" || !verified {
		out.Limitations = append(out.Limitations, "没有可校验的训练报告制品，不能从总体准确率反推出具体错分原因。")
		return out, nil
	}
	dir, e := os.MkdirTemp("", "finevision-test-errors-")
	if e != nil {
		return out, e
	}
	defer os.RemoveAll(dir)
	raw, e := a.read(ctx, descriptor, dir, 16<<20)
	if e != nil {
		return out, fmt.Errorf("test report could not be verified or read")
	}
	// Historical matrices need an authoritative class order; use the same run's
	// verified model bundle, never JSON map iteration or guessed label sorting.
	var doc testerrors.Document
	if json.Unmarshal(raw, &doc) != nil {
		return out, fmt.Errorf("invalid training report")
	}
	classes := []string{}
	if doc.Evaluation.Analysis == nil && (doc.Evaluation.Config.Split == "test" || doc.Config.Split == "test") {
		var bundle artifact.Descriptor
		be := a.Pool.QueryRow(ctx, `SELECT id::text,uri,checksum,size_bytes FROM artifacts WHERE training_run_id=$1 AND artifact_type='model_bundle' AND verified_at IS NOT NULL ORDER BY created_at DESC LIMIT 1`, id).Scan(&bundle.ArtifactID, &bundle.URI, &bundle.SHA256, &bundle.SizeBytes)
		if be == nil {
			if data, be := a.read(ctx, bundle, dir, 1<<20); be == nil {
				var manifest struct {
					Model struct {
						Classes []string `json:"classes"`
					} `json:"model_artifact"`
				}
				if json.Unmarshal(data, &manifest) == nil {
					classes = manifest.Model.Classes
				}
			}
		}
	}
	trainCounts := map[string]int{}
	_ = json.Unmarshal(trainRaw, &trainCounts)
	out, e = testerrors.Analyze(raw, id, runKey, versionID, versionKey, classes, trainCounts, maxPairs, maxExamples)
	return out, e
}
func (a *TrainingTestErrors) read(ctx context.Context, d artifact.Descriptor, dir string, maxSize int64) ([]byte, error) {
	if a.Store == nil || d.SizeBytes < 1 || d.SizeBytes > maxSize {
		return nil, fmt.Errorf("unsupported report size")
	}
	path, e := a.Store.MaterializeVerified(ctx, d, dir)
	if e != nil {
		return nil, e
	}
	file, e := os.Open(path)
	if e != nil {
		return nil, e
	}
	defer file.Close()
	raw, e := io.ReadAll(io.LimitReader(file, maxSize+1))
	if e != nil || int64(len(raw)) > maxSize {
		return nil, fmt.Errorf("report size exceeded")
	}
	return raw, nil
}

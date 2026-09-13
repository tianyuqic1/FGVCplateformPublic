package postgres_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	postgres "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/postgres"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/deployment"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/review"
	"os"
	"strings"
	"testing"
	"time"
)

type verifiedBuildStore struct {
	artifact.Store
	corrupt bool
}

func (s *verifiedBuildStore) Verify(ctx context.Context, d artifact.Descriptor) error {
	bytes := "test compiled bytes"
	if s.corrupt {
		bytes = "corruption"
	}
	return artifact.VerifyReader(ctx, strings.NewReader(bytes), d.SHA256, d.SizeBytes)
}
func TestDeploymentDatabaseLifecycle(t *testing.T) {
	url := os.Getenv("FINEVISION_DEPLOYMENT_TEST_DATABASE_URL")
	if url == "" {
		t.Skip("requires disposable deployment_test_ database with published full ONNX")
	}
	cfg, e := pgxpool.ParseConfig(url)
	if e != nil {
		t.Fatal(e)
	}
	if !strings.HasPrefix(cfg.ConnConfig.Database, "deployment_test_") {
		t.Fatal("refusing non-test database")
	}
	ctx := context.Background()
	pool, e := pgxpool.NewWithConfig(ctx, cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer pool.Close()
	models, e := postgres.NewModelRegistryRepository(pool).List(ctx, modelregistry.Filter{})
	if e != nil {
		t.Fatal(e)
	}
	modelID := ""
	for _, v := range models {
		_, rows := deployment.PortableVariants(v)
		for _, r := range rows {
			if r.Source.ArtifactType == "full_onnx" && r.Precision == "FP32" {
				modelID = v.ID
				break
			}
		}
		if modelID != "" {
			break
		}
	}
	if modelID == "" {
		t.Fatal("test fixture needs a published full FP32 ONNX")
	}
	store := &verifiedBuildStore{}
	profile := "test-" + uuid.NewString()
	repo := &postgres.DeploymentRepository{Pool: pool, Store: store, Targets: []deployment.Target{{Runtime: "tensorrt", Profile: profile, Address: "test:9100"}}}
	input := deployment.Create{Runtime: "tensorrt", Precision: "FP16", MaxBatch: 1, Actor: "test"}
	row, e := repo.Create(ctx, modelID, input)
	if e != nil {
		t.Fatal(e)
	}
	duplicate, e := repo.Create(ctx, modelID, input)
	if e != nil || duplicate.ID != row.ID {
		t.Fatal("create not idempotent", e)
	}
	var token string
	var count int
	if e = pool.QueryRow(ctx, `SELECT build_token::text FROM model_deployments WHERE id=$1`, row.ID).Scan(&token); e != nil {
		t.Fatal(e)
	}
	pool.QueryRow(ctx, `SELECT count(*) FROM deployment_outbox WHERE deployment_id=$1`, row.ID).Scan(&count)
	if count != 1 {
		t.Fatal("duplicate outbox", count)
	}
	if _, e = repo.Claim(ctx, row.ID, token, "worker", "ascend_acl", profile); !errors.Is(e, review.ErrConflict) {
		t.Fatal("wrong runtime", e)
	}
	if _, e = repo.Claim(ctx, row.ID, token, "worker", "tensorrt", profile); e != nil {
		t.Fatal(e)
	}
	if _, e = repo.Claim(ctx, row.ID, token, "other", "tensorrt", profile); !errors.Is(e, review.ErrConflict) {
		t.Fatal("double claim", e)
	}
	if e = repo.Heartbeat(ctx, row.ID, token, "worker"); e != nil {
		t.Fatal(e)
	}
	if e = repo.Complete(ctx, row.ID, token, "worker", nil, nil, "first build failed"); e != nil {
		t.Fatal(e)
	}
	if _, e = repo.Retry(ctx, row.ID); e != nil {
		t.Fatal(e)
	}
	if e = repo.Complete(ctx, row.ID, token, "worker", nil, nil, "stale callback"); !errors.Is(e, review.ErrConflict) {
		t.Fatal("stale token", e)
	}
	pool.QueryRow(ctx, `SELECT build_token::text FROM model_deployments WHERE id=$1`, row.ID).Scan(&token)
	if _, e = repo.Claim(ctx, row.ID, token, "worker", "tensorrt", profile); e != nil {
		t.Fatal(e)
	}
	sha := sha256.Sum256([]byte("test compiled bytes"))
	built := artifact.Descriptor{ArtifactID: uuid.NewString(), ArtifactType: "tensorrt_engine", URI: "s3://test/engine", SHA256: hex.EncodeToString(sha[:]), SizeBytes: int64(len("test compiled bytes")), ContentType: "application/octet-stream", StorageVersion: "s3-v1", Producer: "test", DatasetVersionID: row.Source.DatasetVersionID, TrainingRunID: row.Source.TrainingRunID, CreatedAt: time.Now(), VerifiedAt: time.Now(), Metadata: map[string]any{"deployment_id": row.ID, "source_onnx_sha256": row.Source.SHA256, "precision": "FP16", "target_profile": profile, "runtime": "tensorrt", "parity_passed": true, "runtime_fingerprint": map[string]any{"test": true}, "max_batch": 1}}
	report := map[string]any{"parity_passed": true}
	store.corrupt = true
	if e = repo.Complete(ctx, row.ID, token, "worker", &built, report, ""); !errors.Is(e, artifact.ErrIntegrity) {
		t.Fatal("corruption accepted", e)
	}
	store.corrupt = false
	pool.Exec(ctx, `UPDATE model_deployments SET lease_expires_at=now()-interval '1 second' WHERE id=$1`, row.ID)
	if e = repo.Complete(ctx, row.ID, token, "worker", &built, report, ""); !errors.Is(e, review.ErrConflict) {
		t.Fatal("expired lease accepted", e)
	}
	pool.Exec(ctx, `UPDATE model_deployments SET lease_expires_at=now()+interval '2 minutes' WHERE id=$1`, row.ID)
	if e = repo.Complete(ctx, row.ID, token, "worker", &built, report, ""); e != nil {
		t.Fatal(e)
	}
	if e = repo.Complete(ctx, row.ID, token, "worker", &built, report, ""); e != nil {
		t.Fatal("repeat completion", e)
	}
	changed := built
	changed.ArtifactID = uuid.NewString()
	if e = repo.Complete(ctx, row.ID, token, "worker", &changed, report, ""); !errors.Is(e, review.ErrConflict) {
		t.Fatal("different completion payload accepted", e)
	}
	if _, e = repo.Retry(ctx, row.ID); !errors.Is(e, review.ErrConflict) {
		t.Fatal("ready retry allowed", e)
	}
	rows, e := repo.List(ctx, modelID)
	if e != nil {
		t.Fatal(e)
	}
	found := false
	for _, r := range rows {
		if r.ID == row.ID {
			found = r.Status == "ready" && r.Compiled.ArtifactID == built.ArtifactID
		}
	}
	if !found {
		t.Fatal("ready deployment missing")
	}
}

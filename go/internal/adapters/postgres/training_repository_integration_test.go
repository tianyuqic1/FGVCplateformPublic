package postgres_test

import (
	"context"
	"crypto/sha256"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	postgresadapter "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/postgres"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
)

func TestTrainingLifecyclePersistsAtomicOutboxAndCompletion(t *testing.T) {
	databaseURL := os.Getenv("FINEVISION_TEST_GO_DATABASE_URL")
	if databaseURL == "" {
		t.Skip("set FINEVISION_TEST_GO_DATABASE_URL to run PostgreSQL integration tests")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	datasetID, versionID := uuid.New(), uuid.New()
	now := time.Now().UTC()
	_, err = pool.Exec(ctx, `INSERT INTO datasets (id,dataset_key,name,status,created_at,updated_at) VALUES ($1,$2,'integration','ready',$3,$3)`, datasetID, "integration-"+datasetID.String(), now)
	if err != nil {
		t.Fatal(err)
	}
	_, err = pool.Exec(ctx, `INSERT INTO dataset_versions (id,dataset_id,version_key,root_uri,sample_count,class_count,split_summary,readiness_status,readiness_report,created_at) VALUES ($1,$2,$3,'s3://finevision-datasets/integration',12,3,'{}','ready','{"ready":true}',$4)`, versionID, datasetID, "version-"+versionID.String(), now)
	if err != nil {
		t.Fatal(err)
	}

	service := training.NewService(postgresadapter.NewTrainingRepository(pool), time.Now, 2*time.Minute)
	created, err := service.Create(ctx, training.CreateCommand{
		DatasetID: datasetID.String(), DatasetVersionID: versionID.String(), BackboneID: "dinov3_vits16",
		Payload:     map[string]any{"extractor": "dinov3_vits", "head_config": map[string]any{"head_type": "ridge_linear"}},
		MaxAttempts: 2,
	})
	if err != nil {
		t.Fatal(err)
	}
	var outboxCount int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM outbox_events WHERE aggregate_id=$1`, created.JobID).Scan(&outboxCount); err != nil || outboxCount != 1 {
		t.Fatalf("outbox count/error = %d/%v", outboxCount, err)
	}
	claim, err := service.Claim(ctx, training.ClaimCommand{JobID: created.JobID, DispatchGeneration: 1, WorkerID: "integration-worker"})
	if err != nil {
		t.Fatal(err)
	}
	digest := fmt.Sprintf("%x", sha256.Sum256([]byte("model")))
	modelArtifactID := uuid.NewString()
	completed, err := service.Complete(ctx, training.CompleteCommand{
		JobID: created.JobID, AttemptID: claim.AttemptID, ExecutionEpoch: claim.ExecutionEpoch,
		CompletionKey: "integration-complete", ResultDigest: digest,
		Artifacts: []artifact.Descriptor{{
			ArtifactID: modelArtifactID, ArtifactType: "model", URI: "s3://finevision-artifacts/models/" + digest,
			SHA256: digest, SizeBytes: 5, ContentType: "application/octet-stream", StorageVersion: "s3-v1",
			Producer: "integration-test", DatasetVersionID: versionID.String(), TrainingRunID: created.TrainingRunID,
			AttemptID: claim.AttemptID, SchemaVersion: 1, CreatedAt: now, VerifiedAt: now,
		}}, Metrics: map[string]any{"accuracy": 1.0},
	})
	if err != nil {
		t.Fatal(err)
	}
	var modelCount int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM model_versions WHERE id=$1 AND model_artifact_id=$2`, completed.ModelVersionID, modelArtifactID).Scan(&modelCount); err != nil || modelCount != 1 {
		t.Fatalf("model count/error = %d/%v", modelCount, err)
	}
}

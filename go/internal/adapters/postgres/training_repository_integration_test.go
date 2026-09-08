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
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/httpapi"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
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
		DatasetID: datasetID.String(), DatasetVersionID: versionID.String(), BackboneID: "dinov3_vits16_lvd1689m",
		Payload: map[string]any{
			"name":      "几何图形 · ViT-S 命名验收",
			"extractor": "dinov3_vits",
			"extractor_config": map[string]any{
				"backbone_key": "dinov3_vits16_lvd1689m", "architecture": "vit_small_patch16",
				"pretraining_method": "DINOv3", "pretraining_dataset": "LVD-1689M",
				"image_size": 224, "feature_dim": 384, "parameter_count": 21588480, "feature_pool": "cls",
			},
			"head_config": map[string]any{"head_type": "torch_linear_adam", "epochs": 2},
		},
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
	if err := service.Progress(ctx, training.ProgressCommand{
		JobID: created.JobID, AttemptID: claim.AttemptID, ExecutionEpoch: claim.ExecutionEpoch,
		Progress: map[string]any{"current_stage": "head"},
		MetricPoints: []training.MetricPoint{
			{Name: "train_loss", Step: 1, Value: 0.8},
			{Name: "eval_accuracy", Step: 1, Value: 0.75},
		},
	}); err != nil {
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
	canDelete, err := artifact.NewGarbageCollectionGuard(postgresadapter.NewArtifactReferences(pool)).CanDeletePhysicalObject(ctx, digest, modelArtifactID)
	if err != nil || canDelete {
		t.Fatalf("referenced model artifact must not be physically deleted: allowed=%t error=%v", canDelete, err)
	}
	runRead, err := postgresadapter.NewReadModels(pool).GetTrainingRun(ctx, created.TrainingRunID)
	if err == nil && runRead["name"] != "几何图形 · ViT-S 命名验收" {
		t.Fatalf("training name was not persisted: %v", runRead["name"])
	}
	if err != nil || runRead["model_version_id"] != completed.ModelVersionID {
		t.Fatalf("training detail lost model link: %#v, %v", runRead, err)
	}
	metricRead, err := postgresadapter.NewReadModels(pool).GetTrainingRunMetrics(ctx, created.TrainingRunID, httpapi.MetricsQuery{})
	if err != nil {
		t.Fatal(err)
	}
	if points := metricRead["metric_points"].([]map[string]any); len(points) != 2 || metricRead["next_cursor"].(int64) <= 0 {
		t.Fatalf("metric read = %#v", metricRead)
	}
	registry := modelregistry.NewService(postgresadapter.NewModelRegistryRepository(pool))
	version, err := registry.Get(ctx, completed.ModelVersionID)
	if err != nil {
		t.Fatal(err)
	}
	if version.BackboneKey != "dinov3_vits16_lvd1689m" || version.FeatureDim == nil || *version.FeatureDim != 384 ||
		version.EvaluationContext["protocol_fingerprint"] == "" || len(version.Events) != 1 {
		t.Fatalf("model version metadata = %#v", version)
	}
	staging, err := registry.Promote(ctx, completed.ModelVersionID, modelregistry.StatusStaging, "integration-test", "quality gate passed")
	if err != nil || staging.Status != modelregistry.StatusStaging {
		t.Fatalf("staging/error = %#v/%v", staging, err)
	}
	production, err := registry.Promote(ctx, completed.ModelVersionID, modelregistry.StatusProduction, "integration-test", "release approved")
	if err != nil || production.Status != modelregistry.StatusProduction {
		t.Fatalf("production/error = %#v/%v", production, err)
	}
	alias, err := registry.SetAlias(ctx, version.DatasetID, "champion", completed.ModelVersionID, "integration-test", "best approved baseline")
	if err != nil || alias.Alias != "champion" {
		t.Fatalf("alias/error = %#v/%v", alias, err)
	}
}

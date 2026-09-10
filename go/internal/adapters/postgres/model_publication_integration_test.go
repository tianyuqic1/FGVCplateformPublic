package postgres_test

import (
	"context"
	"errors"
	"os"
	"strings"
	"sync"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	postgresadapter "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/postgres"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
)

func TestPublicationAllocationSerializesDatasetAndRejectsStaleCompletion(t *testing.T) {
	url := os.Getenv("FINEVISION_TEST_GO_DATABASE_URL")
	if url == "" {
		t.Skip("requires isolated migrated publication_test database")
	}
	config, err := pgxpool.ParseConfig(url)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(config.ConnConfig.Database, "publication_test_") {
		t.Skip("publication test fixtures require publication_test_ database")
	}
	ctx := context.Background()
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	repo := postgresadapter.NewModelRegistryRepository(pool)
	fixtures := make([]modelregistry.Version, 2)
	for i := range fixtures {
		id := uuid.NewString()
		result, err := pool.Exec(ctx, `INSERT INTO model_versions(id,model_key,dataset_id,dataset_version_id,training_run_id,status,model_artifact_id,metrics,head_type,created_at,updated_at)
   SELECT $1,$2,dataset_id,dataset_version_id,training_run_id,'candidate',model_artifact_id,'{}','image_classifier_v2',now(),now() FROM model_versions ORDER BY created_at LIMIT 1`, id, "release-race-"+id)
		if err != nil {
			t.Fatal(err)
		}
		if result.RowsAffected() != 1 {
			t.Skip("requires a model fixture")
		}
		fixtures[i], err = repo.Get(ctx, id)
		if err != nil {
			t.Fatal(err)
		}
	}
	outputs := make([]modelregistry.Version, 2)
	start := make(chan struct{})
	var wg sync.WaitGroup
	for i, v := range fixtures {
		wg.Add(1)
		go func(i int, v modelregistry.Version) {
			defer wg.Done()
			<-start
			a := artifact.Descriptor{ArtifactID: uuid.NewString(), ArtifactType: "full_onnx", URI: "s3://test/concurrency", SHA256: strings.Repeat("a", 64), SizeBytes: 4, ContentType: "application/octet-stream", Producer: "test", SchemaVersion: 1, DatasetVersionID: v.DatasetVersionID, TrainingRunID: v.TrainingRunID, Metadata: map[string]any{"model_version_id": v.ID, "precision": "FP32"}}
			result, err := repo.CompletePublication(ctx, v, []artifact.Descriptor{a}, "test", "concurrent release")
			if err != nil {
				t.Error(err)
				return
			}
			outputs[i] = result
		}(i, v)
	}
	close(start)
	wg.Wait()
	if outputs[0].ReleaseVersion == "" || outputs[0].ReleaseVersion == outputs[1].ReleaseVersion {
		t.Fatal("release collision", outputs)
	}
	delta := outputs[0].ReleaseSequence - outputs[1].ReleaseSequence
	if delta != 1 && delta != -1 {
		t.Fatal("non-consecutive release sequence", delta)
	}
	if _, err := repo.CompletePublication(ctx, fixtures[0], nil, "test", "stale callback"); !errors.Is(err, modelregistry.ErrConflict) {
		t.Fatal("stale completion accepted", err)
	}
}

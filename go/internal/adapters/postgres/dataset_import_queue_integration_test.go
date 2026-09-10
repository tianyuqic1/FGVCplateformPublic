package postgres_test

import (
	"context"
	"errors"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	postgresadapter "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/postgres"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
)

func TestDatasetImportQueueDurabilityBoundsRecoveryAndFencing(t *testing.T) {
	url := os.Getenv("FINEVISION_TEST_GO_DATABASE_URL")
	if url == "" {
		t.Skip("requires isolated migrated PostgreSQL")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	// Dedicated schema keeps global queue-cap tests separate from other tests and
	// any application workers connected to the database.
	schema := "import_test_" + uuid.NewString()[:8]
	if _, err = pool.Exec(ctx, `CREATE SCHEMA `+schema); err != nil {
		t.Fatal(err)
	}
	defer pool.Exec(ctx, `DROP SCHEMA `+schema+` CASCADE`)
	if _, err = pool.Exec(ctx, `CREATE TABLE `+schema+`.dataset_import_jobs (LIKE public.dataset_import_jobs INCLUDING ALL)`); err != nil {
		t.Fatal(err)
	}
	config, err := pgxpool.ParseConfig(url)
	if err != nil {
		t.Fatal(err)
	}
	config.ConnConfig.RuntimeParams["search_path"] = schema + ",public"
	isolated, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	defer isolated.Close()
	repo := &postgresadapter.DatasetImportQueue{Pool: isolated}
	newJob := func() dataset.ImportJob {
		id := uuid.NewString()
		return dataset.ImportJob{ID: id, DatasetID: "test-" + id, VersionKey: "v1", Status: "queued", CreatedAt: time.Now().UTC(), Archive: artifact.Descriptor{URI: "s3://test/archive", ArtifactID: uuid.NewString(), DatasetVersionID: id}}
	}
	first := newJob()
	if err = repo.Enqueue(ctx, first); err != nil {
		t.Fatal(err)
	}
	duplicate := first
	duplicate.ID = uuid.NewString()
	if err = repo.Enqueue(ctx, duplicate); !errors.Is(err, dataset.ErrConflict) {
		t.Fatalf("duplicate admission: %v", err)
	}
	for i := 1; i < 20; i++ {
		if err = repo.Enqueue(ctx, newJob()); err != nil {
			t.Fatal(err)
		}
	}
	if err = repo.Enqueue(ctx, newJob()); !errors.Is(err, dataset.ErrQueueFull) {
		t.Fatalf("queue bound: %v", err)
	}
	var wg sync.WaitGroup
	claims := make(chan dataset.ImportJob, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			job, e := repo.Claim(ctx)
			if e == nil {
				claims <- job
			} else if !errors.Is(e, dataset.ErrNoImportJob) {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	close(claims)
	if len(claims) != 1 {
		t.Fatalf("concurrent workers claimed %d jobs", len(claims))
	}
	claimed := <-claims
	if claimed.ID != first.ID {
		t.Fatal("queue did not preserve FIFO")
	}
	if err = repo.Renew(ctx, claimed); err != nil {
		t.Fatal(err)
	}
	if _, err = isolated.Exec(ctx, `UPDATE dataset_import_jobs SET lease_until=now()-interval '1 second' WHERE id=$1`, claimed.ID); err != nil {
		t.Fatal(err)
	}
	// A new repository instance recovers a crashed worker's persisted job.
	reopened := &postgresadapter.DatasetImportQueue{Pool: isolated}
	recovered, err := reopened.Claim(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if recovered.ID != claimed.ID || recovered.Attempt != claimed.Attempt+1 {
		t.Fatal("job not recovered")
	}
	if err = repo.Finish(ctx, claimed, nil, ""); !errors.Is(err, dataset.ErrImportLeaseLost) {
		t.Fatalf("stale worker wrote result: %v", err)
	}
	if err = reopened.Finish(ctx, recovered, map[string]any{"dataset": map[string]any{"id": first.DatasetID}}, ""); err != nil {
		t.Fatal(err)
	}
	jobs, err := repo.List(ctx)
	if err != nil || len(jobs) != 20 {
		t.Fatalf("list %d %v", len(jobs), err)
	}
	found := false
	for _, j := range jobs {
		if j.ID == first.ID {
			found = j.Status == "succeeded" && j.Result != nil
		}
	}
	if !found {
		t.Fatal("result not persisted")
	}
	next, err := repo.Claim(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err = repo.Finish(ctx, next, nil, "corrupt image"); err != nil {
		t.Fatal(err)
	}
	retry := newJob()
	retry.DatasetID = next.DatasetID
	retry.VersionKey = next.VersionKey
	if err = repo.Enqueue(ctx, retry); err != nil {
		t.Fatalf("failed version cannot be retried: %v", err)
	}
}

func TestDatasetImportRegistrationCanBeAcknowledgedAfterCrash(t *testing.T) {
	url := os.Getenv("FINEVISION_TEST_GO_DATABASE_URL")
	if url == "" {
		t.Skip("requires migrated PostgreSQL")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	repo := postgresadapter.DatasetRepository{Pool: pool}
	id, key := uuid.NewString(), "recovery-"+uuid.NewString()
	archive := artifact.Descriptor{ArtifactID: uuid.NewString(), URI: "s3://test/archive", ArtifactType: "dataset_archive", SHA256: strings.Repeat("a", 64), SizeBytes: 10, ContentType: "application/zip", Producer: "test"}
	manifest := artifact.Descriptor{ArtifactID: uuid.NewString(), URI: "s3://test/manifest", ArtifactType: "dataset_manifest", SHA256: strings.Repeat("b", 64), SizeBytes: 10, ContentType: "application/json", Producer: "test"}
	data := map[string]any{"readiness": map[string]any{"ready": true, "sample_count": float64(2), "class_count": float64(2)}, "split_counts": map[string]any{}}
	defer pool.Exec(ctx, `DELETE FROM datasets WHERE dataset_key=$1`, key)
	defer pool.Exec(ctx, `DELETE FROM dataset_versions WHERE id=$1`, id)
	defer func() {
		pool.Exec(ctx, `UPDATE dataset_versions SET manifest_artifact_id=NULL WHERE id=$1`, id)
		pool.Exec(ctx, `DELETE FROM artifacts WHERE dataset_version_id=$1`, id)
	}()
	if err = repo.Save(ctx, key, "v1", id, archive, manifest, data); err != nil {
		t.Fatal(err)
	}
	if err = repo.Save(ctx, key, "v1", id, archive, manifest, data); err != nil {
		t.Fatalf("replayed commit: %v", err)
	}
	if err = repo.Save(ctx, key, "v1", uuid.NewString(), archive, manifest, data); !errors.Is(err, dataset.ErrConflict) {
		t.Fatalf("different job overwrote version: %v", err)
	}
}

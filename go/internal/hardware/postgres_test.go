package hardware_test

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	postgres "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/postgres"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/hardware"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
)

func TestPostgresPersistenceRetentionAndTaskBinding(t *testing.T) {
	url := os.Getenv("FINEVISION_TEST_GO_DATABASE_URL")
	if url == "" {
		t.Skip("requires migrated isolated PostgreSQL")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	store := &hardware.PostgresStore{Pool: pool}
	node := "hardware-" + uuid.NewString()
	now := time.Now().UTC().Truncate(time.Microsecond)
	defer pool.Exec(ctx, `DELETE FROM hardware_nodes WHERE node_id=$1`, node)
	sample := hardware.Snapshot{NodeID: node, Name: "Test node", Scope: "host", GPUStatus: "none", GPUs: []hardware.GPU{}, Disks: []hardware.Disk{}, Errors: []string{}}
	for _, delta := range []time.Duration{-25 * time.Hour, -time.Minute, -55 * time.Second, 0} {
		sample.SampledAt = now.Add(delta)
		sample.ReceivedAt = sample.SampledAt
		if err := store.Ingest(ctx, sample); err != nil {
			t.Fatal(err)
		}
	}
	if err := store.Ingest(ctx, sample); !errors.Is(err, hardware.ErrOldSample) {
		t.Fatal("duplicate accepted", err)
	}
	reopened := &hardware.PostgresStore{Pool: pool}
	latest, err := reopened.Latest(ctx, node)
	if err != nil || !latest.ReceivedAt.Equal(now) {
		t.Fatal("lost latest after reopen", err, latest)
	}
	fine, err := store.History(ctx, node, now.Add(-time.Hour), 1)
	if err != nil || len(fine) != 3 {
		t.Fatal("history", len(fine), err)
	}
	coarse, err := store.History(ctx, node, now.Add(-time.Hour), 300)
	if err != nil || len(coarse) > 2 {
		t.Fatal("downsampling", len(coarse), err)
	}
	if err := store.Prune(ctx); err != nil {
		t.Fatal(err)
	}
	var count int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM hardware_samples WHERE node_id=$1`, node).Scan(&count); err != nil || count != 3 {
		t.Fatal("retention", count, err)
	}
	if _, err := reopened.Latest(ctx, node); err != nil {
		t.Fatal("retention removed node", err)
	}
	d, v := uuid.NewString(), uuid.NewString()
	_, err = pool.Exec(ctx, `INSERT INTO datasets(id,dataset_key,name,status,created_at,updated_at) VALUES($1::uuid,$1::text,'hardware-test','ready',now(),now())`, d)
	if err != nil {
		t.Fatal(err)
	}
	_, err = pool.Exec(ctx, `INSERT INTO dataset_versions(id,dataset_id,version_key,root_uri,sample_count,class_count,split_summary,readiness_status,readiness_report,created_at) VALUES($1::uuid,$2::uuid,$1::text,'s3://test',4,2,'{}','ready','{}',now())`, v, d)
	if err != nil {
		t.Fatal(err)
	}
	service := training.NewService(postgres.NewTrainingRepository(pool), time.Now, 2*time.Minute)
	created, err := service.Create(ctx, training.CreateCommand{DatasetID: d, DatasetVersionID: v, BackboneID: "test", MaxAttempts: 2, Payload: map[string]any{"name": "Hardware linked training"}})
	if err != nil {
		t.Fatal(err)
	}
	_, err = service.Claim(ctx, training.ClaimCommand{JobID: created.JobID, DispatchGeneration: 1, WorkerID: "node/" + node + "/worker:123"})
	if err != nil {
		t.Fatal(err)
	}
	tasks, err := store.Tasks(ctx, node)
	if err != nil || len(tasks) != 1 || tasks[0].ID != created.TrainingRunID {
		t.Fatal("task binding", tasks, err)
	}
	run, err := postgres.NewReadModels(pool).GetTrainingRun(ctx, created.TrainingRunID)
	if err != nil || run["runtime_node_id"] != node {
		t.Fatal("training node link", run, err)
	}
	tasks, err = store.Tasks(ctx, "unrelated")
	if err != nil || len(tasks) != 0 {
		t.Fatal("cross node task leak", tasks, err)
	}
	_, err = pool.Exec(ctx, `UPDATE job_attempts SET lease_expires_at=now()-interval '1 second' WHERE job_id=$1`, created.JobID)
	if err != nil {
		t.Fatal(err)
	}
	tasks, err = store.Tasks(ctx, node)
	if err != nil || len(tasks) != 0 {
		t.Fatal("expired task still active", tasks, err)
	}
}

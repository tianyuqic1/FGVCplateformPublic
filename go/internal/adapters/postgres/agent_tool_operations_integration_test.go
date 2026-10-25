package postgres_test

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	postgresadapter "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/postgres"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/agenttools"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
)

func TestAgentToolOperationsPersistConcurrentClaims(t *testing.T) {
	dsn := os.Getenv("FINEVISION_TEST_GO_DATABASE_URL")
	if dsn == "" {
		t.Skip("set FINEVISION_TEST_GO_DATABASE_URL for PostgreSQL integration")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	actor := uuid.NewString()
	_, err = pool.Exec(ctx, `INSERT INTO app_users(id,email,display_name,password_hash,role,status) VALUES($1,$2,'agent tool test','unused','business','active')`, actor, actor+"@test.invalid")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(), `DELETE FROM agent_tool_operations WHERE actor_id=$1`, actor)
		_, _ = pool.Exec(context.Background(), `DELETE FROM app_users WHERE id=$1`, actor)
	})
	store := &postgresadapter.AgentToolOperations{Pool: pool}
	op := agenttools.Operation{ID: uuid.NewString(), Actor: actor, Tool: "inference.run", Digest: "digest"}
	var fresh atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, won, err := store.Begin(ctx, op)
			if err != nil {
				t.Error(err)
			}
			if won {
				fresh.Add(1)
			}
		}()
	}
	wg.Wait()
	if fresh.Load() != 1 {
		t.Fatal("claims", fresh.Load())
	}
	if err := store.Finish(ctx, actor, op.ID, 200, json.RawMessage(`{"result":"once"}`)); err != nil {
		t.Fatal(err)
	}
	restarted := &postgresadapter.AgentToolOperations{Pool: pool}
	got, won, err := restarted.Begin(ctx, op)
	if err != nil || won || got.Status != "completed" || string(got.Response) != `{"result": "once"}` {
		var result map[string]string
		_ = json.Unmarshal(got.Response, &result)
		if err != nil || won || result["result"] != "once" {
			t.Fatalf("persisted replay: %+v %v", got, err)
		}
	}
	op.Digest = "changed"
	if _, _, err := store.Begin(ctx, op); err != agenttools.ErrConflict {
		t.Fatalf("conflict = %v", err)
	}
}

func TestAgentTrainingIdempotencyPersistsAcrossServices(t *testing.T) {
	dsn := os.Getenv("FINEVISION_TEST_GO_DATABASE_URL")
	if dsn == "" {
		t.Skip("set FINEVISION_TEST_GO_DATABASE_URL for PostgreSQL integration")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	datasetID, versionID := uuid.NewString(), uuid.NewString()
	if _, err = pool.Exec(ctx, `INSERT INTO datasets(id,dataset_key,name,status,created_at,updated_at) VALUES($1::uuid,$1::text,'agent idempotency','ready',now(),now())`, datasetID); err != nil {
		t.Fatal(err)
	}
	if _, err = pool.Exec(ctx, `INSERT INTO dataset_versions(id,dataset_id,version_key,root_uri,sample_count,class_count,split_summary,readiness_status,readiness_report,created_at) VALUES($1::uuid,$2::uuid,$1::text,'s3://test/archive',10,2,'{}','ready','{"ready":true}',now())`, versionID, datasetID); err != nil {
		t.Fatal(err)
	}
	command := training.CreateCommand{OperationKey: uuid.NewString() + ":" + uuid.NewString(), DatasetID: datasetID, DatasetVersionID: versionID, BackboneID: "test", Payload: map[string]any{"head_config": map[string]any{"epochs": 10}}}
	var wg sync.WaitGroup
	ids := make(chan string, 16)
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			service := training.NewService(postgresadapter.NewTrainingRepository(pool), time.Now, time.Minute)
			v, err := service.Create(ctx, command)
			if err != nil {
				t.Error(err)
				return
			}
			ids <- v.JobID
		}()
	}
	wg.Wait()
	close(ids)
	id := ""
	for value := range ids {
		if id == "" {
			id = value
		}
		if value != id {
			t.Fatal("different task IDs")
		}
	}
	if id == "" {
		t.Fatal("no job created")
	}
	var jobs, outbox int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM jobs WHERE id=$1`, id).Scan(&jobs); err != nil {
		t.Fatal(err)
	}
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM outbox_events WHERE aggregate_id=$1`, id).Scan(&outbox); err != nil {
		t.Fatal(err)
	}
	if jobs != 1 || outbox != 1 {
		t.Fatalf("jobs=%d outbox=%d", jobs, outbox)
	}
	actor, operationID, _ := strings.Cut(command.OperationKey, ":")
	store := &postgresadapter.AgentToolOperations{Pool: pool}
	data, found, err := store.Recover(ctx, agenttools.Operation{Actor: actor, ID: operationID, Tool: "training.submit_plan"})
	if err != nil || !found || !json.Valid(data) {
		t.Fatalf("training recovery: %s %v %v", data, found, err)
	}
	cancelID := uuid.NewString()
	service := training.NewService(postgresadapter.NewTrainingRepository(pool), time.Now, time.Minute)
	if err := service.CancelOperation(ctx, id, actor+":"+cancelID); err != nil {
		t.Fatal(err)
	}
	if err := service.CancelOperation(ctx, id, actor+":"+cancelID); err != nil {
		t.Fatal(err)
	}
	data, found, err = store.Recover(ctx, agenttools.Operation{Actor: actor, ID: cancelID, Tool: "training.cancel_run"})
	if err != nil || !found || !json.Valid(data) {
		t.Fatalf("cancellation recovery: %s %v %v", data, found, err)
	}
	command.Payload = map[string]any{"head_config": map[string]any{"epochs": 20}}
	_, err = training.NewService(postgresadapter.NewTrainingRepository(pool), time.Now, time.Minute).Create(ctx, command)
	if training.ErrorCode(err) != training.CodeIdempotencyConflict {
		t.Fatalf("parameter conflict = %v", err)
	}
	version, err := postgresadapter.NewReadModels(pool).GetDatasetVersion(ctx, versionID)
	if err != nil || version["dataset_version_id"] != versionID {
		t.Fatalf("version metadata: %v %v", version, err)
	}
}

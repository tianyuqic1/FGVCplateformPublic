package annotation

import (
	"context"
	"fmt"
	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

func TestCatalogValidationAPI(t *testing.T) {
	h := &Handler{Repo: &Repository{}}
	r := chi.NewRouter()
	h.Register(r)
	for _, tc := range []struct {
		body   string
		status int
	}{
		{`{"schema_version":1,"classes":[{"id":"a","name":"Alpha"},{"id":"b","name":"Beta"}]}`, 200},
		{`{"schema_version":2,"classes":[{"id":"a","name":"Alpha"},{"id":"b","name":"Beta"}]}`, 422},
		{`{"schema_version":1,"classes":[{"id":"a","name":"Alpha"},{"id":"a","name":"Beta"}]}`, 422},
		{`{"schema_version":1,"classes":[{"id":"a","name":"Alpha"},{"id":"b","name":"Alpha"}]}`, 422},
		{`{"schema_version":1,"classes":[{"id":1,"name":"Alpha"},{"id":"b","name":"Beta"}]}`, 422},
		{`{"schema_version":1,"classes":[{"id":"a","name":" Alpha"},{"id":"b","name":"Beta"}]}`, 422},
		{`{"schema_version":1,"classes":[{"id":"a","name":"Alpha","extra":true},{"id":"b","name":"Beta"}]}`, 422},
	} {
		w := httptest.NewRecorder()
		r.ServeHTTP(w, httptest.NewRequest("POST", "/api/annotation/catalog/validate", strings.NewReader(tc.body)))
		if w.Code != tc.status {
			t.Fatal(w.Code, w.Body.String())
		}
	}
	for _, key := range []string{"classes", "catalog"} {
		catalog := `[{"id":"a","name":"Alpha"},{"id":"b","name":"Beta"}]`
		if key == "catalog" {
			catalog = `{"schema_version":1,"classes":` + catalog + `}`
		}
		body := `{"name":"test","method":"A","domain":"general","` + key + `":` + catalog + `}`
		p, ok := decodeProject(httptest.NewRecorder(), httptest.NewRequest("POST", "/", strings.NewReader(body)))
		if !ok || len(p.Classes) != 2 {
			t.Fatal("creation compatibility", key)
		}
	}
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest("POST", "/api/annotation/projects/00000000-0000-0000-0000-000000000001/queue-all", strings.NewReader(`{"through_seq":10,"allow_remote":false}`)))
	if w.Code != 422 {
		t.Fatal("consent gate", w.Code)
	}
}
func TestBatchQueueDatabase(t *testing.T) {
	url := os.Getenv("ANNOTATION_TEST_DATABASE_URL")
	if url == "" {
		t.Skip("requires isolated annotation_test_ database")
	}
	cfg, e := pgxpool.ParseConfig(url)
	if e != nil {
		t.Fatal(e)
	}
	if !strings.HasPrefix(cfg.ConnConfig.Database, "annotation_test_") {
		t.Fatal("refusing business database")
	}
	ctx := context.Background()
	pool, e := pgxpool.NewWithConfig(ctx, cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer pool.Close()
	repo := &Repository{Pool: pool}
	p, e := repo.Create(ctx, fixtureProject())
	if e != nil {
		t.Fatal(e)
	}
	add := func(i int) Task {
		task, e := repo.Add(ctx, p.ID, "test.png", artifact.Descriptor{SHA256: fmt.Sprintf("%064x", i), SizeBytes: 10, ContentType: "image/png"})
		if e != nil {
			t.Fatal(e)
		}
		return task
	}
	for i := 1; i <= 25; i++ {
		add(i)
	}
	for i, status := range []string{"confirmed", "suggested", "failed", "unknown", "queued", "running"} {
		task := add(100 + i)
		if _, e = pool.Exec(ctx, `UPDATE annotation_tasks SET status=$2 WHERE id=$1`, task.ID, status); e != nil {
			t.Fatal(e)
		}
	}
	snapshot, e := repo.QueueSummary(ctx, p.ID)
	if e != nil || snapshot.Pending != 25 {
		t.Fatal(snapshot, e)
	}
	late := add(200)
	count, e := repo.QueueAll(ctx, p.ID, snapshot.ThroughSeq)
	if e != nil || count != 25 {
		t.Fatal(count, e)
	}
	count, e = repo.QueueAll(ctx, p.ID, snapshot.ThroughSeq)
	if e != nil || count != 0 {
		t.Fatal("replayed batch", count, e)
	}
	task, e := repo.Task(ctx, late.ID)
	if e != nil || task.Status != "pending" {
		t.Fatal("late upload incorrectly queued", e)
	}
	after, e := repo.QueueSummary(ctx, p.ID)
	if e != nil || after.Pending != 1 || after.Queued != 26 || after.Running != 1 {
		t.Fatal(after, e)
	}
	// Keep these synthetic queue entries out of other test cases' global claims.
	if _, e = pool.Exec(ctx, `UPDATE annotation_tasks SET status='failed' WHERE project_id=$1 AND status IN ('queued','running','pending')`, p.ID); e != nil {
		t.Fatal(e)
	}
}

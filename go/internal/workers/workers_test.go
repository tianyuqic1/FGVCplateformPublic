package workers

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/auth"
)

type fakeStore struct {
	items []Instance
	err   error
	calls int
}

func (s *fakeStore) Report(context.Context, Snapshot, bool) error { s.calls++; return s.err }
func (s *fakeStore) List(context.Context) ([]Instance, error)     { return s.items, s.err }
func sample() Snapshot {
	return Snapshot{ID: "training-test", SessionID: uuid.NewString(), Name: "训练实例", Kind: "training", Capacity: 1, Readiness: "ready", Tasks: []Task{}}
}

func TestRoleProjectionDoesNotLeakOtherTasksOrDiagnostics(t *testing.T) {
	v := sample()
	v.Reason = "private internal diagnostic"
	v.Sequence = 80
	v.Capacity = 4
	v.Tasks = []Task{{ID: "mine", Kind: "training", Stage: "internal stage", StartedAt: time.Now()}, {ID: "someone-else", Kind: "training", Stage: "private", StartedAt: time.Now()}, {ID: "legacy-owner-unknown", Kind: "training", StartedAt: time.Now()}}
	instance := Instance{Snapshot: v, ReceivedAt: time.Now()}
	instance.Derive(time.Now())
	business := visibleInstance(instance, auth.Business, map[string]bool{"mine": true})
	raw, _ := json.Marshal(business)
	for _, secret := range []string{"someone-else", "legacy-owner-unknown", "private internal", "internal stage", "session_id", "sequence"} {
		if bytes.Contains(raw, []byte(secret)) {
			t.Fatalf("business leaked %s: %s", secret, raw)
		}
	}
	if len(business.Tasks) != 1 || business.ActiveCount != 3 || business.TaskVisibility != "own_training_only" {
		t.Fatalf("projection %+v", business)
	}
	admin := visibleInstance(instance, auth.Admin, nil)
	if len(admin.Tasks) != 3 || admin.SessionID == "" || admin.Reason != v.Reason {
		t.Fatal("admin diagnostic lost")
	}
	instance.Kind = "annotation"
	instance.Tasks = []Task{{ID: "mine", Kind: "annotation"}}
	if len(visibleInstance(instance, auth.Business, map[string]bool{"mine": true}).Tasks) != 0 {
		t.Fatal("annotation exposed")
	}
}

func TestPostgresOwnershipLookup(t *testing.T) {
	dsn := os.Getenv("FINEVISION_WORKER_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("requires isolated test database")
	}
	ctx := context.Background()
	config, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(config.ConnConfig.Database, "test") {
		t.Fatal("test database required")
	}
	config.MaxConns = 1
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	_, err = pool.Exec(ctx, `CREATE TEMP TABLE jobs(id uuid PRIMARY KEY,payload jsonb);
 CREATE TEMP TABLE training_runs(id uuid PRIMARY KEY,job_id uuid);`)
	if err != nil {
		t.Fatal(err)
	}
	ids := []string{uuid.NewString(), uuid.NewString(), uuid.NewString()}
	owner := uuid.NewString()
	for n, id := range ids {
		payload := map[string]string{}
		if n == 0 {
			payload["created_by_user_id"] = owner
		}
		if n == 1 {
			payload["created_by_user_id"] = uuid.NewString()
		}
		raw, _ := json.Marshal(payload)
		if _, err = pool.Exec(ctx, `INSERT INTO jobs VALUES($1,$2)`, id, raw); err != nil {
			t.Fatal(err)
		}
		if _, err = pool.Exec(ctx, `INSERT INTO training_runs VALUES($1,$1)`, id); err != nil {
			t.Fatal(err)
		}
	}
	owned, err := (&PostgresStore{Pool: pool}).OwnedTraining(ctx, owner, ids)
	if err != nil {
		t.Fatal(err)
	}
	if len(owned) != 1 || !owned[ids[0]] {
		t.Fatalf("bad owner filtering: %#v", owned)
	}
}
func TestStateAndValidation(t *testing.T) {
	now := time.Now()
	v := sample()
	if !v.Validate() {
		t.Fatal("valid snapshot rejected")
	}
	for _, tc := range []struct {
		age       time.Duration
		want      string
		available bool
	}{{0, "online", true}, {30 * time.Second, "online", true}, {31 * time.Second, "delayed", false}, {91 * time.Second, "offline", false}} {
		i := Instance{Snapshot: v, ReceivedAt: now.Add(-tc.age)}
		i.Derive(now)
		if i.Connection != tc.want || i.CanAccept != tc.available {
			t.Fatalf("state %+v", i)
		}
	}
	v.Tasks = []Task{{ID: "run-1", Kind: "training", Stage: "训练", StartedAt: now}}
	i := Instance{Snapshot: v, ReceivedAt: now}
	i.Derive(now)
	if i.CanAccept || i.ActiveCount != 1 {
		t.Fatal("full worker available")
	}
	v.Capacity = 0
	if v.Validate() {
		t.Fatal("invalid capacity accepted")
	}
	v = sample()
	v.Kind = "shell"
	if v.Validate() {
		t.Fatal("unknown kind accepted")
	}
}
func TestHTTPBoundariesAndPagination(t *testing.T) {
	now := time.Now()
	store := &fakeStore{}
	for n := 0; n < 8; n++ {
		v := sample()
		v.ID = uuid.NewString()
		store.items = append(store.items, Instance{Snapshot: v, ReceivedAt: now})
	}
	router := chi.NewRouter()
	Handler{Store: store, Token: "test-only", Now: func() time.Time { return now }}.Register(router)
	request := func(method, path, body, token string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Authorization", token)
		w := httptest.NewRecorder()
		router.ServeHTTP(w, r)
		return w
	}
	w := request("GET", "/api/workers?limit=6&offset=6", "", "")
	var result struct {
		Items      []Instance
		Summary    map[string]int
		Pagination map[string]int
	}
	json.Unmarshal(w.Body.Bytes(), &result)
	if w.Code != 200 || len(result.Items) != 2 || result.Pagination["total"] != 8 || result.Summary["available"] != 8 {
		t.Fatalf("pagination: %s", w.Body.String())
	}
	for _, path := range []string{"/api/workers?limit=-1", "/api/workers?offset=-1", "/api/workers?status=garbage"} {
		if request("GET", path, "", "").Code != 400 {
			t.Fatal(path)
		}
	}
	if request("GET", "/api/workers/missing", "", "").Code != 404 {
		t.Fatal("missing instance")
	}
	raw, _ := json.Marshal(sample())
	if request("POST", "/api/internal/workers/register", string(raw), "").Code != 401 || store.calls != 0 {
		t.Fatal("unauthenticated write")
	}
	if request("POST", "/api/internal/workers/register", string(raw), "Bearer test-only").Code != 202 {
		t.Fatal("valid registration")
	}
	if request("POST", "/api/internal/workers/register", string(raw)+"{}", "Bearer test-only").Code != 400 {
		t.Fatal("trailing JSON")
	}
	store.err = ErrConflict
	if request("POST", "/api/internal/workers/heartbeat", string(raw), "Bearer test-only").Code != 409 {
		t.Fatal("stale heartbeat")
	}
	store.err = errors.New("db secret")
	w = request("GET", "/api/workers", "", "")
	if w.Code != 503 || bytes.Contains(w.Body.Bytes(), []byte("db secret")) {
		t.Fatal("database error leaked")
	}
}
func TestPostgresSessionFencing(t *testing.T) {
	dsn := os.Getenv("FINEVISION_WORKER_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("requires isolated migrated test database")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	if !strings.Contains(pool.Config().ConnConfig.Database, "test") {
		t.Fatal("test database required")
	}
	store := &PostgresStore{Pool: pool}
	v := sample()
	v.ID = "test-" + uuid.NewString()
	defer pool.Exec(ctx, `DELETE FROM worker_instances WHERE id=$1`, v.ID)
	defer pool.Exec(ctx, `DELETE FROM worker_sessions WHERE worker_id=$1`, v.ID)
	if err = store.Report(ctx, v, true); err != nil {
		t.Fatal(err)
	}
	v.Sequence = 1
	if err = store.Report(ctx, v, false); err != nil {
		t.Fatal(err)
	}
	if err = store.Report(ctx, v, false); !errors.Is(err, ErrConflict) {
		t.Fatal("duplicate heartbeat accepted")
	}
	old := v
	next := v
	next.SessionID = uuid.NewString()
	next.Sequence = 0
	if err = store.Report(ctx, next, true); err != nil {
		t.Fatal(err)
	}
	if err = store.Report(ctx, old, true); !errors.Is(err, ErrConflict) {
		t.Fatal("retired session revived")
	}
	old.Sequence = 999
	if err = store.Report(ctx, old, false); !errors.Is(err, ErrConflict) {
		t.Fatal("retired heartbeat accepted")
	}
	next.Sequence = 1
	if err = store.Report(ctx, next, false); err != nil {
		t.Fatal(err)
	}
	if err = store.Report(ctx, next, true); err != nil {
		t.Fatal("idempotent registration failed", err)
	}
	var seq int
	pool.QueryRow(ctx, `SELECT sequence FROM worker_instances WHERE id=$1`, v.ID).Scan(&seq)
	if seq != 1 {
		t.Fatal("registration reset sequence")
	}
}

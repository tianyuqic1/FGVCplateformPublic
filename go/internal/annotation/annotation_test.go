package annotation

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"image"
	"image/png"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

func fixtureProject() Project {
	return Project{Name: "annotation acceptance", Classes: []Class{{"a", "Alpha"}, {"b", "Beta"}}, Method: "D", Domain: "general"}
}
func TestValidation(t *testing.T) {
	p := fixtureProject()
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*Project){func(p *Project) { p.Name = "" }, func(p *Project) { p.Method = "" }, func(p *Project) { p.Method = "CD" }, func(p *Project) { p.Domain = "unsafe" }, func(p *Project) { p.Classes[1].ID = "a" }, func(p *Project) { p.Classes = nil }} {
		p := fixtureProject()
		mutate(&p)
		if p.Validate() == nil {
			t.Fatal("accepted invalid project")
		}
	}
	var buf bytes.Buffer
	_ = png.Encode(&buf, image.NewRGBA(image.Rect(0, 0, 8, 8)))
	if kind, err := validateImage(buf.Bytes()); err != nil || kind != "image/png" {
		t.Fatal(kind, err)
	}
	for _, data := range [][]byte{nil, []byte("<svg onload='x'/>"), buf.Bytes()[:30], make([]byte, 20<<20+1)} {
		if _, err := validateImage(data); err == nil {
			t.Fatal("accepted bad image")
		}
	}
}
func TestUnavailableAndWorkerAuth(t *testing.T) {
	for _, tc := range []struct {
		h    *Handler
		path string
		want int
	}{{nil, "/api/annotation/projects", 503}, {&Handler{Repo: &Repository{}, Token: "private"}, "/api/annotation/internal/claim", 401}} {
		r := chi.NewRouter()
		tc.h.Register(r)
		w := httptest.NewRecorder()
		method := "GET"
		if strings.HasSuffix(tc.path, "claim") {
			method = "POST"
		}
		r.ServeHTTP(w, httptest.NewRequest(method, tc.path, nil))
		if w.Code != tc.want {
			t.Fatal(w.Code, w.Body.String())
		}
	}
}
func TestDatabaseLifecycle(t *testing.T) {
	url := os.Getenv("ANNOTATION_TEST_DATABASE_URL")
	if url == "" {
		t.Skip("requires disposable annotation_test_ database, migrated to head")
	}
	cfg, err := pgxpool.ParseConfig(url)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(cfg.ConnConfig.Database, "annotation_test_") {
		t.Fatal("refusing non-test database")
	}
	ctx := context.Background()
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	s := &Repository{Pool: pool}
	p, err := s.Create(ctx, fixtureProject())
	if err != nil {
		t.Fatal(err)
	}
	a := artifact.Descriptor{SHA256: strings.Repeat("a", 64), SizeBytes: 100, ContentType: "image/png"}
	task, err := s.Add(ctx, p.ID, "image.png", a)
	if err != nil {
		t.Fatal(err)
	}
	duplicate, err := s.Add(ctx, p.ID, "again.png", a)
	if err != nil || duplicate.ID != task.ID {
		t.Fatal("SHA dedup failed", err)
	}
	tasks, total, err := s.Tasks(ctx, p.ID, "", 1)
	if err != nil || total != 1 || len(tasks) != 1 {
		t.Fatal(tasks, total, err)
	}
	if err = s.Confirm(ctx, task.ID, "invalid", "human"); !errors.Is(err, ErrInvalid) {
		t.Fatal(err)
	}
	count, err := s.Queue(ctx, p.ID, []string{task.ID})
	if err != nil || count != 1 {
		t.Fatal(count, err)
	}
	if err = s.Confirm(ctx, task.ID, "a", "human"); !errors.Is(err, ErrConflict) {
		t.Fatal(err)
	}
	claimed, err := s.Claim(ctx)
	if err != nil || claimed.ID != task.ID {
		t.Fatal(claimed, err)
	}
	if _, err = s.Claim(ctx); !errors.Is(err, pgx.ErrNoRows) {
		t.Fatal("double claim", err)
	}
	result := json.RawMessage(`{"class_ids":["a","b"]}`)
	if err = s.Finish(ctx, task.ID, uuid.NewString(), "suggested", result, ""); !errors.Is(err, ErrConflict) {
		t.Fatal("stale token accepted", err)
	}
	if err = s.Finish(ctx, task.ID, claimed.Token, "suggested", json.RawMessage(`{"class_ids":["a","a"]}`), ""); !errors.Is(err, ErrInvalid) {
		t.Fatal("duplicate candidates accepted", err)
	}
	if err = s.Finish(ctx, task.ID, claimed.Token, "suggested", result, ""); err != nil {
		t.Fatal(err)
	}
	if err = s.Finish(ctx, task.ID, claimed.Token, "suggested", result, ""); err != nil {
		t.Fatal("non-idempotent result ACK", err)
	}
	if _, err = s.MemoryNext(ctx); !errors.Is(err, pgx.ErrNoRows) {
		t.Fatal("unconfirmed data entered memory")
	}
	if err = s.Confirm(ctx, task.ID, "b", "human"); err != nil {
		t.Fatal(err)
	}
	if err = s.Confirm(ctx, task.ID, "b", "human"); err != nil {
		t.Fatal("duplicate confirm", err)
	}
	if err = s.Confirm(ctx, task.ID, "a", "human"); !errors.Is(err, ErrConflict) {
		t.Fatal("overwrote confirmation", err)
	}
	memory, err := s.MemoryNext(ctx)
	if err != nil || memory.Label != "b" {
		t.Fatal(memory, err)
	}
	if err = s.MemoryAck(ctx, task.ID, false, "unavailable"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.MemoryNext(ctx); !errors.Is(err, pgx.ErrNoRows) {
		t.Fatal("missing outbox cooldown", err)
	}
	if err = s.MemoryAck(ctx, task.ID, true, ""); err != nil {
		t.Fatal(err)
	}
	p, err = s.Project(ctx, p.ID)
	if err != nil || p.Confirmed != 1 || p.Indexed != 1 {
		t.Fatal(p, err)
	}
	// Expired paid-work leases are quarantined, never silently requeued.
	a.SHA256 = strings.Repeat("b", 64)
	expired, err := s.Add(ctx, p.ID, "expired.png", a)
	if err != nil {
		t.Fatal(err)
	}
	_, err = pool.Exec(ctx, `UPDATE annotation_tasks SET status='running',lease_until=now()-interval '1 minute' WHERE id=$1`, expired.ID)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = s.Claim(ctx)
	expired, err = s.Task(ctx, expired.ID)
	if err != nil || expired.Status != "unknown" {
		t.Fatal(expired, err)
	}
	count, err = s.Queue(ctx, p.ID, []string{expired.ID})
	if err != nil || count != 0 {
		t.Fatal("unknown requeued", err)
	}
	// Project scope excludes jobs from other projects even with known task IDs.
	other, err := s.Create(ctx, fixtureProject())
	if err != nil {
		t.Fatal(err)
	}
	a.SHA256 = strings.Repeat("c", 64)
	fresh, err := s.Add(ctx, p.ID, "fresh.png", a)
	if err != nil {
		t.Fatal(err)
	}
	count, err = s.Queue(ctx, other.ID, []string{fresh.ID})
	if err != nil || count != 0 {
		t.Fatal("cross-project queue", err)
	}
}

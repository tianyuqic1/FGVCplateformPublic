package agenttools

import (
	"context"
	"encoding/json"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestReceiptsBindSessionOperationAndExactParameters(t *testing.T) {
	key := []byte("01234567890123456789012345678901")
	now := time.Now()
	r := Receipt{Actor: "user", Session: "session", Tool: "training.submit_plan", Operation: "op", Digest: "digest", Expires: now.Add(time.Minute).Unix()}
	token, err := Sign(key, r)
	if err != nil {
		t.Fatal(err)
	}
	if err := Verify(key, token, r, now); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*Receipt){func(r *Receipt) { r.Actor = "other" }, func(r *Receipt) { r.Session = "other-session" }, func(r *Receipt) { r.Tool = "inference.run" }, func(r *Receipt) { r.Operation = "new" }, func(r *Receipt) { r.Digest = "modified" }} {
		copy := r
		mutate(&copy)
		if Verify(key, token, copy, now) == nil {
			t.Fatal("receipt scope escaped")
		}
	}
	if Verify(key, token, r, now.Add(2*time.Minute)) == nil || Verify([]byte("short"), token, r, now) == nil {
		t.Fatal("expired or weak key accepted")
	}
}
func TestConcurrentOperationClaimAndArgumentConflict(t *testing.T) {
	s := NewMemoryStore()
	ctx := context.Background()
	op := Operation{ID: "id", Actor: "a", Tool: "inference.run", Digest: "digest"}
	var fresh atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 30; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, won, err := s.Begin(ctx, op)
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
		t.Fatal(fresh.Load())
	}
	changed := op
	changed.Digest = "changed"
	if _, _, err := s.Begin(ctx, changed); err != ErrConflict {
		t.Fatal("digest conflict missing")
	}
	if err := s.Finish(ctx, "a", "id", 200, json.RawMessage(`{"result":"ok"}`)); err != nil {
		t.Fatal(err)
	}
	got, won, _ := s.Begin(ctx, op)
	if won || got.Status != "completed" || string(got.Response) != `{"result":"ok"}` {
		t.Fatal(got)
	}
	if _, err := s.Get(ctx, "another-user", "id"); err != ErrNotFound {
		t.Fatal("cross-user read")
	}
}

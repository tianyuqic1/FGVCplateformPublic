package platform

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"

	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/tools"
)

func TestConcurrentSessionsAreRequestScoped(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Cookie") == "" || r.Header.Get("X-CSRF-Token") == "" {
			t.Error("credentials missing")
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"tool": "datasets.list", "data": map[string]string{"cookie": r.Header.Get("Cookie")}})
	}))
	defer server.Close()
	client, _ := New(server.URL, nil)
	var wg sync.WaitGroup
	for _, cookie := range []string{"user-a", "user-b"} {
		wg.Add(1)
		go func(cookie string) {
			defer wg.Done()
			ctx := WithSession(context.Background(), Session{Cookie: cookie, CSRF: "csrf"})
			v, err := client.Invoke(ctx, "datasets.list", json.RawMessage(`{}`), tools.Control{})
			if err != nil {
				t.Error(err)
				return
			}
			var data map[string]string
			_ = json.Unmarshal(v.Data, &data)
			if data["cookie"] != cookie {
				t.Error("cross-user credential leakage")
			}
		}(cookie)
	}
	wg.Wait()
}
func TestNoRedirectCredentialLeakOrAutomaticWriteRetry(t *testing.T) {
	leaks := 0
	destination := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { leaks++ }))
	defer destination.Close()
	calls := 0
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++; http.Redirect(w, r, destination.URL, 307) }))
	defer origin.Close()
	c, _ := New(origin.URL, nil)
	ctx := WithSession(context.Background(), Session{Cookie: "secret-cookie", CSRF: "secret-csrf"})
	_, err := c.Invoke(ctx, "inference.run", json.RawMessage(`{}`), tools.Control{OperationID: "op", Approval: "approval"})
	if err == nil || calls != 1 || leaks != 0 {
		t.Fatalf("redirect/retry: %v calls=%d leaks=%d", err, calls, leaks)
	}
}
func TestMissingCredentialsAndOversizeResponses(t *testing.T) {
	calls := 0
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++; _, _ = w.Write(make([]byte, (512<<10)+1)) }))
	defer s.Close()
	c, _ := New(s.URL, nil)
	if _, err := c.Invoke(context.Background(), "datasets.list", json.RawMessage(`{}`), tools.Control{}); err == nil || calls != 0 {
		t.Fatal("missing identity reached network")
	}
	ctx := WithSession(context.Background(), Session{Cookie: "cookie", CSRF: "csrf"})
	_, err := c.Invoke(ctx, "inference.run", json.RawMessage(`{}`), tools.Control{OperationID: "op", Approval: "approval"})
	e, ok := err.(*tools.Error)
	if !ok || !e.Unknown {
		t.Fatalf("write outcome must be unknown: %v", err)
	}
}

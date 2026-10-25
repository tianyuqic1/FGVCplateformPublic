package auth

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/delegation"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestDelegationIsScopedAndRevokedWithLiveSession(t *testing.T) {
	ctx := context.Background()
	store := newMemoryStore()
	id := uuid.New()
	user := User{ID: id, Email: "agent-test@example.test", Role: Business, Status: Active}
	if e := store.CreateUser(ctx, user, "unused"); e != nil {
		t.Fatal(e)
	}
	secret := strings.Repeat("s", 43)
	hash := sha256.Sum256([]byte(secret))
	if e := store.CreateSession(ctx, hash[:], id, "csrf", time.Now().Add(time.Hour)); e != nil {
		t.Fatal(e)
	}
	service := New(store)
	service.AgentKey = []byte(strings.Repeat("k", 32))
	calls := 0
	handler := service.Protect(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++; w.WriteHeader(204) }))
	c := delegation.Claims{Actor: id.String(), Role: "business", Session: hex.EncodeToString(hash[:]), Purpose: "tools", Expires: time.Now().Add(time.Minute).Unix()}
	token, _ := delegation.Sign(service.AgentKey, c)
	request := func(method, path, assertion string) int {
		r := httptest.NewRequest(method, path, nil)
		r.Header.Set("X-Agent-Delegation", assertion)
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		return w.Code
	}
	if code := request("POST", "/api/agent-tools/invoke/datasets.list", token); code != 204 {
		t.Fatalf("tool rejected %d", code)
	}
	if code := request("POST", "/api/agent-tools/approvals/training.cancel_run", token); code != 401 {
		t.Fatalf("background grant approved write %d", code)
	}
	if code := request("GET", "/api/auth/me", token); code != 401 {
		t.Fatalf("grant escaped scope %d", code)
	}
	c.Purpose = "interactive"
	interactive, _ := delegation.Sign(service.AgentKey, c)
	if code := request("POST", "/api/agent-tools/approvals/training.cancel_run", interactive); code != 204 {
		t.Fatalf("interactive approval rejected %d", code)
	}
	// The signed role is not authoritative: the platform rereads the current role.
	store.mu.Lock()
	a := store.accounts[user.Email]
	a.Role = Annotator
	store.accounts[user.Email] = a
	store.mu.Unlock()
	if code := request("POST", "/api/agent-tools/invoke/training.list_runs", token); code != 403 {
		t.Fatalf("role downgrade ignored %d", code)
	}
	if e := store.DeleteSession(ctx, hash[:]); e != nil {
		t.Fatal(e)
	}
	if code := request("GET", "/api/agent-tools/catalog", token); code != 401 {
		t.Fatalf("revoked session accepted %d", code)
	}
	if calls != 2 {
		t.Fatalf("unauthorized dispatches %d", calls)
	}
}

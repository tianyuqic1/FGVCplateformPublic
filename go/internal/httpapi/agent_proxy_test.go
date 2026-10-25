package httpapi

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/delegation"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/auth"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type proxySessions struct {
	auth.Store
	User auth.User
	Hash []byte
}

func (s proxySessions) FindSession(_ context.Context, hash []byte) (auth.Session, error) {
	if string(hash) != string(s.Hash) {
		return auth.Session{}, auth.ErrCredentials
	}
	return auth.Session{User: s.User, CSRFToken: "csrf-token"}, nil
}
func TestAgentProxyAuthenticatesAndReplacesIdentity(t *testing.T) {
	key := []byte(strings.Repeat("k", 32))
	secret := strings.Repeat("s", 43)
	hash := sha256.Sum256([]byte(secret))
	user := auth.User{ID: uuid.New(), Role: auth.Business, Status: auth.Active}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, e := delegation.Verify(key, r.Header.Get("X-Agent-Identity"), time.Now())
		if e != nil || c.Actor != user.ID.String() || c.Purpose != "interactive" {
			t.Error("invalid proxy identity")
		}
		if r.Header.Get("Cookie") != "" || r.Header.Get("Authorization") != "" || r.Header.Get("X-CSRF-Token") != "" || r.Header.Get("X-Agent-Delegation") != "" {
			t.Error("browser credentials leaked")
		}
		if r.URL.Path != "/v1/conversations" {
			t.Errorf("path %s", r.URL.Path)
		}
		_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	}))
	defer upstream.Close()
	a := auth.New(proxySessions{User: user, Hash: hash[:]})
	a.AgentKey = key
	router := NewRouter(Dependencies{Auth: a, AgentURL: upstream.URL})
	request := func(cookie, csrf string) int {
		r := httptest.NewRequest("POST", "/api/agent/conversations", strings.NewReader(`{}`))
		r.Header.Set("Cookie", cookie)
		r.Header.Set("X-CSRF-Token", csrf)
		r.Header.Set("X-Agent-Identity", "forged")
		r.Header.Set("Authorization", "browser-secret")
		w := httptest.NewRecorder()
		router.ServeHTTP(w, r)
		return w.Code
	}
	if code := request("", ""); code != 401 {
		t.Fatal(code)
	}
	if code := request("fv_session="+secret, ""); code != 403 {
		t.Fatal(code)
	}
	if code := request("fv_session="+secret, "csrf-token"); code != 200 {
		t.Fatal(code)
	}
}

func TestDelegatedReadToolCanReachProtectedResource(t *testing.T) {
	key := []byte(strings.Repeat("k", 32))
	secret := strings.Repeat("s", 43)
	hash := sha256.Sum256([]byte(secret))
	user := auth.User{ID: uuid.New(), Role: auth.Business, Status: auth.Active}
	a := auth.New(proxySessions{User: user, Hash: hash[:]})
	a.AgentKey = key
	token, _ := delegation.Sign(key, delegation.Claims{Actor: user.ID.String(), Role: "business", Session: fmt.Sprintf("%x", hash[:]), Purpose: "tools", Expires: time.Now().Add(time.Minute).Unix()})
	router := NewRouter(Dependencies{Auth: a})
	request := httptest.NewRequest("POST", "/api/agent-tools/invoke/datasets.list", strings.NewReader(`{"arguments":{}}`))
	request.Header.Set("X-Agent-Delegation", token)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, request)
	if w.Code != 200 {
		t.Fatalf("delegated resource %d %s", w.Code, w.Body.String())
	}
}

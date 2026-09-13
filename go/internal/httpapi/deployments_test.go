package httpapi

import (
	"context"
	"github.com/go-chi/chi/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/deployment"
	"net/http/httptest"
	"strings"
	"testing"
)

type deploymentStub struct{ DeploymentApplication }

func (*deploymentStub) List(context.Context, string) ([]deployment.Record, error) {
	return []deployment.Record{}, nil
}
func (*deploymentStub) RuntimeTargets() []deployment.Target                     { return []deployment.Target{} }
func (*deploymentStub) Heartbeat(context.Context, string, string, string) error { return nil }
func TestDeploymentRoutesAndCallbackAuthentication(t *testing.T) {
	router := chi.NewRouter()
	registerDeployments(router, &deploymentStub{}, "test-token")
	for _, path := range []string{"/api/model-versions/model/deployments", "/api/model-versions/model/deployments/"} {
		w := httptest.NewRecorder()
		router.ServeHTTP(w, httptest.NewRequest("GET", path, nil))
		if w.Code != 200 {
			t.Fatal(path, w.Code)
		}
	}
	path := "/api/internal/deployments/11111111-1111-4111-8111-111111111111/heartbeat"
	for _, header := range []string{"", "wrong", "test-token", "Bearer wrong", "Bearer test-token"} {
		r := httptest.NewRequest("POST", path, strings.NewReader(`{"build_token":"22222222-2222-4222-8222-222222222222","worker_id":"test"}`))
		r.Header.Set("Authorization", header)
		w := httptest.NewRecorder()
		router.ServeHTTP(w, r)
		want := 401
		if header == "Bearer test-token" {
			want = 200
		}
		if w.Code != want {
			t.Fatal(header, w.Code)
		}
	}
	r := httptest.NewRequest("POST", path, strings.NewReader(`{"unknown":true}`))
	r.Header.Set("Authorization", "Bearer test-token")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, r)
	if w.Code != 422 {
		t.Fatal(w.Code)
	}
}

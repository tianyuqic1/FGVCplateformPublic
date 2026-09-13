package httpapi

import (
	"bytes"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestComparisonHTTPRejectsCrossScope(t *testing.T) {
	for _, other := range []struct{ dataset, version string }{{"birds", "v2"}, {"flowers", "v1"}, {"", ""}} {
		repository := modelregistry.NewMemoryRepository(
			modelregistry.Version{ID: "11111111-1111-4111-8111-111111111111", DatasetID: "birds", DatasetVersionID: "v1"},
			modelregistry.Version{ID: "22222222-2222-4222-8222-222222222222", DatasetID: other.dataset, DatasetVersionID: other.version},
		)
		handler := NewRouter(Dependencies{ModelRegistry: modelregistry.NewService(repository)})
		req := httptest.NewRequest(http.MethodPost, "/api/model-version-comparisons", bytes.NewBufferString(`{"model_version_ids":["11111111-1111-4111-8111-111111111111","22222222-2222-4222-8222-222222222222"]}`))
		req.Header.Set("Content-Type", "application/json")
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, req)
		if response.Code != 422 {
			t.Fatalf("scope %+v: %d %s", other, response.Code, response.Body.String())
		}
	}
}

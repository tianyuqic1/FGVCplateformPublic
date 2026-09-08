package httpapi

import (
	"bytes"
	"context"
	"errors"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/datasetcard"
	"net/http/httptest"
	"strings"
	"testing"
)

type cardRepoStub struct {
	err   error
	state datasetcard.State
}

func (r cardRepoStub) Get(context.Context, string) (datasetcard.State, error) { return r.state, r.err }
func (r cardRepoStub) Save(context.Context, string, int, datasetcard.Card, string) (datasetcard.State, error) {
	return r.state, r.err
}
func (r cardRepoStub) Begin(context.Context, string, string, string, int) (datasetcard.Generation, bool, error) {
	return datasetcard.Generation{}, false, r.err
}
func (r cardRepoStub) Finish(context.Context, string, *datasetcard.Generated, string) error {
	return r.err
}
func TestDatasetCardHTTPErrorContract(t *testing.T) {
	for _, tc := range []struct {
		name, method, path, body string
		err                      error
		status                   int
	}{
		{"get", "GET", "/card", "", nil, 200},
		{"missing", "GET", "/card", "", datasetcard.ErrNotFound, 404},
		{"private error", "GET", "/card", "", errors.New("secret-provider-response"), 503},
		{"save conflict", "PUT", "/card", `{"expected_revision":1,"dataset_card":{"task":"image_classification","summary":"manual"}}`, datasetcard.ErrConflict, 409},
		{"invalid task", "PUT", "/card", `{"expected_revision":0,"dataset_card":{"task":"delete_dataset"}}`, nil, 422},
		{"negative revision", "PUT", "/card", `{"expected_revision":-1,"dataset_card":{"task":"image_classification"}}`, nil, 422},
		{"missing request id", "POST", "/card/generate", `{}`, nil, 422},
		{"no classes", "POST", "/card/generate", `{"request_id":"80000000-0000-4000-8000-000000000001"}`, nil, 422},
	} {
		t.Run(tc.name, func(t *testing.T) {
			router := NewRouter(Dependencies{DatasetCards: &datasetcard.Service{Repository: cardRepoStub{err: tc.err}}})
			request := httptest.NewRequest(tc.method, "/api/dataset-versions/test"+tc.path, bytes.NewBufferString(tc.body))
			request.Header.Set("Content-Type", "application/json")
			response := httptest.NewRecorder()
			router.ServeHTTP(response, request)
			if response.Code != tc.status {
				t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
			}
			if strings.Contains(response.Body.String(), "secret-provider-response") {
				t.Fatal("leaked internal error")
			}
		})
	}
}

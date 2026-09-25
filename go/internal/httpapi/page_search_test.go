package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

type pageSearchReadModels struct {
	emptyReadModels
	datasetFilter  PageFilter
	trainingFilter PageFilter
	searchQuery    string
}

func (stub *pageSearchReadModels) ListDatasetsPage(_ context.Context, filter PageFilter) ([]map[string]any, int, error) {
	stub.datasetFilter = filter
	return []map[string]any{{"id": "birds", "name": "CUB-200", "status": "ready"}}, 25, nil
}
func (stub *pageSearchReadModels) ListTrainingRunsPage(_ context.Context, filter PageFilter) ([]map[string]any, int, error) {
	stub.trainingFilter = filter
	return []map[string]any{{"id": "run-1", "status": "failed", "name": "Bird run"}}, 3, nil
}
func (stub *pageSearchReadModels) Search(_ context.Context, query string, _ int) ([]map[string]any, error) {
	stub.searchQuery = query
	return []map[string]any{{"kind": "dataset", "id": "birds", "label": "CUB-200", "hint": "数据集"}}, nil
}
func (stub *pageSearchReadModels) TrainingRunStatusCounts(context.Context) (map[string]int, error) {
	return map[string]int{"failed": 3, "succeeded": 2}, nil
}

func TestPagedListAndEntitySearchContracts(t *testing.T) {
	stub := &pageSearchReadModels{}
	router := NewRouter(Dependencies{ReadModels: stub})
	for _, test := range []struct {
		path  string
		total int
	}{
		{"/api/datasets?q=CUB&status=ready&limit=6&offset=12", 25},
		{"/api/training-runs?q=Bird&status=failed&dataset_id=birds&backbone_id=vit&limit=6&offset=6", 3},
	} {
		response := httptest.NewRecorder()
		router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, test.path, nil))
		if response.Code != http.StatusOK {
			t.Fatalf("%s: %d %s", test.path, response.Code, response.Body.String())
		}
		var payload struct {
			Pagination struct {
				Total  int `json:"total"`
				Limit  int `json:"limit"`
				Offset int `json:"offset"`
			} `json:"pagination"`
		}
		if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
			t.Fatal(err)
		}
		if payload.Pagination.Total != test.total || payload.Pagination.Limit != 6 {
			t.Fatalf("%s: %+v", test.path, payload.Pagination)
		}
	}
	if stub.datasetFilter.Query != "CUB" || stub.datasetFilter.Status != "ready" || stub.datasetFilter.Offset != 12 {
		t.Fatalf("dataset filter: %+v", stub.datasetFilter)
	}
	if stub.trainingFilter.DatasetID != "birds" || stub.trainingFilter.BackboneID != "vit" || stub.trainingFilter.Status != "failed" {
		t.Fatalf("training filter: %+v", stub.trainingFilter)
	}
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/api/search?q=CUB-200", nil))
	if response.Code != http.StatusOK || stub.searchQuery != "CUB-200" {
		t.Fatalf("search: %d %s", response.Code, response.Body.String())
	}
	response = httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/api/training-runs/summary", nil))
	if response.Code != http.StatusOK || !json.Valid(response.Body.Bytes()) {
		t.Fatalf("summary: %d %s", response.Code, response.Body.String())
	}
}

func TestPageBoundsAreSafeForDatabaseQueries(t *testing.T) {
	for _, test := range []struct {
		limit, offset         int
		wantLimit, wantOffset int
	}{
		{0, -1, 1, 0},
		{101, 7, 100, 7},
		{6, 12, 6, 12},
	} {
		limit, offset := normalizePageBounds(test.limit, test.offset)
		if limit != test.wantLimit || offset != test.wantOffset {
			t.Fatalf("normalizePageBounds(%d,%d)=(%d,%d)", test.limit, test.offset, limit, offset)
		}
	}
	stub := &pageSearchReadModels{}
	router := NewRouter(Dependencies{ReadModels: stub})
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/api/training-runs?limit=0&offset=-1", nil))
	if response.Code != http.StatusOK || stub.trainingFilter.Limit != 1 || stub.trainingFilter.Offset != 0 {
		t.Fatalf("unsafe page filter: %d %+v", response.Code, stub.trainingFilter)
	}
}

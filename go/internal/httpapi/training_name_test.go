package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestTrainingTaskName(t *testing.T) {
	for _, tc := range []struct {
		name   string
		status int
	}{{"  鸟类识别基线  ", 202}, {" ", 422}, {strings.Repeat("名", 81), 422}, {"任务\n换行", 422}} {
		body, _ := json.Marshal(map[string]any{"dataset_version_id": "version-1", "name": tc.name})
		req := httptest.NewRequest("POST", "/api/training-runs", bytes.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		response := httptest.NewRecorder()
		NewRouter(Dependencies{}).ServeHTTP(response, req)
		if response.Code != tc.status {
			t.Fatalf("status=%d want=%d: %s", response.Code, tc.status, response.Body.String())
		}
		if tc.status == 202 {
			var result struct {
				Run struct {
					Name string `json:"name"`
				} `json:"training_run"`
			}
			if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil || result.Run.Name != "鸟类识别基线" {
				t.Fatal("name round trip failed", err)
			}
		}
	}
}

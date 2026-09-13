package httpapi

import (
	"context"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/review"
	"net/http/httptest"
	"strings"
	"testing"
)

type reviewFake struct {
	filter    review.Filter
	submitted int
	item      map[string]any
	assisted  bool
}

func (f *reviewFake) List(_ context.Context, q review.Filter, _ bool) ([]map[string]any, int, error) {
	f.filter = q
	return []map[string]any{}, 0, nil
}
func (f *reviewFake) Get(context.Context, string) (map[string]any, error) {
	if f.item != nil {
		return f.item, nil
	}
	return nil, review.ErrNotFound
}
func (f *reviewFake) Submit(_ context.Context, _ string, s review.Submission) (map[string]any, error) {
	f.submitted++
	return nil, review.ErrConflict
}
func (f *reviewFake) SaveAssistance(context.Context, string, map[string]any) error {
	f.assisted = true
	return nil
}

func TestReviewAssistanceIsAdvisory(t *testing.T) {
	f := &reviewFake{item: map[string]any{"status": "pending", "context": map[string]any{"top_k": []any{}}}}
	router := NewRouter(Dependencies{Reviews: f, LLMApplication: llm.NewApplication(fakeLLMGateway{})})
	w := httptest.NewRecorder()
	router.ServeHTTP(w, httptest.NewRequest("POST", "/api/review-items/test/assist", strings.NewReader(`{"question":"检查哪些特征？"}`)))
	if w.Code != 200 || !f.assisted || f.submitted != 0 || f.item["status"] != "pending" {
		t.Fatalf("advisory changed review: %d %s", w.Code, w.Body.String())
	}
}
func TestReviewRoutesValidation(t *testing.T) {
	f := &reviewFake{}
	router := NewRouter(Dependencies{Reviews: f})
	for _, tc := range []struct {
		path   string
		status int
	}{{"/api/feedback-items?destination=all&limit=120", 200}, {"/api/review-items?status=pending&limit=6&offset=6", 200}, {"/api/feedback-items?destination=invalid", 422}, {"/api/review-items?limit=-1", 422}, {"/api/review-items/missing", 404}} {
		w := httptest.NewRecorder()
		router.ServeHTTP(w, httptest.NewRequest("GET", tc.path, nil))
		if w.Code != tc.status {
			t.Fatalf("%s: %d %s", tc.path, w.Code, w.Body.String())
		}
	}
	for _, body := range []string{`{"final_outcome":"ood","destination":"training_candidate"}`, `{"final_outcome":"corrected_label","destination":"training_candidate","final_label":" "}`, `{"final_outcome":"ignore","destination":"ignore"} {}`, `{"final_outcome":"ignore","destination":"ignore","extra":true}`} {
		w := httptest.NewRecorder()
		router.ServeHTTP(w, httptest.NewRequest("POST", "/api/review-items/test/submit", strings.NewReader(body)))
		if w.Code != 422 {
			t.Fatalf("bad input accepted: %d", w.Code)
		}
	}
	if f.submitted != 0 {
		t.Fatal("invalid submission reached repository")
	}
	w := httptest.NewRecorder()
	router.ServeHTTP(w, httptest.NewRequest("POST", "/api/review-items/test/submit", strings.NewReader(`{"final_outcome":"ignore","destination":"ignore"}`)))
	if w.Code != 409 {
		t.Fatalf("expected conflict, got %d", w.Code)
	}
}

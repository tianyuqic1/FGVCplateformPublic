package httpapi

import (
	"encoding/json"
	"errors"
	"github.com/go-chi/chi/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/auth"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/review"
	"google.golang.org/grpc/codes"
	grpcstatus "google.golang.org/grpc/status"
	"io"
	"log/slog"
	"net/http"
	"strconv"
)

func reviewResponse(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
func reviewError(w http.ResponseWriter, err error) {
	status, message := 500, "服务处理失败，请稍后重试"
	switch {
	case errors.Is(err, review.ErrInvalid):
		status, message = 422, err.Error()
	case errors.Is(err, review.ErrNotFound), errors.Is(err, dataset.ErrNotFound), errors.Is(err, modelregistry.ErrNotFound):
		status, message = 404, err.Error()
	case errors.Is(err, review.ErrConflict):
		status, message = 409, err.Error()
	case grpcstatus.Code(err) == codes.InvalidArgument:
		status, message = 422, "推理输入或模型契约不匹配，请检查图片与模型版本"
	case grpcstatus.Code(err) == codes.Unavailable:
		status, message = 503, "推理服务暂不可用，请稍后重试"
	case grpcstatus.Code(err) == codes.DeadlineExceeded:
		status, message = 504, "推理超时，请稍后重试"
	default:
		slog.Error("review request failed", "error", err)
	}
	reviewResponse(w, status, map[string]any{"detail": message})
}
func parseReviewFilter(r *http.Request, feedback bool) (review.Filter, error) {
	q := r.URL.Query()
	f := review.Filter{Status: q.Get("status"), Destination: q.Get("destination"), Dataset: q.Get("dataset_id"), Limit: 100}
	if f.Status == "all" {
		f.Status = ""
	}
	if f.Destination == "all" {
		f.Destination = ""
	}
	if !feedback && !member(f.Status, "", "pending", "submitted", "feedbacked", "skipped", "disputed") {
		return f, review.ErrInvalid
	}
	if feedback && !member(f.Destination, "", "training_candidate", "ood_stress", "bad_image", "taxonomy_dispute", "ignore") {
		return f, review.ErrInvalid
	}
	for key, target := range map[string]*int{"limit": &f.Limit, "offset": &f.Offset} {
		if q.Has(key) {
			n, err := strconv.Atoi(q.Get(key))
			if err != nil || n < 0 {
				return f, review.ErrInvalid
			}
			*target = n
		}
	}
	if f.Limit < 1 || f.Limit > 300 {
		return f, review.ErrInvalid
	}
	return f, nil
}
func member(s string, values ...string) bool {
	for _, v := range values {
		if s == v {
			return true
		}
	}
	return false
}
func registerReviews(router chi.Router, repo review.Repository, assistant *llm.Application) {
	available := func(w http.ResponseWriter) bool {
		if repo == nil {
			reviewResponse(w, 503, map[string]any{"detail": "复核服务未配置"})
			return false
		}
		return true
	}
	list := func(feedback bool) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			if !available(w) {
				return
			}
			filter, err := parseReviewFilter(r, feedback)
			if err != nil {
				reviewError(w, err)
				return
			}
			items, total, err := repo.List(r.Context(), filter, feedback)
			if err != nil {
				reviewError(w, err)
				return
			}
			key := "review_items"
			if feedback {
				key = "feedback_items"
			}
			var next any
			if filter.Offset+filter.Limit < total {
				next = filter.Offset + filter.Limit
			}
			reviewResponse(w, 200, map[string]any{key: items, "pagination": map[string]any{"total": total, "limit": filter.Limit, "offset": filter.Offset, "has_more": next != nil, "next_offset": next}})
		}
	}
	router.Get("/api/review-items", list(false))
	router.Get("/api/feedback-items", list(true))
	router.Get("/api/review-items/{review_id}", func(w http.ResponseWriter, r *http.Request) {
		if !available(w) {
			return
		}
		item, err := repo.Get(r.Context(), chi.URLParam(r, "review_id"))
		if err != nil {
			reviewError(w, err)
			return
		}
		reviewResponse(w, 200, map[string]any{"review_item": item})
	})
	router.Post("/api/review-items/{review_id}/submit", func(w http.ResponseWriter, r *http.Request) {
		if !available(w) {
			return
		}
		var input review.Submission
		decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16384))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&input); err != nil {
			reviewError(w, review.ErrInvalid)
			return
		}
		if err := decoder.Decode(new(any)); err != io.EOF {
			reviewError(w, review.ErrInvalid)
			return
		}
		if err := input.Validate(); err != nil {
			reviewError(w, err)
			return
		}
		input.Reviewer = auth.Actor(r.Context(), input.Reviewer)
		item, err := repo.Submit(r.Context(), chi.URLParam(r, "review_id"), input)
		if err != nil {
			reviewError(w, err)
			return
		}
		reviewResponse(w, 200, map[string]any{"review_item": item, "feedback_item": item["feedback"]})
	})
	router.Post("/api/review-items/{review_id}/assist", func(w http.ResponseWriter, r *http.Request) {
		if !available(w) {
			return
		}
		if assistant == nil {
			reviewResponse(w, 503, map[string]any{"detail": "AI 辅助未配置"})
			return
		}
		id := chi.URLParam(r, "review_id")
		item, err := repo.Get(r.Context(), id)
		if err != nil {
			reviewError(w, err)
			return
		}
		if item["status"] != "pending" {
			reviewError(w, review.ErrConflict)
			return
		}
		var input struct {
			Question string `json:"question"`
		}
		if err = json.NewDecoder(http.MaxBytesReader(w, r.Body, 8192)).Decode(&input); err != nil && err != io.EOF {
			reviewError(w, review.ErrInvalid)
			return
		}
		result, err := assistant.Assist(r.Context(), "review_assistance", map[string]any{"review": item, "question": input.Question})
		if err != nil {
			reviewResponse(w, 502, map[string]any{"detail": "AI 辅助调用失败，人工复核仍可使用"})
			return
		}
		if err = repo.SaveAssistance(r.Context(), id, result); err != nil {
			reviewError(w, err)
			return
		}
		item, err = repo.Get(r.Context(), id)
		if err != nil {
			reviewError(w, err)
			return
		}
		reviewResponse(w, 200, map[string]any{"assistance": result, "review_item": item})
	})
}

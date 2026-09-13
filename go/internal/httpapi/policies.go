package httpapi

import (
	"context"
	"encoding/json"
	"github.com/go-chi/chi/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/review"
	"io"
	"net/http"
)

type PolicyRepository interface {
	Policies(context.Context, map[string]string) ([]map[string]any, error)
	Shadow(context.Context, string, map[string]string) ([]map[string]any, error)
	PolicyAction(context.Context, string, string, map[string]any) (map[string]any, error)
}

func registerPolicies(router chi.Router, repo PolicyRepository) {
	available := func(w http.ResponseWriter) bool {
		if repo == nil {
			reviewResponse(w, 503, map[string]any{"detail": "策略服务未配置"})
			return false
		}
		return true
	}
	filters := func(r *http.Request) map[string]string {
		out := map[string]string{}
		for k, v := range r.URL.Query() {
			if len(v) > 0 {
				out[k] = v[0]
			}
		}
		return out
	}
	router.Get("/api/abstention-policies", func(w http.ResponseWriter, r *http.Request) {
		if !available(w) {
			return
		}
		rows, err := repo.Policies(r.Context(), filters(r))
		if err != nil {
			reviewError(w, err)
			return
		}
		reviewResponse(w, 200, map[string]any{"policies": rows})
	})
	router.Get("/api/abstention-policies/{policy_id}/shadow-decisions", func(w http.ResponseWriter, r *http.Request) {
		if !available(w) {
			return
		}
		rows, err := repo.Shadow(r.Context(), chi.URLParam(r, "policy_id"), filters(r))
		if err != nil {
			reviewError(w, err)
			return
		}
		reviewResponse(w, 200, map[string]any{"shadow_decisions": rows})
	})
	for _, action := range []string{"propose", "activate", "deactivate"} {
		path := "/api/abstention-policies/{policy_id}/" + action
		if action == "propose" {
			path = "/api/abstention-policies/propose"
		}
		router.Post(path, func(w http.ResponseWriter, r *http.Request) {
			if !available(w) {
				return
			}
			input := map[string]any{}
			decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16384))
			if err := decoder.Decode(&input); err != nil || input == nil {
				reviewError(w, review.ErrInvalid)
				return
			}
			if decoder.Decode(new(any)) != io.EOF {
				reviewError(w, review.ErrInvalid)
				return
			}
			row, err := repo.PolicyAction(r.Context(), chi.URLParam(r, "policy_id"), action, input)
			if err != nil {
				reviewError(w, err)
				return
			}
			reviewResponse(w, 200, map[string]any{"policy": row})
		})
	}
}

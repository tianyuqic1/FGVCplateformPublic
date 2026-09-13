package httpapi

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"io"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/deployment"
)

type DeploymentApplication interface {
	List(context.Context, string) ([]deployment.Record, error)
	RuntimeTargets() []deployment.Target
	Create(context.Context, string, deployment.Create) (deployment.Record, error)
	Retry(context.Context, string) (deployment.Record, error)
	Claim(context.Context, string, string, string, string, string) (deployment.Record, error)
	Heartbeat(context.Context, string, string, string) error
	Complete(context.Context, string, string, string, *artifact.Descriptor, map[string]any, string) error
}

func deploymentBody(w http.ResponseWriter, r *http.Request, v any) bool {
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 128<<10))
	dec.DisallowUnknownFields()
	if dec.Decode(v) != nil || dec.Decode(new(any)) != io.EOF {
		reviewResponse(w, 422, map[string]any{"detail": "部署参数无效"})
		return false
	}
	return true
}
func registerDeployments(router chi.Router, s DeploymentApplication, token string) {
	router.Route("/api/model-versions/{model_id}/deployments", func(r chi.Router) {
		r.Get("/", func(w http.ResponseWriter, r *http.Request) {
			if s == nil {
				reviewResponse(w, 503, map[string]any{"detail": "部署服务未配置"})
				return
			}
			rows, err := s.List(r.Context(), chi.URLParam(r, "model_id"))
			if err != nil {
				reviewError(w, err)
				return
			}
			reviewResponse(w, 200, map[string]any{"deployments": rows, "targets": s.RuntimeTargets()})
		})
		r.Post("/", func(w http.ResponseWriter, r *http.Request) {
			if s == nil {
				reviewResponse(w, 503, map[string]any{"detail": "部署服务未配置"})
				return
			}
			var input deployment.Create
			if !deploymentBody(w, r, &input) {
				return
			}
			row, err := s.Create(r.Context(), chi.URLParam(r, "model_id"), input)
			if err != nil {
				reviewError(w, err)
				return
			}
			reviewResponse(w, 202, map[string]any{"deployment": row})
		})
	})
	router.Post("/api/deployments/{id}/retry", func(w http.ResponseWriter, r *http.Request) {
		if s == nil {
			w.WriteHeader(503)
			return
		}
		if _, e := uuid.Parse(chi.URLParam(r, "id")); e != nil {
			w.WriteHeader(422)
			return
		}
		row, err := s.Retry(r.Context(), chi.URLParam(r, "id"))
		if err != nil {
			reviewError(w, err)
			return
		}
		reviewResponse(w, 202, map[string]any{"deployment": row})
	})
	router.Post("/api/internal/deployments/{id}/{action}", func(w http.ResponseWriter, r *http.Request) {
		if token == "" || !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") || subtle.ConstantTimeCompare([]byte(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")), []byte(token)) != 1 {
			w.WriteHeader(401)
			return
		}
		if s == nil {
			w.WriteHeader(503)
			return
		}
		id := chi.URLParam(r, "id")
		if _, err := uuid.Parse(id); err != nil {
			w.WriteHeader(422)
			return
		}
		var input struct {
			Token      string               `json:"build_token"`
			Worker     string               `json:"worker_id"`
			Runtime    string               `json:"runtime"`
			Profile    string               `json:"target_profile"`
			Artifact   *artifact.Descriptor `json:"artifact"`
			Validation map[string]any       `json:"validation"`
			Error      string               `json:"error"`
		}
		if !deploymentBody(w, r, &input) {
			return
		}
		if _, err := uuid.Parse(input.Token); err != nil {
			w.WriteHeader(422)
			return
		}
		var err error
		switch chi.URLParam(r, "action") {
		case "claim":
			var row deployment.Record
			row, err = s.Claim(r.Context(), id, input.Token, input.Worker, input.Runtime, input.Profile)
			if err == nil {
				reviewResponse(w, 200, map[string]any{"deployment": row})
				return
			}
		case "heartbeat":
			err = s.Heartbeat(r.Context(), id, input.Token, input.Worker)
		case "complete":
			err = s.Complete(r.Context(), id, input.Token, input.Worker, input.Artifact, input.Validation, input.Error)
		default:
			w.WriteHeader(404)
			return
		}
		if err != nil {
			reviewError(w, err)
			return
		}
		reviewResponse(w, 200, map[string]any{"ok": true})
	})
}

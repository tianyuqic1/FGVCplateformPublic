package httpapi

import (
	"context"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/tianyuqic1/FGVCplateformPublic/go/api/openapi"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/annotation"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/datasetcard"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/hardware"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/review"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
)

type Dependencies struct {
	Annotation      *annotation.Handler
	Deployments     DeploymentApplication
	DeploymentToken string
	Inference       InferenceApplication
	Policies        PolicyRepository
	Reviews         review.Repository
	Hardware        hardware.Handler
	DatasetCards    *datasetcard.Service
	DatasetImport   *dataset.Service
	DatasetQueue    *dataset.ImportQueue
	Lifecycle       *training.Service
	ReadModels      ReadModels
	LLMApplication  *llm.Application
	ModelRegistry   *modelregistry.Service
}

func NewRouter(dependencies Dependencies) http.Handler {
	if dependencies.Lifecycle == nil {
		dependencies.Lifecycle = training.NewService(training.NewMemoryRepository(), time.Now, 2*time.Minute)
	}
	if dependencies.ReadModels == nil {
		dependencies.ReadModels = emptyReadModels{}
	}
	if dependencies.ModelRegistry == nil {
		dependencies.ModelRegistry = modelregistry.NewService(modelregistry.NewMemoryRepository())
	}
	application := NewServer(dependencies.Lifecycle, dependencies.ReadModels, dependencies.LLMApplication, dependencies.ModelRegistry)
	application.datasets = dependencies.DatasetImport
	application.datasetQueue = dependencies.DatasetQueue
	application.uploadSlots = make(chan struct{}, 2)
	application.cards = dependencies.DatasetCards
	strict := openapi.NewStrictHandler(application, nil)
	router := chi.NewRouter()
	router.Use(middleware.RequestID)
	router.Use(middleware.RealIP)
	router.Use(middleware.Recoverer)
	router.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			limit := int64(512 << 20)
			if r.Method == http.MethodPost && r.URL.Path == "/api/datasets/upload-imagefolder" {
				// Allow multipart headers in addition to the validated image bytes.
				limit = dataset.MaxUploadBytes + (128 << 20)
			}
			if strings.HasPrefix(r.URL.Path, "/api/dataset-versions/") && (strings.HasSuffix(r.URL.Path, "/card") || strings.HasSuffix(r.URL.Path, "/card/generate")) {
				limit = 256 << 10
			}
			r.Body = http.MaxBytesReader(w, r.Body, limit)
			next.ServeHTTP(w, r)
		})
	})
	router.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			timeout := 30 * time.Second
			if r.Method == http.MethodPost && strings.HasPrefix(r.URL.Path, "/api/review-items/") && strings.HasSuffix(r.URL.Path, "/assist") {
				timeout = 115 * time.Second
			}
			if strings.HasPrefix(r.URL.Path, "/api/inference") {
				timeout = 120 * time.Second
				if strings.HasSuffix(r.URL.Path, "upload-folder") {
					timeout = 10 * time.Minute
				}
			}
			if r.Method == http.MethodPost && strings.HasPrefix(r.URL.Path, "/api/model-versions/") && strings.HasSuffix(r.URL.Path, "/promote") {
				timeout = 120 * time.Second
			}
			if r.Method == http.MethodPost && r.URL.Path == "/api/datasets/upload-imagefolder" {
				timeout = 30 * time.Minute
			}
			if r.Method == http.MethodPost && strings.HasPrefix(r.URL.Path, "/api/dataset-versions/") && strings.HasSuffix(r.URL.Path, "/card/generate") {
				timeout = 115 * time.Second
			}
			middleware.Timeout(timeout)(next).ServeHTTP(w, r)
		})
	})
	dependencies.Hardware.Register(router)
	dependencies.Annotation.Register(router)
	registerReviews(router, dependencies.Reviews, dependencies.LLMApplication)
	registerPolicies(router, dependencies.Policies)
	registerInference(router, dependencies.Inference)
	registerDeployments(router, dependencies.Deployments, dependencies.DeploymentToken)
	router.Delete("/api/training-runs/{run_id}", func(w http.ResponseWriter, r *http.Request) {
		service, ok := dependencies.ReadModels.(interface {
			DeleteTrainingRun(context.Context, string) error
		})
		if !ok {
			reviewResponse(w, 503, map[string]any{"detail": "训练记录删除未配置"})
			return
		}
		if err := service.DeleteTrainingRun(r.Context(), chi.URLParam(r, "run_id")); err != nil {
			reviewError(w, err)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})
	registerDatasetPreviews(router, dependencies.DatasetImport)
	return openapi.HandlerFromMux(strict, router)
}

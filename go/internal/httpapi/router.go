package httpapi

import (
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/tianyuqic1/FGVCplateformPublic/go/api/openapi"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/datasetcard"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
)

type Dependencies struct {
	DatasetCards   *datasetcard.Service
	DatasetImport  *dataset.Service
	Lifecycle      *training.Service
	ReadModels     ReadModels
	LLMApplication *llm.Application
	ModelRegistry  *modelregistry.Service
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
	return openapi.HandlerFromMux(strict, router)
}

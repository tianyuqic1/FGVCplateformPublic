package httpapi

import (
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/tianyuqic1/FGVCplateformPublic/go/api/openapi"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
)

type Dependencies struct {
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
	strict := openapi.NewStrictHandler(application, nil)
	router := chi.NewRouter()
	router.Use(middleware.RequestID)
	router.Use(middleware.RealIP)
	router.Use(middleware.Recoverer)
	router.Use(middleware.Timeout(30 * time.Second))
	return openapi.HandlerFromMux(strict, router)
}

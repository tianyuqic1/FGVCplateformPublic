package llmgateway

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
)

func NewRouter(provider llm.Gateway, internalToken string) http.Handler {
	router := chi.NewRouter()
	router.Use(middleware.RequestID, middleware.Recoverer)
	router.Get("/health", func(writer http.ResponseWriter, _ *http.Request) {
		writeJSON(writer, http.StatusOK, map[string]string{"status": "ok"})
	})
	router.Post("/internal/v1/generate", func(writer http.ResponseWriter, request *http.Request) {
		if !authorized(request.Header.Get("Authorization"), internalToken) {
			writeJSON(writer, http.StatusUnauthorized, map[string]any{"error": map[string]string{"code": "UNAUTHORIZED", "message": "internal credential is required"}})
			return
		}
		var command llm.GatewayRequest
		request.Body = http.MaxBytesReader(writer, request.Body, 12*1024*1024)
		if err := json.NewDecoder(request.Body).Decode(&command); err != nil || command.RequestID == "" || command.Task == "" || command.Prompt == "" {
			writeJSON(writer, http.StatusUnprocessableEntity, map[string]any{"error": map[string]string{"code": "VALIDATION_FAILED", "message": "request_id, task, and prompt are required"}})
			return
		}
		ctx, cancel := context.WithTimeout(request.Context(), 90*time.Second)
		defer cancel()
		// A timeout does not prove the provider did not charge the request.
		// Retry is explicit, with a new user-requested generation.
		result, err := provider.Generate(ctx, command)
		if err != nil {
			writeJSON(writer, http.StatusBadGateway, map[string]any{"error": map[string]string{"code": "LLM_PROVIDER_FAILED", "message": "configured LLM provider did not return a response"}})
			return
		}
		writeJSON(writer, http.StatusOK, map[string]any{"result": result})
	})
	return router
}

func authorized(header, expected string) bool {
	actual := strings.TrimPrefix(header, "Bearer ")
	if expected == "" || len(actual) != len(expected) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(actual), []byte(expected)) == 1
}

func writeJSON(writer http.ResponseWriter, status int, payload any) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(payload)
}

package llmgateway

import (
	"context"
	"encoding/json"
	"github.com/go-chi/chi/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/modelcontract"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/toolcontract"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
	"log/slog"
	"net/http"
	"time"
)

type chatProvider interface {
	Chat(context.Context, modelcontract.Request) (modelcontract.Response, error)
}

func registerAgent(router chi.Router, provider llm.Gateway, token string) {
	slots := make(chan struct{}, 8)
	router.Post("/internal/v2/chat", func(w http.ResponseWriter, r *http.Request) {
		if !authorized(r.Header.Get("Authorization"), token) {
			writeJSON(w, 401, map[string]string{"detail": "internal credential required"})
			return
		}
		chat, ok := provider.(chatProvider)
		if !ok {
			writeJSON(w, 503, map[string]string{"detail": "chat provider unavailable"})
			return
		}
		var in modelcontract.Request
		r.Body = http.MaxBytesReader(w, r.Body, 768<<10)
		d := json.NewDecoder(r.Body)
		d.DisallowUnknownFields()
		if d.Decode(&in) != nil || len(in.RequestID) > 120 || in.RequestID == "" || len(in.Messages) < 2 || len(in.Messages) > 100 || len(in.Tools) > len(toolcontract.Definitions()) {
			writeJSON(w, 422, map[string]string{"detail": "invalid chat request"})
			return
		}
		select {
		case slots <- struct{}{}:
			defer func() { <-slots }()
		default:
			writeJSON(w, 429, map[string]string{"detail": "model concurrency exhausted; request not dispatched"})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 100*time.Second)
		defer cancel()
		out, err := chat.Chat(ctx, in)
		if err != nil {
			slog.Warn("agent model request failed", "request_id", in.RequestID, "error", err)
			writeJSON(w, 502, map[string]string{"detail": "model response unavailable; dispatch outcome may be unknown"})
			return
		}
		writeJSON(w, 200, out)
	})
}

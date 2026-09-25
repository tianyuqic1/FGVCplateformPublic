package observabilityconsole

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/observability"
)

func (service *Service) Handler() http.Handler {
	router := chi.NewRouter()
	router.Use(observability.HTTPMiddleware)
	router.Use(service.authMiddleware)
	router.Handle("/metrics", observability.MetricsHandler())
	router.Get("/health", func(writer http.ResponseWriter, request *http.Request) {
		writeJSON(writer, http.StatusOK, map[string]any{"status": "ok", "runtime": "observability-console"})
	})
	router.Get("/api/observability/overview", func(writer http.ResponseWriter, request *http.Request) {
		writeJSON(writer, http.StatusOK, service.Overview(request.Context()))
	})
	router.Get("/api/observability/logs", func(writer http.ResponseWriter, request *http.Request) {
		filter := LogFilter{
			Category: request.URL.Query().Get("category"), Service: request.URL.Query().Get("service"),
			Level: request.URL.Query().Get("level"), Outcome: request.URL.Query().Get("outcome"), Search: request.URL.Query().Get("search"),
			Range: parseRange(request.URL.Query().Get("range")), Limit: parseLimit(request.URL.Query().Get("limit")),
		}
		if raw := request.URL.Query().Get("end"); raw != "" {
			if nanoseconds, err := strconv.ParseInt(raw, 10, 64); err == nil {
				filter.End = time.Unix(0, nanoseconds)
			}
		}
		page, err := service.Logs(request.Context(), filter)
		if err != nil {
			writeError(writer, http.StatusBadGateway, "LOG_BACKEND_UNAVAILABLE", "日志查询暂不可用")
			return
		}
		writeJSON(writer, http.StatusOK, page)
	})
	router.Get("/api/observability/alerts", func(writer http.ResponseWriter, request *http.Request) {
		alerts, err := service.Alerts(request.Context())
		if err != nil {
			writeError(writer, http.StatusBadGateway, "METRIC_BACKEND_UNAVAILABLE", "告警查询暂不可用")
			return
		}
		writeJSON(writer, http.StatusOK, map[string]any{"items": alerts})
	})
	router.Get("/api/observability/timeline", func(writer http.ResponseWriter, request *http.Request) {
		events, err := service.Timeline(request.Context(), request.URL.Query().Get("entity_type"), request.URL.Query().Get("entity_id"), parseRange(request.URL.Query().Get("range")))
		if err != nil && len(events) == 0 {
			writeError(writer, http.StatusUnprocessableEntity, "TIMELINE_UNAVAILABLE", "无法生成该实体的时间线")
			return
		}
		writeJSON(writer, http.StatusOK, map[string]any{"items": events, "partial": err != nil})
	})
	router.Get("/api/observability/traces/{trace_id}", func(writer http.ResponseWriter, request *http.Request) {
		trace, err := service.Trace(request.Context(), chi.URLParam(request, "trace_id"))
		if err != nil {
			writeError(writer, http.StatusBadGateway, "TRACE_UNAVAILABLE", "调用链查询暂不可用")
			return
		}
		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(http.StatusOK)
		_, _ = writer.Write(trace)
	})
	router.Handle("/*", service.uiHandler())
	return router
}

func parseRange(value string) time.Duration {
	switch strings.TrimSpace(value) {
	case "15m":
		return 15 * time.Minute
	case "6h":
		return 6 * time.Hour
	case "24h":
		return 24 * time.Hour
	case "7d":
		return 7 * 24 * time.Hour
	default:
		return time.Hour
	}
}

func parseLimit(value string) int {
	parsed, _ := strconv.Atoi(value)
	if parsed < 1 {
		return 50
	}
	if parsed > 200 {
		return 200
	}
	return parsed
}

func writeJSON(writer http.ResponseWriter, status int, value any) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(value)
}

func writeError(writer http.ResponseWriter, status int, code, message string) {
	writeJSON(writer, status, map[string]any{"error": map[string]any{"code": code, "message": message}})
}

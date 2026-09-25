package observability

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
	"unicode"

	"go.opentelemetry.io/otel/trace"

	"github.com/go-chi/chi/v5"
	chimiddleware "github.com/go-chi/chi/v5/middleware"
	"github.com/google/uuid"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/status"
)

const RedactedValue = "[REDACTED]"

type Config struct {
	Service        string
	ServiceVersion string
	Environment    string
	Writer         io.Writer
	Level          slog.Level
	Format         string
}

type contextKey string

const requestIDKey contextKey = "finevision.request_id"

var (
	httpRequests = prometheus.NewCounterVec(prometheus.CounterOpts{
		Namespace: "finevision", Subsystem: "http", Name: "requests_total",
		Help: "Total number of HTTP requests handled by FineVision.",
	}, []string{"method", "route", "status"})
	httpDuration = prometheus.NewHistogramVec(prometheus.HistogramOpts{
		Namespace: "finevision", Subsystem: "http", Name: "request_duration_seconds",
		Help:    "FineVision HTTP request duration in seconds.",
		Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30},
	}, []string{"method", "route"})
	httpInflight = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Namespace: "finevision", Subsystem: "http", Name: "inflight",
		Help: "Current FineVision HTTP requests by method.",
	}, []string{"method"})
	grpcRequests = prometheus.NewCounterVec(prometheus.CounterOpts{
		Namespace: "finevision", Subsystem: "grpc", Name: "requests_total",
		Help: "Total FineVision unary gRPC calls.",
	}, []string{"direction", "method", "code"})
	grpcDuration = prometheus.NewHistogramVec(prometheus.HistogramOpts{
		Namespace: "finevision", Subsystem: "grpc", Name: "request_duration_seconds",
		Help:    "FineVision unary gRPC call duration.",
		Buckets: []float64{.001, .005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30},
	}, []string{"direction", "method"})
	grpcInflight = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Namespace: "finevision", Subsystem: "grpc", Name: "inflight",
		Help: "Current FineVision unary gRPC calls.",
	}, []string{"direction", "method"})
	outboxPublished = prometheus.NewCounterVec(prometheus.CounterOpts{
		Namespace: "finevision", Subsystem: "outbox", Name: "published_total", Help: "Published outbox events.",
	}, []string{"relay"})
	outboxFailures = prometheus.NewCounterVec(prometheus.CounterOpts{
		Namespace: "finevision", Subsystem: "outbox", Name: "publish_failures_total", Help: "Outbox publish failures.",
	}, []string{"relay"})
	outboxPending = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Namespace: "finevision", Subsystem: "outbox", Name: "pending", Help: "Pending outbox events.",
	}, []string{"relay"})
	outboxOldestAge = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Namespace: "finevision", Subsystem: "outbox", Name: "oldest_age_seconds", Help: "Age of the oldest pending outbox event.",
	}, []string{"relay"})
	llmRequests = prometheus.NewCounterVec(prometheus.CounterOpts{
		Namespace: "finevision", Subsystem: "llm", Name: "requests_total", Help: "LLM provider requests by bounded outcome.",
	}, []string{"provider", "outcome"})
	llmDuration = prometheus.NewHistogramVec(prometheus.HistogramOpts{
		Namespace: "finevision", Subsystem: "llm", Name: "request_duration_seconds", Help: "LLM provider request latency.",
		Buckets: []float64{.1, .25, .5, 1, 2.5, 5, 10, 30, 60, 90},
	}, []string{"provider", "outcome"})
	llmTokens = prometheus.NewCounterVec(prometheus.CounterOpts{
		Namespace: "finevision", Subsystem: "llm", Name: "tokens_total", Help: "LLM provider token usage.",
	}, []string{"provider", "direction"})
)

func init() {
	prometheus.MustRegister(httpRequests, httpDuration, httpInflight, grpcRequests, grpcDuration, grpcInflight, outboxPublished, outboxFailures, outboxPending, outboxOldestAge, llmRequests, llmDuration, llmTokens)
}

func ConfigFromEnv(service string) Config {
	level := slog.LevelInfo
	switch strings.ToUpper(strings.TrimSpace(os.Getenv("FINEVISION_LOG_LEVEL"))) {
	case "DEBUG":
		level = slog.LevelDebug
	case "WARN", "WARNING":
		level = slog.LevelWarn
	case "ERROR":
		level = slog.LevelError
	}
	return Config{
		Service: service, ServiceVersion: valueOr(os.Getenv("FINEVISION_SERVICE_VERSION"), "dev"),
		Environment: valueOr(os.Getenv("FINEVISION_ENVIRONMENT"), "local"), Writer: os.Stdout,
		Level: level, Format: valueOr(os.Getenv("FINEVISION_LOG_FORMAT"), "json"),
	}
}

func Bootstrap(service string) *slog.Logger {
	logger := NewLogger(ConfigFromEnv(service))
	slog.SetDefault(logger)
	return logger
}

func NewLogger(config Config) *slog.Logger {
	if config.Writer == nil {
		config.Writer = os.Stdout
	}
	if config.Service == "" {
		config.Service = "finevision"
	}
	if config.Environment == "" {
		config.Environment = "local"
	}
	options := &slog.HandlerOptions{Level: config.Level, AddSource: false, ReplaceAttr: replaceBuiltins}
	var handler slog.Handler
	if strings.EqualFold(config.Format, "text") {
		handler = slog.NewTextHandler(config.Writer, options)
	} else {
		handler = slog.NewJSONHandler(config.Writer, options)
	}
	handler = &redactingHandler{next: handler}
	return slog.New(handler).With(
		"service", config.Service,
		"service_version", valueOr(config.ServiceVersion, "dev"),
		"environment", config.Environment,
	)
}

func WithRequestID(ctx context.Context, requestID string) context.Context {
	return context.WithValue(ctx, requestIDKey, requestID)
}

func RequestID(ctx context.Context) string {
	if value, ok := ctx.Value(requestIDKey).(string); ok {
		return value
	}
	return ""
}

func HTTPMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		started := time.Now()
		httpInflight.WithLabelValues(request.Method).Inc()
		defer httpInflight.WithLabelValues(request.Method).Dec()
		requestID := validRequestID(chimiddleware.GetReqID(request.Context()))
		if requestID == "" {
			requestID = validRequestID(request.Header.Get("X-Request-ID"))
		}
		if requestID == "" {
			requestID = uuid.NewString()
		}
		ctx := WithRequestID(request.Context(), requestID)
		request = request.WithContext(ctx)
		writer.Header().Set("X-Request-ID", requestID)
		wrapped := chimiddleware.NewWrapResponseWriter(writer, request.ProtoMajor)
		next.ServeHTTP(wrapped, request)

		route := "unmatched"
		if routeContext := chi.RouteContext(request.Context()); routeContext != nil {
			if pattern := routeContext.RoutePattern(); pattern != "" {
				route = pattern
			}
		}
		status := wrapped.Status()
		if status == 0 {
			status = http.StatusOK
		}
		outcome, level := "success", slog.LevelInfo
		if status >= 500 {
			outcome, level = "failed", slog.LevelError
		} else if status >= 400 {
			outcome, level = "rejected", slog.LevelWarn
		}
		duration := time.Since(started)
		httpRequests.WithLabelValues(request.Method, route, strconv.Itoa(status)).Inc()
		httpDuration.WithLabelValues(request.Method, route).Observe(duration.Seconds())
		if route != "/api/health" || status >= 400 {
			slog.Log(ctx, level, "HTTP request completed",
				"event", "http_request_completed", "request_id", requestID,
				"method", request.Method, "route", route, "status", status,
				"response_bytes", wrapped.BytesWritten(), "duration_ms", duration.Milliseconds(), "outcome", outcome)
		}
	})
}

func UnaryServerMetricsInterceptor() grpc.UnaryServerInterceptor {
	return func(ctx context.Context, request any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		started := time.Now()
		method := boundedRPCMethod(info.FullMethod)
		grpcInflight.WithLabelValues("server", method).Inc()
		defer grpcInflight.WithLabelValues("server", method).Dec()
		response, err := handler(ctx, request)
		recordGRPC("server", method, err, time.Since(started))
		return response, err
	}
}

func UnaryClientMetricsInterceptor() grpc.UnaryClientInterceptor {
	return func(ctx context.Context, method string, request, reply any, connection *grpc.ClientConn, invoker grpc.UnaryInvoker, options ...grpc.CallOption) error {
		started := time.Now()
		metricMethod := boundedRPCMethod(method)
		grpcInflight.WithLabelValues("client", metricMethod).Inc()
		defer grpcInflight.WithLabelValues("client", metricMethod).Dec()
		err := invoker(ctx, method, request, reply, connection, options...)
		recordGRPC("client", metricMethod, err, time.Since(started))
		return err
	}
}

func recordGRPC(direction, method string, err error, duration time.Duration) {
	grpcRequests.WithLabelValues(direction, method, status.Code(err).String()).Inc()
	grpcDuration.WithLabelValues(direction, method).Observe(duration.Seconds())
}

func boundedRPCMethod(method string) string {
	if method == "" || len(method) > 256 || !strings.HasPrefix(method, "/") {
		return "unknown"
	}
	return method
}

func MetricsHandler() http.Handler { return promhttp.Handler() }

func StartMetricsServer(address string) {
	if strings.TrimSpace(address) == "" {
		return
	}
	go func() {
		server := &http.Server{Addr: address, Handler: MetricsHandler(), ReadHeaderTimeout: 5 * time.Second}
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Warn("metrics server stopped", "error", err)
		}
	}()
}

func RecordOutboxBatch(relay string, published int, err error) {
	if published > 0 {
		outboxPublished.WithLabelValues(relay).Add(float64(published))
	}
	if err != nil {
		outboxFailures.WithLabelValues(relay).Inc()
	}
}

func SetOutboxBacklog(relay string, pending int64, oldestAgeSeconds float64) {
	outboxPending.WithLabelValues(relay).Set(float64(pending))
	outboxOldestAge.WithLabelValues(relay).Set(max(0, oldestAgeSeconds))
}

func RecordLLM(provider, outcome string, duration time.Duration, inputTokens, outputTokens int) {
	llmRequests.WithLabelValues(provider, outcome).Inc()
	llmDuration.WithLabelValues(provider, outcome).Observe(duration.Seconds())
	if inputTokens > 0 {
		llmTokens.WithLabelValues(provider, "input").Add(float64(inputTokens))
	}
	if outputTokens > 0 {
		llmTokens.WithLabelValues(provider, "output").Add(float64(outputTokens))
	}
}

type redactingHandler struct{ next slog.Handler }

func (handler *redactingHandler) Enabled(ctx context.Context, level slog.Level) bool {
	return handler.next.Enabled(ctx, level)
}

func (handler *redactingHandler) Handle(ctx context.Context, record slog.Record) error {
	copy := slog.NewRecord(record.Time, record.Level, record.Message, record.PC)
	if span := trace.SpanFromContext(ctx).SpanContext(); span.IsValid() {
		copy.AddAttrs(slog.String("trace_id", span.TraceID().String()), slog.String("span_id", span.SpanID().String()))
	}
	record.Attrs(func(attr slog.Attr) bool {
		copy.AddAttrs(sanitizeAttr(attr))
		return true
	})
	return handler.next.Handle(ctx, copy)
}

func (handler *redactingHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	clean := make([]slog.Attr, 0, len(attrs))
	for _, attr := range attrs {
		clean = append(clean, sanitizeAttr(attr))
	}
	return &redactingHandler{next: handler.next.WithAttrs(clean)}
}

func (handler *redactingHandler) WithGroup(name string) slog.Handler {
	return &redactingHandler{next: handler.next.WithGroup(name)}
}

func sanitizeAttr(attr slog.Attr) slog.Attr {
	attr.Value = attr.Value.Resolve()
	if sensitiveKey(attr.Key) {
		return slog.String(attr.Key, RedactedValue)
	}
	if attr.Value.Kind() == slog.KindGroup {
		children := attr.Value.Group()
		clean := make([]any, 0, len(children))
		for _, child := range children {
			clean = append(clean, sanitizeAttr(child))
		}
		return slog.Group(attr.Key, clean...)
	}
	if attr.Value.Kind() == slog.KindString {
		return slog.String(attr.Key, sanitizeString(attr.Value.String()))
	}
	if attr.Value.Kind() == slog.KindAny {
		return slog.Any(attr.Key, sanitizeAny(attr.Key, attr.Value.Any()))
	}
	return attr
}

func sanitizeAny(key string, value any) any {
	switch typed := value.(type) {
	case nil, bool, float32, float64, int, int8, int16, int32, int64, uint, uint8, uint16, uint32, uint64:
		return typed
	case string:
		return sanitizeString(typed)
	case json.Number:
		return typed
	case error:
		return sanitizeString(typed.Error())
	case map[string]any:
		clean := make(map[string]any, len(typed))
		for childKey, childValue := range typed {
			if sensitiveKey(childKey) {
				clean[childKey] = RedactedValue
			} else {
				clean[childKey] = sanitizeAny(childKey, childValue)
			}
		}
		return clean
	case []any:
		clean := make([]any, len(typed))
		for index, child := range typed {
			clean[index] = sanitizeAny(key, child)
		}
		return clean
	default:
		raw, err := json.Marshal(value)
		if err == nil {
			var normalized any
			if json.Unmarshal(raw, &normalized) == nil {
				return sanitizeAny(key, normalized)
			}
		}
		return sanitizeString(fmt.Sprint(value))
	}
}

func sensitiveKey(key string) bool {
	key = strings.ToLower(strings.Map(func(r rune) rune {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			return r
		}
		return '_'
	}, key))
	for _, part := range []string{"authorization", "api_key", "apikey", "password", "secret", "access_token", "refresh_token", "database_url", "image_data", "data_url", "prompt", "request_body", "response_body"} {
		if key == part || strings.HasSuffix(key, "_"+part) {
			return true
		}
	}
	return key == "image"
}

func sanitizeString(value string) string {
	trimmed := strings.TrimSpace(value)
	lower := strings.ToLower(trimmed)
	if strings.HasPrefix(lower, "data:image/") || strings.HasPrefix(lower, "bearer ") || strings.HasPrefix(lower, "sk-") {
		return RedactedValue
	}
	const maxFieldBytes = 4096
	if len(value) > maxFieldBytes {
		return value[:maxFieldBytes] + "...[TRUNCATED]"
	}
	return value
}

func replaceBuiltins(_ []string, attr slog.Attr) slog.Attr {
	switch attr.Key {
	case slog.TimeKey:
		attr.Key = "timestamp"
	case slog.LevelKey:
		attr.Key = "level"
	case slog.MessageKey:
		attr.Key = "message"
	}
	return attr
}

func validRequestID(value string) string {
	if value == "" || len(value) > 128 {
		return ""
	}
	for _, r := range value {
		if !(unicode.IsLetter(r) || unicode.IsDigit(r) || strings.ContainsRune("-_.:", r)) {
			return ""
		}
	}
	return value
}

func valueOr(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

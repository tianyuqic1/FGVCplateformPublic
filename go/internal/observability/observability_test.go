package observability

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestStructuredLoggerRedactsSecretsAndLargeImagePayloads(t *testing.T) {
	var output bytes.Buffer
	logger := NewLogger(Config{Service: "test-service", Environment: "test", Writer: &output, Level: slog.LevelDebug})
	logger.Info("provider request", "event", "provider_request", "authorization", "Bearer secret-token", "image", "data:image/png;base64,AAAA", "safe", "visible")

	var record map[string]any
	if err := json.Unmarshal(output.Bytes(), &record); err != nil {
		t.Fatalf("decode JSON log: %v; output=%q", err, output.String())
	}
	if record["service"] != "test-service" || record["environment"] != "test" || record["event"] != "provider_request" {
		t.Fatalf("record identity = %#v", record)
	}
	if record["authorization"] != RedactedValue || record["image"] != RedactedValue {
		t.Fatalf("sensitive values were not redacted: %#v", record)
	}
	if record["safe"] != "visible" {
		t.Fatalf("safe field = %#v", record["safe"])
	}
}

func TestStructuredLoggerRecursivelyRedactsAnyPayloads(t *testing.T) {
	var output bytes.Buffer
	logger := NewLogger(Config{Service: "test-service", Environment: "test", Writer: &output})
	secret := "sk-" + "test-should-never-appear"
	logger.Info("nested payload", "payload", map[string]any{
		"safe": "visible",
		"provider": map[string]any{
			"api_key": secret,
			"prompt":  "private prompt",
		},
	})

	if strings.Contains(output.String(), secret) || strings.Contains(output.String(), "private prompt") {
		t.Fatalf("nested sensitive values leaked: %s", output.String())
	}
	if !strings.Contains(output.String(), RedactedValue) || !strings.Contains(output.String(), "visible") {
		t.Fatalf("nested payload was not sanitized: %s", output.String())
	}
}

func TestHTTPMiddlewareReturnsDiagnosticIDAndRecordsRouteOutcome(t *testing.T) {
	var output bytes.Buffer
	previous := slog.Default()
	slog.SetDefault(NewLogger(Config{Service: "test-control-plane", Environment: "test", Writer: &output}))
	t.Cleanup(func() { slog.SetDefault(previous) })

	router := chi.NewRouter()
	router.Use(HTTPMiddleware)
	router.Get("/widgets/{widgetID}", func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusCreated)
		_, _ = writer.Write([]byte("ok"))
	})

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/widgets/123", nil)
	router.ServeHTTP(recorder, request)

	if recorder.Header().Get("X-Request-ID") == "" {
		t.Fatal("X-Request-ID was not returned")
	}
	if !strings.Contains(output.String(), `"route":"/widgets/{widgetID}"`) ||
		!strings.Contains(output.String(), `"status":201`) ||
		!strings.Contains(output.String(), `"outcome":"success"`) {
		t.Fatalf("access log = %s", output.String())
	}
}

func TestMetricsExposeBoundedHTTPSeries(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "/health", nil)
	recorder := httptest.NewRecorder()
	HTTPMiddleware(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.WriteHeader(http.StatusNoContent)
	})).ServeHTTP(recorder, request)

	metrics := httptest.NewRecorder()
	MetricsHandler().ServeHTTP(metrics, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if !strings.Contains(metrics.Body.String(), "finevision_http_requests_total") {
		t.Fatalf("metrics output did not expose HTTP counter: %s", metrics.Body.String())
	}
}

func TestGRPCMetricsExposeOnlyContractMethodAndCode(t *testing.T) {
	method := "/finevision.test.v1.Runtime/Predict"
	err := UnaryClientMetricsInterceptor()(context.Background(), method, nil, nil, nil,
		func(context.Context, string, any, any, *grpc.ClientConn, ...grpc.CallOption) error {
			return status.Error(codes.Unavailable, "offline")
		})
	if status.Code(err) != codes.Unavailable {
		t.Fatalf("code = %v", status.Code(err))
	}

	metrics := httptest.NewRecorder()
	MetricsHandler().ServeHTTP(metrics, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	output := metrics.Body.String()
	if !strings.Contains(output, `finevision_grpc_requests_total{code="Unavailable",direction="client",method="/finevision.test.v1.Runtime/Predict"} 1`) {
		t.Fatalf("gRPC metric missing: %s", output)
	}
}

func TestTraceSampleRatioAcceptsBoundedFraction(t *testing.T) {
	t.Setenv("OTEL_TRACES_SAMPLER_ARG", "0.25")
	if ratio := traceSampleRatio(); ratio != 0.25 {
		t.Fatalf("ratio = %v, want 0.25", ratio)
	}
	t.Setenv("OTEL_TRACES_SAMPLER_ARG", "4")
	if ratio := traceSampleRatio(); ratio != 1 {
		t.Fatalf("clamped ratio = %v, want 1", ratio)
	}
}

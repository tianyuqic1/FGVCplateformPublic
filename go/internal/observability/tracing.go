package observability

import (
	"context"
	"os"
	"strconv"
	"strings"

	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	tracesdk "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.37.0"
	"net/http"
)

func SetupTracing(ctx context.Context, service string) (func(context.Context) error, error) {
	propagator := propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{})
	otel.SetTextMapPropagator(propagator)
	if !envEnabled("FINEVISION_OBSERVABILITY_ENABLED") {
		return func(context.Context) error { return nil }, nil
	}
	endpoint := valueOr(os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT"), "http://alloy:4317")
	exporter, err := otlptracegrpc.New(ctx, otlptracegrpc.WithEndpointURL(endpoint))
	if err != nil {
		return nil, err
	}
	res, err := resource.Merge(resource.Default(), resource.NewWithAttributes(
		semconv.SchemaURL,
		semconv.ServiceName(service),
		semconv.ServiceVersion(valueOr(os.Getenv("FINEVISION_SERVICE_VERSION"), "dev")),
		semconv.DeploymentEnvironmentName(valueOr(os.Getenv("FINEVISION_ENVIRONMENT"), "local")),
	))
	if err != nil {
		return nil, err
	}
	provider := tracesdk.NewTracerProvider(
		tracesdk.WithBatcher(exporter),
		tracesdk.WithResource(res),
		tracesdk.WithSampler(tracesdk.ParentBased(tracesdk.TraceIDRatioBased(traceSampleRatio()))),
	)
	otel.SetTracerProvider(provider)
	return provider.Shutdown, nil
}

func TraceHTTPHandler(handler http.Handler, operation string) http.Handler {
	if !envEnabled("FINEVISION_OBSERVABILITY_ENABLED") {
		return handler
	}
	return otelhttp.NewHandler(handler, operation)
}

func TracePublicHTTPHandler(handler http.Handler, operation string) http.Handler {
	if !envEnabled("FINEVISION_OBSERVABILITY_ENABLED") {
		return handler
	}
	return otelhttp.NewHandler(handler, operation, otelhttp.WithPublicEndpointFn(func(*http.Request) bool { return true }))
}

func TraceHTTPTransport(base http.RoundTripper) http.RoundTripper {
	if base == nil {
		base = http.DefaultTransport
	}
	if !envEnabled("FINEVISION_OBSERVABILITY_ENABLED") {
		return base
	}
	return otelhttp.NewTransport(base)
}

func envEnabled(key string) bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(key))) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func traceSampleRatio() float64 {
	value := strings.TrimSpace(os.Getenv("OTEL_TRACES_SAMPLER_ARG"))
	if value == "" {
		return 0.1
	}
	ratio, err := strconv.ParseFloat(value, 64)
	if err != nil {
		return 0.1
	}
	if ratio < 0 {
		return 0
	}
	if ratio > 1 {
		return 1
	}
	return ratio
}

package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/llmprovider"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/config"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llmgateway"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/observability"
)

func main() {
	observability.Bootstrap("go-llm-gateway")
	shutdownTracing, traceErr := observability.SetupTracing(context.Background(), "go-llm-gateway")
	if traceErr != nil {
		slog.Warn("tracing exporter unavailable; continuing without export", "error", traceErr)
	} else {
		defer func() {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = shutdownTracing(ctx)
		}()
	}
	configuration, err := config.LoadLLMGateway()
	if err != nil {
		slog.Error("invalid LLM Gateway configuration", "error", err)
		os.Exit(1)
	}
	client := &http.Client{Timeout: configuration.Timeout, Transport: observability.TraceHTTPTransport(nil)}
	provider := llmprovider.NewOpenAICompatible(
		configuration.ProviderURL, configuration.Model, configuration.APIKey, client,
	)
	router := http.NewServeMux()
	router.Handle("/metrics", observability.MetricsHandler())
	router.Handle("/", observability.TraceHTTPHandler(observability.HTTPMiddleware(llmgateway.NewRouter(provider, configuration.InternalToken)), "finevision.llm.http"))
	server := &http.Server{
		Addr:              configuration.HTTPAddress,
		Handler:           router,
		ReadHeaderTimeout: configuration.Timeout,
	}
	slog.Info("starting FineVision LLM Gateway", "address", configuration.HTTPAddress, "provider", configuration.ProviderName, "model", configuration.Model)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("LLM Gateway stopped", "error", err)
		os.Exit(1)
	}
}

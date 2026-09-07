package main

import (
	"errors"
	"log/slog"
	"net/http"
	"os"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/llmprovider"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/config"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llmgateway"
)

func main() {
	configuration, err := config.LoadLLMGateway()
	if err != nil {
		slog.Error("invalid LLM Gateway configuration", "error", err)
		os.Exit(1)
	}
	client := &http.Client{Timeout: configuration.Timeout}
	provider := llmprovider.NewOpenAICompatible(
		configuration.ProviderURL, configuration.Model, configuration.APIKey, client,
	)
	server := &http.Server{
		Addr:              configuration.HTTPAddress,
		Handler:           llmgateway.NewRouter(provider, configuration.InternalToken),
		ReadHeaderTimeout: configuration.Timeout,
	}
	slog.Info("starting FineVision LLM Gateway", "address", configuration.HTTPAddress, "provider", configuration.ProviderName, "model", configuration.Model)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("LLM Gateway stopped", "error", err)
		os.Exit(1)
	}
}

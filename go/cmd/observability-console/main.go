package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/observability"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/observabilityconsole"
)

func main() {
	observability.Bootstrap("observability-console")
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	shutdownTracing, err := observability.SetupTracing(ctx, "observability-console")
	if err == nil {
		defer func() {
			shutdown, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			_ = shutdownTracing(shutdown)
		}()
	}
	var pool *pgxpool.Pool
	if databaseURL := os.Getenv("FINEVISION_DATABASE_URL"); databaseURL != "" {
		pool, err = pgxpool.New(ctx, databaseURL)
		if err != nil {
			slog.Warn("audit database configuration unavailable", "event", "audit_database_unavailable", "error", err)
		}
		if pool != nil {
			defer pool.Close()
		}
	}
	service := observabilityconsole.New(pool, observabilityconsole.Config{
		LokiURL: os.Getenv("FINEVISION_LOKI_URL"), PrometheusURL: os.Getenv("FINEVISION_PROMETHEUS_URL"), TempoURL: os.Getenv("FINEVISION_TEMPO_URL"), AccessToken: os.Getenv("FINEVISION_OBSERVABILITY_TOKEN"),
	})
	handler := observability.TraceHTTPHandler(service.Handler(), "finevision.observability.console")
	server := &http.Server{Addr: valueOr(os.Getenv("FINEVISION_OBSERVABILITY_CONSOLE_ADDRESS"), "127.0.0.1:9400"), Handler: handler, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
	}()
	slog.Info("starting FineVision observability console", "event", "observability_console_starting", "address", server.Addr)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("observability console stopped", "event", "observability_console_stopped", "error", err)
		os.Exit(1)
	}
}

func valueOr(value, fallback string) string {
	if value == "" {
		return fallback
	}
	return value
}

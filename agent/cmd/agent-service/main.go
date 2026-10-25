package main

import (
	"context"
	"errors"
	"github.com/jackc/pgx/v5/pgxpool"
	modeladapter "github.com/tianyuqic1/FGVCplateformPublic/agent/internal/adapters/model"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/adapters/platform"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/httpapi"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/runner"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/storage"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/tools"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	if err := run(); err != nil {
		slog.Error("agent service stopped", "error", err)
		os.Exit(1)
	}
}
func run() error {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	key := []byte(os.Getenv("FINEVISION_AGENT_DELEGATION_KEY"))
	if len(key) < 32 {
		return errors.New("FINEVISION_AGENT_DELEGATION_KEY needs at least 32 bytes")
	}
	config, e := pgxpool.ParseConfig(os.Getenv("FINEVISION_AGENT_DATABASE_URL"))
	if e != nil {
		return errors.New("invalid agent database configuration")
	}
	config.MaxConns = 16
	config.ConnConfig.ConnectTimeout = 5 * time.Second
	pool, e := pgxpool.NewWithConfig(ctx, config)
	if e != nil {
		return e
	}
	defer pool.Close()
	store := &storage.Store{Pool: pool}
	migration, c := context.WithTimeout(ctx, 30*time.Second)
	e = store.Migrate(migration)
	c()
	if e != nil {
		return errors.New("agent database migration failed")
	}
	platformClient, e := platform.New(os.Getenv("FINEVISION_CONTROL_PLANE_HTTP"), nil)
	if e != nil {
		return e
	}
	registry, e := tools.New(platformClient, 16, 4)
	if e != nil {
		return e
	}
	httpClient := &http.Client{Timeout: 110 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	model := &modeladapter.Client{URL: os.Getenv("FINEVISION_LLM_GATEWAY_URL"), Token: os.Getenv("FINEVISION_LLM_INTERNAL_TOKEN"), HTTP: httpClient}
	worker := &runner.Runner{Store: store, Platform: platformClient, Registry: registry, Model: model, Key: key}
	wg := worker.Start(ctx, 4)
	wg.Add(1)
	go func() { defer wg.Done(); worker.WatchLoop(ctx) }()
	api := &httpapi.Server{Store: store, Platform: platformClient, Key: key}
	address := os.Getenv("FINEVISION_AGENT_HTTP_ADDRESS")
	if address == "" {
		address = ":8091"
	}
	server := &http.Server{Addr: address, Handler: api.Handler(), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16 << 10}
	failed := make(chan error, 1)
	go func() { slog.Info("agent listening", "address", address); failed <- server.ListenAndServe() }()
	select {
	case <-ctx.Done():
	case e = <-failed:
		stop()
	}
	shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = server.Shutdown(shutdown)
	wg.Wait()
	if e != nil && !errors.Is(e, http.ErrServerClosed) {
		return e
	}
	return nil
}

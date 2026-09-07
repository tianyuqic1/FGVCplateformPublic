package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	amqp "github.com/rabbitmq/amqp091-go"
	postgresadapter "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/postgres"
	rabbitadapter "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/rabbitmq"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/config"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/outbox"
)

func main() {
	configuration, err := config.LoadOutboxRelay()
	if err != nil {
		slog.Error("invalid outbox relay configuration", "error", err)
		os.Exit(1)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	pool, err := pgxpool.New(ctx, configuration.DatabaseURL)
	if err != nil {
		slog.Error("connect PostgreSQL", "error", err)
		os.Exit(1)
	}
	defer pool.Close()
	connection, err := amqp.Dial(configuration.RabbitURL)
	if err != nil {
		slog.Error("connect RabbitMQ", "error", err)
		os.Exit(1)
	}
	defer connection.Close()
	channel, err := connection.Channel()
	if err != nil {
		slog.Error("open RabbitMQ channel", "error", err)
		os.Exit(1)
	}
	defer channel.Close()
	topology := rabbitadapter.Topology{
		Exchange: configuration.Exchange, Queue: configuration.Queue, RoutingKey: configuration.RoutingKey,
		DLX: configuration.DLX, DLQ: configuration.DLQ,
	}
	if err := rabbitadapter.DeclareTopology(channel, topology); err != nil {
		slog.Error("declare RabbitMQ topology", "error", err)
		os.Exit(1)
	}
	hostname, _ := os.Hostname()
	relay := outbox.NewRelay(
		postgresadapter.NewOutboxStore(pool),
		rabbitadapter.NewPublisher(channel, configuration.Exchange, configuration.RoutingKey),
		fmt.Sprintf("%s-%d", hostname, os.Getpid()), time.Now,
	)
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			published, err := relay.RunBatch(ctx, 50)
			if err != nil {
				slog.Error("publish outbox batch", "error", err)
				continue
			}
			if published > 0 {
				slog.Info("published outbox events", "count", published)
			}
		}
	}
}

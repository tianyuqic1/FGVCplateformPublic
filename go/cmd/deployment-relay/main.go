// Deployment compilation has an isolated outbox and queues from training.
package main

import (
	"context"
	"encoding/json"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	amqp "github.com/rabbitmq/amqp091-go"
	rabbit "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/rabbitmq"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/observability"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/outbox"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	observability.Bootstrap("deployment-relay")
	observability.StartMetricsServer(":8090")
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	shutdownTracing, traceErr := observability.SetupTracing(ctx, "deployment-relay")
	if traceErr != nil {
		slog.Warn("tracing exporter unavailable; continuing without export", "error", traceErr)
	} else {
		defer func() {
			shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = shutdownTracing(shutdownCtx)
		}()
	}
	pool, err := pgxpool.New(ctx, os.Getenv("FINEVISION_DATABASE_URL"))
	if err != nil {
		slog.Error("database configuration failed")
		return
	}
	defer pool.Close()
	for ctx.Err() == nil {
		if err = serve(ctx, pool); err != nil {
			slog.Error("deployment relay disconnected", "error", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(5 * time.Second):
		}
	}
}
func serve(ctx context.Context, pool *pgxpool.Pool) error {
	conn, err := amqp.Dial(os.Getenv("FINEVISION_RABBITMQ_URL"))
	if err != nil {
		return err
	}
	defer conn.Close()
	for _, runtime := range []string{"tensorrt", "ascend_acl"} {
		ch, e := conn.Channel()
		if e != nil {
			return e
		}
		defer ch.Close()
		q := "fgvc.deployment." + runtime
		if e = rabbit.DeclareTopology(ch, rabbit.Topology{Exchange: "fgvc.deployment.v1", Queue: q, RoutingKey: runtime, DLX: "fgvc.deployment.dlx", DLQ: q + ".dead"}); e != nil {
			return e
		}
	}
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			if _, err = pool.Exec(ctx, `UPDATE model_deployments SET status='failed',error='构建 Worker 心跳超时，请检查日志后手动重试',updated_at=now() WHERE status='building' AND lease_expires_at<now()`); err != nil {
				return err
			}
			for i := 0; i < 20; i++ {
				more, e := dispatch(ctx, pool, conn)
				if e != nil {
					observability.RecordOutboxBatch("deployment", 0, e)
					return e
				}
				if !more {
					break
				}
				observability.RecordOutboxBatch("deployment", 1, nil)
			}
			var pending int64
			var oldest float64
			if e := pool.QueryRow(ctx, `SELECT count(*),COALESCE(EXTRACT(EPOCH FROM now()-min(created_at)),0) FROM deployment_outbox WHERE published_at IS NULL`).Scan(&pending, &oldest); e == nil {
				observability.SetOutboxBacklog("deployment", pending, oldest)
			}
		}
	}
}
func dispatch(ctx context.Context, pool *pgxpool.Pool, conn *amqp.Connection) (bool, error) {
	tx, err := pool.Begin(ctx)
	if err != nil {
		return false, err
	}
	defer tx.Rollback(ctx)
	var id, dep, token, runtime string
	var traceContext []byte
	err = tx.QueryRow(ctx, `SELECT o.id::text,o.deployment_id::text,o.build_token::text,o.runtime,o.trace_context FROM deployment_outbox o WHERE o.published_at IS NULL ORDER BY o.created_at FOR UPDATE SKIP LOCKED LIMIT 1`).Scan(&id, &dep, &token, &runtime, &traceContext)
	if err == pgx.ErrNoRows {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	payload, _ := json.Marshal(map[string]string{"deployment_id": dep, "build_token": token})
	// A short-lived channel also bounds confirm/return listeners per delivery.
	ch, err := conn.Channel()
	if err != nil {
		return false, err
	}
	defer ch.Close()
	if err = ch.Confirm(false); err != nil {
		return false, err
	}
	pub := rabbit.NewPublisher(ch, "fgvc.deployment.v1", runtime)
	timeout, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	headers := map[string]string{}
	_ = json.Unmarshal(traceContext, &headers)
	if err = pub.Publish(timeout, outbox.Event{MessageID: id, EventType: "deployment.build", Payload: payload, Headers: headers}); err != nil {
		return false, err
	}
	if _, err = tx.Exec(ctx, `UPDATE deployment_outbox SET published_at=now() WHERE id=$1`, id); err != nil {
		return false, err
	}
	return true, tx.Commit(ctx)
}

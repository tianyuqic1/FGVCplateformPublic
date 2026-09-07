package rabbitmq_test

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
	amqp "github.com/rabbitmq/amqp091-go"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/rabbitmq"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/outbox"
)

func TestRabbitMQTopologyRoutesConfirmedPersistentMessage(t *testing.T) {
	url := os.Getenv("FINEVISION_TEST_RABBITMQ_URL")
	if url == "" {
		t.Skip("set FINEVISION_TEST_RABBITMQ_URL to run the RabbitMQ integration test")
	}
	connection, err := amqp.Dial(url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = connection.Close() })
	channel, err := connection.Channel()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = channel.Close() })
	suffix := uuid.NewString()
	topology := rabbitmq.Topology{
		Exchange: "finevision.training.integration." + suffix, Queue: "finevision.training.integration." + suffix,
		RoutingKey: "training.ready", DLX: "finevision.training.integration.dlx." + suffix, DLQ: "finevision.training.integration.dlq." + suffix,
	}
	if err := rabbitmq.DeclareTopology(channel, topology); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = channel.QueueDelete(topology.Queue, false, false, false)
		_, _ = channel.QueueDelete(topology.DLQ, false, false, false)
		_ = channel.ExchangeDelete(topology.Exchange, false, false)
		_ = channel.ExchangeDelete(topology.DLX, false, false)
	})
	publisher := rabbitmq.NewPublisher(channel, topology.Exchange, topology.RoutingKey)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := publisher.Publish(ctx, outbox.Event{
		MessageID: uuid.NewString(), EventType: "training.job.ready.v1", Payload: []byte(`{"job_id":"integration"}`),
	}); err != nil {
		t.Fatal(err)
	}
	delivery, ok, err := channel.Get(topology.Queue, true)
	if err != nil {
		t.Fatal(err)
	}
	if !ok || delivery.DeliveryMode != amqp.Persistent || string(delivery.Body) != `{"job_id":"integration"}` {
		t.Fatalf("delivery ok=%v value=%#v", ok, delivery)
	}
}

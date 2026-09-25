package rabbitmq_test

import (
	"context"
	"testing"

	amqp "github.com/rabbitmq/amqp091-go"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/rabbitmq"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/outbox"
)

type fakeChannel struct {
	exchange   string
	routingKey string
	mandatory  bool
	message    amqp.Publishing
	confirms   chan amqp.Confirmation
	returns    chan amqp.Return
}

func (channel *fakeChannel) PublishWithContext(_ context.Context, exchange, key string, mandatory, _ bool, message amqp.Publishing) error {
	channel.exchange, channel.routingKey, channel.mandatory, channel.message = exchange, key, mandatory, message
	channel.confirms <- amqp.Confirmation{Ack: true}
	return nil
}
func (channel *fakeChannel) NotifyPublish(confirm chan amqp.Confirmation) chan amqp.Confirmation {
	channel.confirms = confirm
	return confirm
}
func (channel *fakeChannel) NotifyReturn(returned chan amqp.Return) chan amqp.Return {
	channel.returns = returned
	return returned
}

func TestPublisherUsesPersistentMandatoryMessagesAndWaitsForConfirm(t *testing.T) {
	t.Parallel()
	channel := &fakeChannel{}
	publisher := rabbitmq.NewPublisher(channel, "finevision.training", "training.ready")
	event := outbox.Event{
		MessageID: "message-1", EventType: "training.job.ready.v1", Payload: []byte(`{"job_id":"job-1"}`),
		Headers: map[string]string{"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
	}

	if err := publisher.Publish(context.Background(), event); err != nil {
		t.Fatal(err)
	}
	if channel.exchange != "finevision.training" || channel.routingKey != "training.ready" || !channel.mandatory {
		t.Fatalf("routing = %s/%s mandatory=%v", channel.exchange, channel.routingKey, channel.mandatory)
	}
	if channel.message.DeliveryMode != amqp.Persistent || channel.message.MessageId != "message-1" || channel.message.ContentType != "application/json" {
		t.Fatalf("message = %#v", channel.message)
	}
	if channel.message.Headers["traceparent"] != event.Headers["traceparent"] {
		t.Fatalf("trace headers = %#v", channel.message.Headers)
	}
}

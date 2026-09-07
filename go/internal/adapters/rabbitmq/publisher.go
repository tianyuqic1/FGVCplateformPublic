package rabbitmq

import (
	"context"
	"fmt"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/outbox"
)

type Channel interface {
	PublishWithContext(context.Context, string, string, bool, bool, amqp.Publishing) error
	NotifyPublish(chan amqp.Confirmation) chan amqp.Confirmation
	NotifyReturn(chan amqp.Return) chan amqp.Return
}

type Publisher struct {
	channel    Channel
	exchange   string
	routingKey string
}

func NewPublisher(channel Channel, exchange, routingKey string) *Publisher {
	return &Publisher{channel: channel, exchange: exchange, routingKey: routingKey}
}

func (publisher *Publisher) Publish(ctx context.Context, event outbox.Event) error {
	confirms := publisher.channel.NotifyPublish(make(chan amqp.Confirmation, 1))
	returns := publisher.channel.NotifyReturn(make(chan amqp.Return, 1))
	err := publisher.channel.PublishWithContext(ctx, publisher.exchange, publisher.routingKey, true, false, amqp.Publishing{
		DeliveryMode: amqp.Persistent,
		ContentType:  "application/json",
		MessageId:    event.MessageID,
		Type:         event.EventType,
		Timestamp:    time.Now().UTC(),
		Body:         event.Payload,
	})
	if err != nil {
		return err
	}
	select {
	case returned := <-returns:
		return fmt.Errorf("RabbitMQ returned mandatory message: code=%d text=%s", returned.ReplyCode, returned.ReplyText)
	case confirmation := <-confirms:
		if !confirmation.Ack {
			return fmt.Errorf("RabbitMQ publisher confirm was negative")
		}
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

type Topology struct {
	Exchange   string
	Queue      string
	RoutingKey string
	DLX        string
	DLQ        string
}

func DeclareTopology(channel *amqp.Channel, topology Topology) error {
	if err := channel.ExchangeDeclare(topology.DLX, "topic", true, false, false, false, nil); err != nil {
		return err
	}
	if _, err := channel.QueueDeclare(topology.DLQ, true, false, false, false, nil); err != nil {
		return err
	}
	if err := channel.QueueBind(topology.DLQ, "#", topology.DLX, false, nil); err != nil {
		return err
	}
	if err := channel.ExchangeDeclare(topology.Exchange, "topic", true, false, false, false, nil); err != nil {
		return err
	}
	arguments := amqp.Table{"x-dead-letter-exchange": topology.DLX}
	if _, err := channel.QueueDeclare(topology.Queue, true, false, false, false, arguments); err != nil {
		return err
	}
	if err := channel.QueueBind(topology.Queue, topology.RoutingKey, topology.Exchange, false, nil); err != nil {
		return err
	}
	return channel.Confirm(false)
}

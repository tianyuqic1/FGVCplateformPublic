package outbox_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/outbox"
)

type recordingPublisher struct {
	messages []outbox.Event
	err      error
}

func (publisher *recordingPublisher) Publish(_ context.Context, event outbox.Event) error {
	publisher.messages = append(publisher.messages, event)
	return publisher.err
}

func TestRelayMarksConfirmedMessagePublished(t *testing.T) {
	t.Parallel()
	now := time.Now().UTC()
	store := outbox.NewMemoryStore([]outbox.Event{{
		ID: "event-1", MessageID: "message-1", EventType: "training.job.ready.v1",
		Payload: []byte(`{"job_id":"job-1"}`), AvailableAt: now,
	}})
	publisher := &recordingPublisher{}
	relay := outbox.NewRelay(store, publisher, "relay-1", func() time.Time { return now })

	count, err := relay.RunBatch(context.Background(), 10)
	if err != nil {
		t.Fatal(err)
	}
	if count != 1 || len(publisher.messages) != 1 || store.Events()[0].PublishedAt.IsZero() {
		t.Fatalf("count/messages/event = %d/%d/%#v", count, len(publisher.messages), store.Events()[0])
	}
}

func TestRelayRetriesPublishFailureWithoutLosingEvent(t *testing.T) {
	t.Parallel()
	now := time.Now().UTC()
	store := outbox.NewMemoryStore([]outbox.Event{{
		ID: "event-1", MessageID: "message-1", EventType: "training.job.ready.v1",
		Payload: []byte(`{"job_id":"job-1"}`), AvailableAt: now,
	}})
	publisher := &recordingPublisher{err: errors.New("broker unavailable")}
	relay := outbox.NewRelay(store, publisher, "relay-1", func() time.Time { return now })

	count, err := relay.RunBatch(context.Background(), 10)
	if err == nil || count != 0 {
		t.Fatalf("count/error = %d/%v", count, err)
	}
	event := store.Events()[0]
	if !event.PublishedAt.IsZero() || event.PublishAttempts != 1 || event.LastError == "" || !event.AvailableAt.After(now) {
		t.Fatalf("event = %#v", event)
	}
}

func TestConfirmThenDatabaseFailureAllowsExpectedDuplicatePublish(t *testing.T) {
	t.Parallel()
	now := time.Now().UTC()
	store := outbox.NewMemoryStore([]outbox.Event{{
		ID: "event-1", MessageID: "message-1", EventType: "training.job.ready.v1",
		Payload: []byte(`{"job_id":"job-1"}`), AvailableAt: now,
	}})
	store.FailNextMarkPublished(errors.New("database unavailable"))
	publisher := &recordingPublisher{}
	relay := outbox.NewRelay(store, publisher, "relay-1", func() time.Time { return now })
	if _, err := relay.RunBatch(context.Background(), 1); err == nil {
		t.Fatal("expected mark-published failure")
	}
	relay = outbox.NewRelay(store, publisher, "relay-2", func() time.Time { return now.Add(2 * time.Minute) })
	if count, err := relay.RunBatch(context.Background(), 1); err != nil || count != 1 {
		t.Fatalf("retry count/error = %d/%v", count, err)
	}
	if len(publisher.messages) != 2 || publisher.messages[0].MessageID != publisher.messages[1].MessageID {
		t.Fatalf("published messages = %#v", publisher.messages)
	}
}

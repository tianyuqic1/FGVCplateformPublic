package outbox

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

type Event struct {
	ID              string
	MessageID       string
	EventType       string
	SchemaVersion   int
	Payload         []byte
	AvailableAt     time.Time
	CreatedAt       time.Time
	PublishedAt     time.Time
	LockOwner       string
	LockExpiresAt   time.Time
	PublishAttempts int
	LastError       string
}

type Store interface {
	Claim(context.Context, string, time.Time, time.Duration, int) ([]Event, error)
	MarkPublished(context.Context, string, string, time.Time) error
	MarkFailed(context.Context, string, string, time.Time, string) error
}

type Publisher interface {
	Publish(context.Context, Event) error
}

type Relay struct {
	store     Store
	publisher Publisher
	owner     string
	clock     func() time.Time
	lockTTL   time.Duration
}

func NewRelay(store Store, publisher Publisher, owner string, clock func() time.Time) *Relay {
	return &Relay{store: store, publisher: publisher, owner: owner, clock: clock, lockTTL: time.Minute}
}

func (relay *Relay) RunBatch(ctx context.Context, limit int) (int, error) {
	now := relay.clock().UTC()
	events, err := relay.store.Claim(ctx, relay.owner, now, relay.lockTTL, limit)
	if err != nil {
		return 0, err
	}
	published := 0
	var batchError error
	for _, event := range events {
		if err := relay.publisher.Publish(ctx, event); err != nil {
			markErr := relay.store.MarkFailed(ctx, event.ID, relay.owner, now, err.Error())
			batchError = errors.Join(batchError, err, markErr)
			continue
		}
		if err := relay.store.MarkPublished(ctx, event.ID, relay.owner, now); err != nil {
			batchError = errors.Join(batchError, err)
			continue
		}
		published++
	}
	return published, batchError
}

type MemoryStore struct {
	mu                    sync.Mutex
	events                []Event
	failNextMarkPublished error
}

func NewMemoryStore(events []Event) *MemoryStore {
	return &MemoryStore{events: append([]Event(nil), events...)}
}

func (store *MemoryStore) Claim(_ context.Context, owner string, now time.Time, lockTTL time.Duration, limit int) ([]Event, error) {
	store.mu.Lock()
	defer store.mu.Unlock()
	claimed := make([]Event, 0)
	for index := range store.events {
		event := &store.events[index]
		if !event.PublishedAt.IsZero() || event.AvailableAt.After(now) || (!event.LockExpiresAt.IsZero() && event.LockExpiresAt.After(now)) {
			continue
		}
		event.LockOwner, event.LockExpiresAt = owner, now.Add(lockTTL)
		claimed = append(claimed, *event)
		if limit > 0 && len(claimed) >= limit {
			break
		}
	}
	return claimed, nil
}

func (store *MemoryStore) MarkPublished(_ context.Context, id, owner string, now time.Time) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	if store.failNextMarkPublished != nil {
		err := store.failNextMarkPublished
		store.failNextMarkPublished = nil
		return err
	}
	event, err := store.find(id, owner)
	if err != nil {
		return err
	}
	event.PublishedAt, event.LockOwner, event.LockExpiresAt = now, "", time.Time{}
	event.PublishAttempts++
	return nil
}

func (store *MemoryStore) MarkFailed(_ context.Context, id, owner string, now time.Time, message string) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	event, err := store.find(id, owner)
	if err != nil {
		return err
	}
	event.PublishAttempts++
	backoff := time.Duration(1<<min(event.PublishAttempts-1, 6)) * time.Second
	event.AvailableAt, event.LastError = now.Add(backoff), message
	event.LockOwner, event.LockExpiresAt = "", time.Time{}
	return nil
}

func (store *MemoryStore) find(id, owner string) (*Event, error) {
	for index := range store.events {
		if store.events[index].ID == id && store.events[index].LockOwner == owner {
			return &store.events[index], nil
		}
	}
	return nil, fmt.Errorf("outbox event is not locked by relay")
}

func (store *MemoryStore) Events() []Event {
	store.mu.Lock()
	defer store.mu.Unlock()
	return append([]Event(nil), store.events...)
}

func (store *MemoryStore) FailNextMarkPublished(err error) {
	store.mu.Lock()
	defer store.mu.Unlock()
	store.failNextMarkPublished = err
}

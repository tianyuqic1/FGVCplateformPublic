package postgres

import (
	"context"
	"fmt"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/outbox"
)

type OutboxStore struct {
	pool *pgxpool.Pool
}

func NewOutboxStore(pool *pgxpool.Pool) *OutboxStore { return &OutboxStore{pool: pool} }

func (store *OutboxStore) Claim(ctx context.Context, owner string, now time.Time, lockTTL time.Duration, limit int) ([]outbox.Event, error) {
	rows, err := store.pool.Query(ctx, `
WITH candidates AS (
  SELECT id
  FROM outbox_events
  WHERE published_at IS NULL
    AND available_at <= $2
    AND (lock_expires_at IS NULL OR lock_expires_at <= $2)
  ORDER BY created_at
  FOR UPDATE SKIP LOCKED
  LIMIT $4
)
UPDATE outbox_events event
SET lock_owner=$1, lock_expires_at=$2 + make_interval(secs => $3)
FROM candidates
WHERE event.id=candidates.id
RETURNING event.id::text, event.message_id, event.event_type, event.schema_version,
          event.payload, event.available_at, event.created_at, event.publish_attempts`,
		owner, now, int(lockTTL.Seconds()), limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	events := make([]outbox.Event, 0)
	for rows.Next() {
		var event outbox.Event
		if err := rows.Scan(&event.ID, &event.MessageID, &event.EventType, &event.SchemaVersion,
			&event.Payload, &event.AvailableAt, &event.CreatedAt, &event.PublishAttempts); err != nil {
			return nil, err
		}
		event.LockOwner, event.LockExpiresAt = owner, now.Add(lockTTL)
		events = append(events, event)
	}
	return events, rows.Err()
}

func (store *OutboxStore) MarkPublished(ctx context.Context, id, owner string, now time.Time) error {
	result, err := store.pool.Exec(ctx, `
UPDATE outbox_events
SET published_at=$3, publish_attempts=publish_attempts+1, lock_owner=NULL, lock_expires_at=NULL, last_error=NULL
WHERE id=$1 AND lock_owner=$2 AND published_at IS NULL`, id, owner, now)
	if err != nil {
		return err
	}
	if result.RowsAffected() != 1 {
		return fmt.Errorf("outbox event is not locked by relay")
	}
	return nil
}

func (store *OutboxStore) MarkFailed(ctx context.Context, id, owner string, now time.Time, message string) error {
	message = truncateUTF8(message, 1000)
	result, err := store.pool.Exec(ctx, `
UPDATE outbox_events
SET publish_attempts=publish_attempts+1,
    available_at=$3 + make_interval(secs => LEAST(POWER(2, LEAST(publish_attempts, 6))::integer, 64)),
    last_error=$4, lock_owner=NULL, lock_expires_at=NULL
WHERE id=$1 AND lock_owner=$2 AND published_at IS NULL`, id, owner, now, message)
	if err != nil {
		return err
	}
	if result.RowsAffected() != 1 {
		return fmt.Errorf("outbox event is not locked by relay")
	}
	return nil
}

func truncateUTF8(value string, maximum int) string {
	if len(value) <= maximum {
		return value
	}
	value = value[:maximum]
	for !utf8.ValidString(value) {
		value = value[:len(value)-1]
	}
	return value
}

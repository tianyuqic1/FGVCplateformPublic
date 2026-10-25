package storage

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/service"
)

const watchColumns = `id::text,conversation_id::text,owner_id::text,training_run_id::text,status,observed_status,result,error,grant_token,epoch,created_at,updated_at,expires_at`

func scanWatch(row pgx.Row) (service.Watch, error) {
	var w service.Watch
	e := row.Scan(&w.ID, &w.ConversationID, &w.Owner, &w.TrainingRunID, &w.Status, &w.ObservedStatus, &w.Result, &w.Error, &w.Grant, &w.Epoch, &w.CreatedAt, &w.UpdatedAt, &w.ExpiresAt)
	return w, e
}
func (s *Store) CreateWatch(ctx context.Context, owner, conv, run, grant string) (service.Watch, error) {
	tx, e := s.Pool.Begin(ctx)
	if e != nil {
		return service.Watch{}, e
	}
	defer tx.Rollback(ctx)
	if _, e = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1,10))`, owner); e != nil {
		return service.Watch{}, e
	}
	var own bool
	if e = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM conversations WHERE id=$1 AND owner_id=$2)`, conv, owner).Scan(&own); e != nil {
		return service.Watch{}, e
	}
	if !own {
		return service.Watch{}, fault(404, "对话不存在")
	}
	existing, e := scanWatch(tx.QueryRow(ctx, `SELECT `+watchColumns+` FROM watches WHERE conversation_id=$1 AND owner_id=$2 AND training_run_id=$3 AND status IN ('active','waiting_auth')`, conv, owner, run))
	if e == nil {
		return existing, nil
	}
	if !errors.Is(e, pgx.ErrNoRows) {
		return existing, e
	}
	var count int
	if e = tx.QueryRow(ctx, `SELECT count(*) FROM watches WHERE owner_id=$1 AND status IN ('active','waiting_auth')`, owner).Scan(&count); e != nil {
		return existing, e
	}
	if count >= 8 {
		return existing, fault(429, "最多同时跟踪 8 个训练任务")
	}
	w, e := scanWatch(tx.QueryRow(ctx, `INSERT INTO watches(id,conversation_id,owner_id,training_run_id,grant_token) VALUES($1,$2,$3,$4,$5) RETURNING `+watchColumns, uuid.NewString(), conv, owner, run, grant))
	if e != nil {
		return w, e
	}
	return w, tx.Commit(ctx)
}
func (s *Store) Watches(ctx context.Context, owner, conv string) ([]service.Watch, error) {
	rows, e := s.Pool.Query(ctx, `SELECT `+watchColumns+` FROM watches WHERE owner_id=$1 AND conversation_id=$2 ORDER BY created_at DESC LIMIT 50`, owner, conv)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []service.Watch{}
	for rows.Next() {
		w, e := scanWatch(rows)
		if e != nil {
			return nil, e
		}
		out = append(out, w)
	}
	return out, rows.Err()
}
func (s *Store) ChangeWatch(ctx context.Context, owner, id, status, grant string) error {
	tag, e := s.Pool.Exec(ctx, `UPDATE watches SET status=$3,grant_token=$4,epoch=epoch+1,lease_until=NULL,next_poll=now(),updated_at=now() WHERE id=$1 AND owner_id=$2 AND status IN ('active','waiting_auth')`, id, owner, status, grant)
	if e == nil && tag.RowsAffected() != 1 {
		return fault(404, "跟踪已结束或不存在")
	}
	return e
}
func (s *Store) ClaimWatch(ctx context.Context) (service.Watch, error) {
	_, e := s.Pool.Exec(ctx, `UPDATE watches SET status='expired',grant_token='',epoch=epoch+1 WHERE status IN ('active','waiting_auth') AND expires_at<now()`)
	if e != nil {
		return service.Watch{}, e
	}
	w, e := scanWatch(s.Pool.QueryRow(ctx, `WITH candidate AS(SELECT id FROM watches WHERE status='active' AND next_poll<=now() AND (lease_until IS NULL OR lease_until<now()) ORDER BY next_poll LIMIT 1 FOR UPDATE SKIP LOCKED)
 UPDATE watches SET epoch=epoch+1,lease_until=now()+interval '45 seconds' FROM candidate WHERE watches.id=candidate.id RETURNING `+`watches.id::text,conversation_id::text,owner_id::text,training_run_id::text,status,observed_status,result,error,grant_token,epoch,created_at,updated_at,expires_at`))
	if errors.Is(e, pgx.ErrNoRows) {
		return service.Watch{}, nil
	}
	return w, e
}
func (s *Store) SaveWatch(ctx context.Context, w service.Watch, status, observed, message string, result json.RawMessage) error {
	if len(result) == 0 {
		result = json.RawMessage(`{}`)
	}
	tag, e := s.Pool.Exec(ctx, `UPDATE watches SET status=$3,observed_status=$4,error=$5,result=$6,lease_until=NULL,next_poll=now()+interval '30 seconds',updated_at=now(),grant_token=CASE WHEN $3 IN ('completed','cancelled','expired') THEN '' ELSE grant_token END WHERE id=$1 AND epoch=$2 AND status='active'`, w.ID, w.Epoch, status, observed, message, result)
	if e == nil && tag.RowsAffected() != 1 {
		return fault(409, "跟踪租约已失效")
	}
	return e
}

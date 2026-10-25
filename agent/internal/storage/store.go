package storage

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/modelcontract"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/service"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/migrations"
	"strings"
	"unicode/utf8"
)

type Store struct{ Pool *pgxpool.Pool }

func (s *Store) Migrate(ctx context.Context) error {
	tx, e := s.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer tx.Rollback(ctx)
	if _, e = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(739201001)`); e != nil {
		return e
	}
	if _, e = tx.Exec(ctx, migrations.Initial); e != nil {
		return e
	}
	if _, e = tx.Exec(ctx, migrations.Watches); e != nil {
		return e
	}
	return tx.Commit(ctx)
}
func fault(code int, msg string) error { return &service.Fault{Status: code, Message: msg} }
func (s *Store) CreateConversation(ctx context.Context, owner, title string) (service.Conversation, error) {
	c := service.Conversation{ID: uuid.NewString(), Title: strings.TrimSpace(title)}
	if c.Title == "" {
		c.Title = "新对话"
	}
	if utf8.RuneCountInString(c.Title) > 80 || !utf8.ValidString(c.Title) {
		return c, fault(422, "标题最多 80 字")
	}
	e := s.Pool.QueryRow(ctx, `INSERT INTO conversations(id,owner_id,title) VALUES($1,$2,$3) RETURNING created_at,updated_at`, c.ID, owner, c.Title).Scan(&c.CreatedAt, &c.UpdatedAt)
	return c, e
}
func (s *Store) Conversations(ctx context.Context, owner string) ([]service.Conversation, error) {
	rows, e := s.Pool.Query(ctx, `SELECT id::text,title,created_at,updated_at FROM conversations WHERE owner_id=$1 ORDER BY updated_at DESC LIMIT 100`, owner)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []service.Conversation{}
	for rows.Next() {
		var c service.Conversation
		if e = rows.Scan(&c.ID, &c.Title, &c.CreatedAt, &c.UpdatedAt); e != nil {
			return nil, e
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

const columns = `id::text,conversation_id::text,owner_id::text,request_key::text,input,status,state,grant_token,error,epoch,cancel_requested,created_at,updated_at`

func scan(row pgx.Row) (service.Run, error) {
	var r service.Run
	var raw []byte
	e := row.Scan(&r.ID, &r.ConversationID, &r.Owner, &r.Key, &r.Input, &r.Status, &raw, &r.Grant, &r.Error, &r.Epoch, &r.Cancel, &r.CreatedAt, &r.UpdatedAt)
	if e == nil {
		e = json.Unmarshal(raw, &r.State)
	}
	r.Public()
	return r, e
}
func (s *Store) Get(ctx context.Context, owner, id string) (service.Run, error) {
	r, e := scan(s.Pool.QueryRow(ctx, `SELECT `+columns+` FROM runs WHERE id=$1 AND owner_id=$2`, id, owner))
	if errors.Is(e, pgx.ErrNoRows) {
		return r, fault(404, "任务不存在")
	}
	return r, e
}
func (s *Store) Runs(ctx context.Context, owner, id string) ([]service.Run, error) {
	var exists bool
	if e := s.Pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM conversations WHERE id=$1 AND owner_id=$2)`, id, owner).Scan(&exists); e != nil {
		return nil, e
	}
	if !exists {
		return nil, fault(404, "对话不存在")
	}
	rows, e := s.Pool.Query(ctx, `SELECT `+columns+` FROM (SELECT * FROM runs WHERE conversation_id=$1 AND owner_id=$2 ORDER BY created_at DESC LIMIT 100) q ORDER BY created_at`, id, owner)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []service.Run{}
	for rows.Next() {
		r, e := scan(rows)
		if e != nil {
			return nil, e
		}
		out = append(out, r)
	}
	return out, rows.Err()
}
func event(ctx context.Context, tx pgx.Tx, id, kind string, data any) error {
	b, e := json.Marshal(data)
	if e != nil {
		return e
	}
	_, e = tx.Exec(ctx, `INSERT INTO events(run_id,kind,data) VALUES($1,$2,$3)`, id, kind, b)
	return e
}
func (s *Store) Submit(ctx context.Context, owner, conv, key, input, grant string) (service.Run, error) {
	input = strings.TrimSpace(input)
	if input == "" || len(input) > 16000 || !utf8.ValidString(input) {
		return service.Run{}, fault(422, "请输入问题，最多 16000 字节")
	}
	if _, e := uuid.Parse(key); e != nil {
		return service.Run{}, fault(422, "请求键必须为 UUID")
	}
	tx, e := s.Pool.Begin(ctx)
	if e != nil {
		return service.Run{}, e
	}
	defer tx.Rollback(ctx)
	// Serialize queue admission, then the owner, so global/user quotas are exact across replicas.
	if _, e = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(739201003)`); e != nil {
		return service.Run{}, e
	}
	// Lock the owner first to make queue quotas and idempotency atomic across tabs.
	if _, e = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1,9))`, owner); e != nil {
		return service.Run{}, e
	}
	previous, e := scan(tx.QueryRow(ctx, `SELECT `+columns+` FROM runs WHERE owner_id=$1 AND request_key=$2`, owner, key))
	if e == nil {
		if previous.ConversationID != conv || previous.Input != input {
			return previous, fault(409, "请求键对应的内容不同")
		}
		return previous, nil
	}
	if !errors.Is(e, pgx.ErrNoRows) {
		return previous, e
	}
	var title string
	e = tx.QueryRow(ctx, `SELECT title FROM conversations WHERE id=$1 AND owner_id=$2 FOR UPDATE`, conv, owner).Scan(&title)
	if errors.Is(e, pgx.ErrNoRows) {
		return previous, fault(404, "对话不存在")
	}
	if e != nil {
		return previous, e
	}
	var active, queued, total int
	if e = tx.QueryRow(ctx, `SELECT count(*) FILTER(WHERE conversation_id=$1 AND status IN ('queued','running','waiting_confirmation','waiting_auth')),count(*) FILTER(WHERE owner_id=$2 AND status IN ('queued','running','waiting_confirmation','waiting_auth')),count(*) FILTER(WHERE status IN ('queued','running')) FROM runs`, conv, owner).Scan(&active, &queued, &total); e != nil {
		return previous, e
	}
	if active > 0 {
		return previous, fault(409, "当前对话还有未完成任务")
	}
	if queued >= 8 || total >= 128 {
		return previous, fault(429, "任务队列已满，请稍后重试")
	}
	state := service.State{Messages: []modelcontract.Message{}}
	history, e := tx.Query(ctx, `SELECT input,state->>'output' FROM (SELECT input,state,created_at FROM runs WHERE conversation_id=$1 AND status='succeeded' ORDER BY created_at DESC LIMIT 6) q ORDER BY created_at`, conv)
	if e != nil {
		return previous, e
	}
	for history.Next() {
		var in, out string
		if e = history.Scan(&in, &out); e != nil {
			history.Close()
			return previous, e
		}
		state.Messages = append(state.Messages, modelcontract.Message{Role: "user", Content: in}, modelcontract.Message{Role: "assistant", Content: out})
	}
	e = history.Err()
	history.Close()
	if e != nil {
		return previous, e
	}
	state.Messages = append(state.Messages, modelcontract.Message{Role: "user", Content: input})
	raw, _ := json.Marshal(state)
	r, e := scan(tx.QueryRow(ctx, `INSERT INTO runs(id,conversation_id,owner_id,request_key,input,status,state,grant_token) VALUES($1,$2,$3,$4,$5,'queued',$6,$7) RETURNING `+columns, uuid.NewString(), conv, owner, key, input, raw, grant))
	if e != nil {
		return r, e
	}
	if title == "新对话" {
		rr := []rune(input)
		title = string(rr[:min(30, len(rr))])
	}
	if _, e = tx.Exec(ctx, `UPDATE conversations SET updated_at=now(),title=$2 WHERE id=$1`, conv, title); e != nil {
		return r, e
	}
	if e = event(ctx, tx, r.ID, "queued", map[string]string{"status": "queued"}); e != nil {
		return r, e
	}
	return r, tx.Commit(ctx)
}

// Claim serializes admission, enforces limits across replicas and fences stale workers.
func (s *Store) Claim(ctx context.Context) (service.Run, error) {
	tx, e := s.Pool.Begin(ctx)
	if e != nil {
		return service.Run{}, e
	}
	defer tx.Rollback(ctx)
	if _, e = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(739201002)`); e != nil {
		return service.Run{}, e
	}
	stale, e := tx.Query(ctx, `SELECT id::text,state,cancel_requested FROM runs WHERE status='running' AND lease_until<now() FOR UPDATE`)
	if e != nil {
		return service.Run{}, e
	}
	type expired struct {
		Cancel bool
		id     string
		state  service.State
	}
	items := []expired{}
	for stale.Next() {
		var x expired
		var b []byte
		if e = stale.Scan(&x.id, &b, &x.Cancel); e != nil {
			stale.Close()
			return service.Run{}, e
		}
		if e = json.Unmarshal(b, &x.state); e != nil {
			stale.Close()
			return service.Run{}, e
		}
		items = append(items, x)
	}
	e = stale.Err()
	stale.Close()
	if e != nil {
		return service.Run{}, e
	}
	for _, x := range items {
		status := "queued"
		reason := "执行租约过期，已恢复队列"
		if x.Cancel {
			status = "cancelled"
			reason = "助手已停止，未继续派发任务"
		}
		if x.Cancel && x.state.WritePending {
			status = "unknown"
			reason = "平台写操作已派发，结果需核对"
		}
		if x.state.ModelPending {
			status = "unknown"
			reason = "模型请求在重启前已派发，结果未知，请检查后重新发起"
		}
		if _, e = tx.Exec(ctx, `UPDATE runs SET status=$2,epoch=epoch+1,lease_until=NULL,error=$3,updated_at=now(),
 grant_token=CASE WHEN $2 IN ('unknown','cancelled') THEN '' ELSE grant_token END,
 state=CASE WHEN $2 IN ('unknown','cancelled') THEN state-'receipt' ELSE state END WHERE id=$1`, x.id, status, reason); e != nil {
			return service.Run{}, e
		}
		if e = event(ctx, tx, x.id, "recovered", map[string]string{"status": status, "message": reason}); e != nil {
			return service.Run{}, e
		}
	}
	r, e := scan(tx.QueryRow(ctx, `SELECT `+columns+` FROM runs r WHERE status='queued' AND NOT cancel_requested
 AND (SELECT count(*) FROM runs WHERE status='running')<8
 AND (SELECT count(*) FROM runs a WHERE a.status='running' AND a.owner_id=r.owner_id)<2
 ORDER BY (SELECT count(*) FROM runs a WHERE a.status='running' AND a.owner_id=r.owner_id),created_at LIMIT 1 FOR UPDATE SKIP LOCKED`))
	if errors.Is(e, pgx.ErrNoRows) {
		return r, tx.Commit(ctx)
	}
	if e != nil {
		return r, e
	}
	r.Epoch++
	r.Status = "running"
	if _, e = tx.Exec(ctx, `UPDATE runs SET status='running',epoch=$2,lease_until=now()+interval '60 seconds',updated_at=now() WHERE id=$1`, r.ID, r.Epoch); e != nil {
		return r, e
	}
	if e = event(ctx, tx, r.ID, "running", map[string]string{"status": "running"}); e != nil {
		return r, e
	}
	return r, tx.Commit(ctx)
}
func (s *Store) Heartbeat(ctx context.Context, r service.Run) (bool, error) {
	var cancel bool
	e := s.Pool.QueryRow(ctx, `UPDATE runs SET lease_until=now()+interval '60 seconds' WHERE id=$1 AND epoch=$2 AND status='running' AND lease_until>now() RETURNING cancel_requested`, r.ID, r.Epoch).Scan(&cancel)
	return cancel, e
}
func (s *Store) Save(ctx context.Context, r *service.Run, status, kind string, data any) error {
	if status == "succeeded" || status == "failed" || status == "cancelled" || status == "unknown" {
		r.State.Receipt = ""
	}
	raw, e := json.Marshal(r.State)
	if e != nil {
		return e
	}
	tx, e := s.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer tx.Rollback(ctx)
	tag, e := tx.Exec(ctx, `UPDATE runs SET state=$3,status=$4,error=$5,updated_at=now(),lease_until=CASE WHEN $4='running' THEN lease_until ELSE NULL END,grant_token=CASE WHEN $4 IN ('succeeded','failed','cancelled','unknown') THEN '' ELSE grant_token END WHERE id=$1 AND epoch=$2 AND status='running' AND lease_until>now()`, r.ID, r.Epoch, raw, status, r.Error)
	if e != nil {
		return e
	}
	if tag.RowsAffected() != 1 {
		return fault(409, "执行租约已失效")
	}
	if e = event(ctx, tx, r.ID, kind, data); e != nil {
		return e
	}
	r.Status = status
	return tx.Commit(ctx)
}
func (s *Store) Cancel(ctx context.Context, owner, id string) error {
	tx, e := s.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer tx.Rollback(ctx)
	r, e := scan(tx.QueryRow(ctx, `SELECT `+columns+` FROM runs WHERE id=$1 AND owner_id=$2 FOR UPDATE`, id, owner))
	if errors.Is(e, pgx.ErrNoRows) {
		return fault(404, "任务不存在")
	}
	if e != nil {
		return e
	}
	if r.Status == "succeeded" || r.Status == "failed" || r.Status == "cancelled" {
		return nil
	}
	status := r.Status
	if status != "running" {
		status = "cancelled"
	}
	if _, e = tx.Exec(ctx, `UPDATE runs SET cancel_requested=true,status=$2,updated_at=now(),grant_token=CASE WHEN $2='cancelled' THEN '' ELSE grant_token END WHERE id=$1`, id, status); e != nil {
		return e
	}
	if e = event(ctx, tx, id, "cancellation", map[string]string{"status": status, "message": "停止后不再调用模型或新工具；已提交的平台任务需另行取消"}); e != nil {
		return e
	}
	return tx.Commit(ctx)
}

// Confirm is a CAS against the exact preview. Browser retries cannot dispatch twice.
func (s *Store) Confirm(ctx context.Context, owner, id, operation, receipt, grant string, args json.RawMessage) error {
	tx, e := s.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer tx.Rollback(ctx)
	r, e := scan(tx.QueryRow(ctx, `SELECT `+columns+` FROM runs WHERE id=$1 AND owner_id=$2 FOR UPDATE`, id, owner))
	if e != nil {
		return e
	}
	for _, completed := range r.State.CompletedOperations {
		if completed == operation {
			return nil
		}
	}
	p := r.State.Proposal
	if p == nil || p.OperationID != operation {
		return fault(409, "方案已变更，请刷新")
	}
	if p.Confirmed {
		return nil
	}
	if r.Status != "waiting_confirmation" || r.Cancel {
		return fault(409, "任务不再等待确认")
	}
	var a, b any
	_ = json.Unmarshal(p.Arguments, &a)
	_ = json.Unmarshal(args, &b)
	left, _ := json.Marshal(a)
	right, _ := json.Marshal(b)
	if string(left) != string(right) {
		return fault(409, "规范参数已变化，请重新生成方案")
	}
	p.Confirmed = true
	r.State.Receipt = receipt
	raw, _ := json.Marshal(r.State)
	if _, e = tx.Exec(ctx, `UPDATE runs SET state=$2,status='queued',grant_token=$3,updated_at=now() WHERE id=$1`, id, raw, grant); e != nil {
		return e
	}
	if e = event(ctx, tx, id, "confirmed", map[string]string{"operation_id": operation}); e != nil {
		return e
	}
	return tx.Commit(ctx)
}
func (s *Store) Resume(ctx context.Context, owner, id, grant string) error {
	tag, e := s.Pool.Exec(ctx, `UPDATE runs SET status='queued',grant_token=$3,error='',updated_at=now() WHERE id=$1 AND owner_id=$2 AND status='waiting_auth' AND NOT cancel_requested`, id, owner, grant)
	if e == nil && tag.RowsAffected() != 1 {
		return fault(409, "任务无需恢复授权")
	}
	return e
}
func (s *Store) Events(ctx context.Context, owner, id string, after int64) ([]service.Event, error) {
	if _, e := s.Get(ctx, owner, id); e != nil {
		return nil, e
	}
	rows, e := s.Pool.Query(ctx, `SELECT id,kind,data,created_at FROM events WHERE run_id=$1 AND id>$2 ORDER BY id LIMIT 100`, id, after)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []service.Event{}
	for rows.Next() {
		var ev service.Event
		if e = rows.Scan(&ev.ID, &ev.Kind, &ev.Data, &ev.CreatedAt); e != nil {
			return nil, e
		}
		out = append(out, ev)
	}
	return out, rows.Err()
}

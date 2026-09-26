// Package workers is an observation plane, never a task scheduler or lease owner.
package workers

import (
	"context"
	"encoding/json"
	"errors"
	"regexp"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var ErrConflict = errors.New("worker session superseded or sequence stale")
var validID = regexp.MustCompile(`^[A-Za-z0-9_.:/-]{1,160}$`)
var kinds = map[string]bool{"training": true, "inference": true, "annotation": true, "artifact": true, "deployment": true}

type Task struct {
	ID        string    `json:"id"`
	Kind      string    `json:"kind"`
	Stage     string    `json:"stage"`
	StartedAt time.Time `json:"started_at"`
}
type Snapshot struct {
	ID        string `json:"id"`
	SessionID string `json:"session_id,omitempty"`
	Sequence  int64  `json:"sequence,omitempty"`
	Name      string `json:"name"`
	Kind      string `json:"kind"`
	NodeID    string `json:"node_id"`
	Version   string `json:"version"`
	Backend   string `json:"backend"`
	Device    string `json:"device"`
	Capacity  int    `json:"capacity"`
	Readiness string `json:"readiness"`
	Reason    string `json:"reason"`
	Tasks     []Task `json:"tasks"`
}

func (s Snapshot) Validate() bool {
	if !validID.MatchString(s.ID) || !kinds[s.Kind] || s.Name == "" || len(s.Name) > 120 || s.Sequence < 0 {
		return false
	}
	if _, err := uuid.Parse(s.SessionID); err != nil {
		return false
	}
	if s.NodeID != "" && !validID.MatchString(s.NodeID) {
		return false
	}
	if s.Capacity < 1 || s.Capacity > 128 || len(s.Tasks) > s.Capacity {
		return false
	}
	if s.Readiness != "ready" && s.Readiness != "initializing" && s.Readiness != "dependency_error" && s.Readiness != "unknown" && s.Readiness != "stopped" {
		return false
	}
	if len(s.Reason) > 200 || len(s.Version) > 80 || len(s.Backend) > 80 || len(s.Device) > 80 {
		return false
	}
	for _, t := range s.Tasks {
		if !validID.MatchString(t.ID) || !kinds[t.Kind] || len(t.Stage) > 80 || t.StartedAt.IsZero() {
			return false
		}
	}
	return true
}

type Instance struct {
	Snapshot
	RegisteredAt   time.Time `json:"registered_at"`
	ReceivedAt     time.Time `json:"received_at"`
	Connection     string    `json:"connection"`
	CanAccept      bool      `json:"can_accept"`
	ActiveCount    int       `json:"active_count"`
	TaskVisibility string    `json:"task_visibility"`
}

// Scope task detail using control-plane ownership, never a claimed owner from telemetry.
func (s *PostgresStore) OwnedTraining(ctx context.Context, userID string, ids []string) (map[string]bool, error) {
	rows, err := s.Pool.Query(ctx, `SELECT tr.id::text FROM training_runs tr JOIN jobs j ON j.id=tr.job_id
 WHERE tr.id::text=ANY($1::text[]) AND j.payload->>'created_by_user_id'=$2`, ids, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	owned := map[string]bool{}
	for rows.Next() {
		var id string
		if err = rows.Scan(&id); err != nil {
			return nil, err
		}
		owned[id] = true
	}
	return owned, rows.Err()
}

func (i *Instance) Derive(now time.Time) {
	age := now.Sub(i.ReceivedAt)
	i.Connection = "online"
	if age > 30*time.Second {
		i.Connection = "delayed"
	}
	if age > 90*time.Second || i.Readiness == "stopped" {
		i.Connection = "offline"
	}
	i.CanAccept = i.Connection == "online" && i.Readiness == "ready" && len(i.Tasks) < i.Capacity
	i.ActiveCount = len(i.Tasks)
	if i.Tasks == nil {
		i.Tasks = []Task{}
	}
}

type Store interface {
	Report(context.Context, Snapshot, bool) error
	List(context.Context) ([]Instance, error)
}
type PostgresStore struct{ Pool *pgxpool.Pool }

func (s *PostgresStore) Report(ctx context.Context, v Snapshot, register bool) error {
	raw, err := json.Marshal(v)
	if err != nil {
		return err
	}
	if !register {
		result, err := s.Pool.Exec(ctx, `UPDATE worker_instances SET sequence=$3,snapshot=$4,received_at=clock_timestamp()
   WHERE id=$1 AND session_id=$2 AND sequence<$3`, v.ID, v.SessionID, v.Sequence, raw)
		if err != nil {
			return err
		}
		if result.RowsAffected() == 0 {
			return ErrConflict
		}
		return nil
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	// Serialize first registration too: a row does not necessarily exist yet.
	if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1,0))`, v.ID); err != nil {
		return err
	}
	var owner string
	err = tx.QueryRow(ctx, `SELECT worker_id FROM worker_sessions WHERE session_id=$1`, v.SessionID).Scan(&owner)
	if err == nil {
		var active string
		if owner != v.ID {
			return ErrConflict
		}
		if err = tx.QueryRow(ctx, `SELECT session_id::text FROM worker_instances WHERE id=$1`, v.ID).Scan(&active); err != nil {
			return err
		}
		if active != v.SessionID {
			return ErrConflict
		}
		return tx.Commit(ctx) // idempotent registration must not refresh liveness or overwrite a heartbeat
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO worker_sessions(session_id,worker_id) VALUES($1,$2)`, v.SessionID, v.ID); err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO worker_instances(id,session_id,snapshot) VALUES($1,$2,$3)
 ON CONFLICT(id) DO UPDATE SET session_id=EXCLUDED.session_id,sequence=0,snapshot=EXCLUDED.snapshot,registered_at=clock_timestamp(),received_at=clock_timestamp()`, v.ID, v.SessionID, raw)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func (s *PostgresStore) List(ctx context.Context) ([]Instance, error) {
	rows, err := s.Pool.Query(ctx, `SELECT snapshot,registered_at,received_at FROM worker_instances ORDER BY id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := []Instance{}
	for rows.Next() {
		var i Instance
		var raw []byte
		if err = rows.Scan(&raw, &i.RegisteredAt, &i.ReceivedAt); err != nil {
			return nil, err
		}
		if err = json.Unmarshal(raw, &i.Snapshot); err != nil {
			return nil, err
		}
		items = append(items, i)
	}
	return items, rows.Err()
}

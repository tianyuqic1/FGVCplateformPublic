package hardware

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var ErrNotFound = errors.New("node not found")
var ErrOldSample = errors.New("sample is older than the latest sample")

type Store interface {
	Ingest(context.Context, Snapshot) error
	Nodes(context.Context) ([]Snapshot, error)
	Latest(context.Context, string) (Snapshot, error)
	History(context.Context, string, time.Time, int) ([]Snapshot, error)
	Tasks(context.Context, string) ([]Task, error)
}
type PostgresStore struct{ Pool *pgxpool.Pool }

func (s *PostgresStore) Ingest(ctx context.Context, sample Snapshot) error {
	raw, err := json.Marshal(sample)
	if err != nil {
		return err
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	result, err := tx.Exec(ctx, `INSERT INTO hardware_nodes(node_id,sampled_at,received_at,snapshot) VALUES($1,$2,$3,$4)
 ON CONFLICT(node_id) DO UPDATE SET sampled_at=EXCLUDED.sampled_at,received_at=EXCLUDED.received_at,snapshot=EXCLUDED.snapshot
 WHERE hardware_nodes.sampled_at < EXCLUDED.sampled_at`, sample.NodeID, sample.SampledAt, sample.ReceivedAt, raw)
	if err != nil {
		return err
	}
	if result.RowsAffected() == 0 {
		return ErrOldSample
	}
	_, err = tx.Exec(ctx, `INSERT INTO hardware_samples(node_id,received_at,snapshot) VALUES($1,$2,$3)`, sample.NodeID, sample.ReceivedAt, raw)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func (s *PostgresStore) Nodes(ctx context.Context) ([]Snapshot, error) {
	rows, err := s.Pool.Query(ctx, `SELECT snapshot FROM hardware_nodes ORDER BY node_id`)
	if err != nil {
		return nil, err
	}
	return readSamples(rows)
}
func (s *PostgresStore) Latest(ctx context.Context, id string) (Snapshot, error) {
	var raw []byte
	var sample Snapshot
	err := s.Pool.QueryRow(ctx, `SELECT snapshot FROM hardware_nodes WHERE node_id=$1`, id).Scan(&raw)
	if errors.Is(err, pgx.ErrNoRows) {
		return sample, ErrNotFound
	}
	if err != nil {
		return sample, err
	}
	err = json.Unmarshal(raw, &sample)
	return sample, err
}
func (s *PostgresStore) History(ctx context.Context, id string, since time.Time, step int) ([]Snapshot, error) {
	// Keep the last real sample in each bucket; never synthesize zeroes or average missing fields.
	rows, err := s.Pool.Query(ctx, `SELECT snapshot FROM (
 SELECT DISTINCT ON (floor(extract(epoch FROM received_at)/$3::int)) snapshot,received_at
 FROM hardware_samples WHERE node_id=$1 AND received_at >= $2
 ORDER BY floor(extract(epoch FROM received_at)/$3::int),received_at DESC
 ) samples ORDER BY received_at`, id, since, step)
	if err != nil {
		return nil, err
	}
	return readSamples(rows)
}
func readSamples(rows pgx.Rows) ([]Snapshot, error) {
	defer rows.Close()
	samples := []Snapshot{}
	for rows.Next() {
		var raw []byte
		var sample Snapshot
		if err := rows.Scan(&raw); err != nil {
			return nil, err
		}
		if err := json.Unmarshal(raw, &sample); err != nil {
			return nil, err
		}
		samples = append(samples, sample)
	}
	return samples, rows.Err()
}
func (s *PostgresStore) Tasks(ctx context.Context, id string) ([]Task, error) {
	rows, err := s.Pool.Query(ctx, `SELECT tr.id::text,COALESCE(NULLIF(j.payload->>'name',''),d.name || ' · 训练'),a.started_at
 FROM training_runs tr JOIN datasets d ON d.id=tr.dataset_id JOIN jobs j ON j.id=tr.job_id
 JOIN job_attempts a ON a.id=j.active_attempt_id
 WHERE tr.status='running' AND j.status='running' AND a.status='running' AND a.lease_expires_at>now()
 AND split_part(a.worker_id,'/',1)='node' AND split_part(a.worker_id,'/',2)=$1 ORDER BY a.started_at`, id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	tasks := []Task{}
	for rows.Next() {
		var task Task
		if err := rows.Scan(&task.ID, &task.Name, &task.StartedAt); err != nil {
			return nil, err
		}
		tasks = append(tasks, task)
	}
	return tasks, rows.Err()
}
func (s *PostgresStore) Prune(ctx context.Context) error {
	_, err := s.Pool.Exec(ctx, `DELETE FROM hardware_samples WHERE received_at < now()-interval '24 hours'`)
	return err
}
func (s *PostgresStore) RunRetention(ctx context.Context) {
	ticker := time.NewTicker(time.Minute)
	defer ticker.Stop()
	for {
		cleanup, cancel := context.WithTimeout(ctx, 20*time.Second)
		err := s.Prune(cleanup)
		cancel()
		if err != nil && ctx.Err() == nil {
			slog.Error("hardware retention failed", "error", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

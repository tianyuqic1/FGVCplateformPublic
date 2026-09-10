package postgres

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
)

// Pending jobs are bounded globally. One scan at a time avoids competing with
// inference for the shared compute pool. Advisory transaction locks serialize
// admission/claims across replicas without holding a DB connection during scans.
type DatasetImportQueue struct{ Pool *pgxpool.Pool }

const importQueueLock = 72610419

func (r *DatasetImportQueue) Enqueue(ctx context.Context, j dataset.ImportJob) error {
	err := pgx.BeginFunc(ctx, r.Pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock($1)`, importQueueLock); err != nil {
			return err
		}
		var exists bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM dataset_versions WHERE import_request_id=$1)`, j.RequestID).Scan(&exists); err != nil {
			return err
		}
		if exists {
			return dataset.ErrConflict
		}
		var count int
		if err := tx.QueryRow(ctx, `SELECT count(*) FROM dataset_import_jobs WHERE status IN ('queued','running')`).Scan(&count); err != nil {
			return err
		}
		if count >= 20 {
			return dataset.ErrQueueFull
		}
		archive, err := json.Marshal(j.Archive)
		if err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `INSERT INTO dataset_import_jobs(id,name,request_id,archive,status,created_at) VALUES($1,$2,$3,$4,'queued',$5)`, j.ID, j.Name, j.RequestID, archive, j.CreatedAt)
		return err
	})
	var pgerr *pgconn.PgError
	if errors.As(err, &pgerr) && pgerr.Code == "23505" {
		return dataset.ErrConflict
	}
	return err
}

const importJobColumns = `id::text,name,request_id::text,archive,status,attempt,created_at,result,error`

func scanImportJob(row pgx.Row) (dataset.ImportJob, error) {
	var j dataset.ImportJob
	var archive, result []byte
	err := row.Scan(&j.ID, &j.Name, &j.RequestID, &archive, &j.Status, &j.Attempt, &j.CreatedAt, &result, &j.Error)
	if err != nil {
		return j, err
	}
	if err = json.Unmarshal(archive, &j.Archive); err != nil {
		return j, err
	}
	if len(result) > 0 {
		err = json.Unmarshal(result, &j.Result)
	}
	return j, err
}
func (r *DatasetImportQueue) List(ctx context.Context) ([]dataset.ImportJob, error) {
	rows, err := r.Pool.Query(ctx, `SELECT * FROM (
 (SELECT `+importJobColumns+` FROM dataset_import_jobs WHERE status IN ('queued','running') ORDER BY created_at DESC LIMIT 20)
 UNION ALL
 (SELECT `+importJobColumns+` FROM dataset_import_jobs WHERE status IN ('succeeded','failed') ORDER BY created_at DESC LIMIT 20)
 ) jobs ORDER BY (status IN ('queued','running')) DESC,created_at DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	jobs := []dataset.ImportJob{}
	for rows.Next() {
		j, err := scanImportJob(rows)
		if err != nil {
			return nil, err
		}
		jobs = append(jobs, j)
	}
	return jobs, rows.Err()
}
func (r *DatasetImportQueue) Claim(ctx context.Context) (dataset.ImportJob, error) {
	var j dataset.ImportJob
	err := pgx.BeginFunc(ctx, r.Pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock($1)`, importQueueLock); err != nil {
			return err
		}
		var count int
		if err := tx.QueryRow(ctx, `SELECT count(*) FROM dataset_import_jobs WHERE status='running' AND lease_until>now()`).Scan(&count); err != nil {
			return err
		}
		if count >= 1 {
			return dataset.ErrNoImportJob
		}
		var err error
		j, err = scanImportJob(tx.QueryRow(ctx, `UPDATE dataset_import_jobs SET status='running',attempt=attempt+1,lease_until=now()+interval '2 minutes' WHERE id=(SELECT id FROM dataset_import_jobs WHERE status='queued' OR (status='running' AND lease_until<=now()) ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING `+importJobColumns))
		if errors.Is(err, pgx.ErrNoRows) {
			return dataset.ErrNoImportJob
		}
		return err
	})
	return j, err
}
func (r *DatasetImportQueue) Renew(ctx context.Context, j dataset.ImportJob) error {
	result, err := r.Pool.Exec(ctx, `UPDATE dataset_import_jobs SET lease_until=now()+interval '2 minutes' WHERE id=$1 AND attempt=$2 AND status='running' AND lease_until>now()`, j.ID, j.Attempt)
	return checkImportLease(result, err)
}
func (r *DatasetImportQueue) Finish(ctx context.Context, j dataset.ImportJob, result map[string]any, message string) error {
	status := "succeeded"
	if message != "" {
		status = "failed"
	}
	data, err := json.Marshal(result)
	if err != nil {
		return err
	}
	tag, err := r.Pool.Exec(ctx, `UPDATE dataset_import_jobs SET status=$3,result=$4,error=$5,finished_at=now(),lease_until=NULL WHERE id=$1 AND attempt=$2 AND status='running' AND lease_until>now()`, j.ID, j.Attempt, status, data, message)
	return checkImportLease(tag, err)
}
func checkImportLease(tag pgconn.CommandTag, err error) error {
	if err != nil {
		return err
	}
	if tag.RowsAffected() != 1 {
		return dataset.ErrImportLeaseLost
	}
	return nil
}

package postgres

import (
	"context"
	"errors"
	"fmt"
	"github.com/jackc/pgx/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/review"
)

// DeleteTrainingRun only removes terminal, artifact-free queue records. A
// queued/paused run must be cancelled first so the outbox cannot dispatch it.
func (s *ReadModels) DeleteTrainingRun(ctx context.Context, id string) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	var job, status string
	err = tx.QueryRow(ctx, `SELECT j.id::text,j.status FROM jobs j JOIN training_runs r ON r.job_id=j.id WHERE r.id::text=$1 OR r.run_key=$1 FOR UPDATE OF j`, id).Scan(&job, &status)
	if errors.Is(err, pgx.ErrNoRows) {
		return fmt.Errorf("训练记录不存在: %w", review.ErrNotFound)
	}
	if err != nil {
		return err
	}
	if status != "failed" && status != "cancelled" {
		return fmt.Errorf("只能删除已失败或已取消且没有模型产物的任务: %w", review.ErrConflict)
	}
	var run string
	var protected bool
	err = tx.QueryRow(ctx, `SELECT r.id::text,(r.feature_artifact_id IS NOT NULL OR r.model_artifact_id IS NOT NULL OR r.report_artifact_id IS NOT NULL OR r.calibration_artifact_id IS NOT NULL OR r.threshold_strategy_artifact_id IS NOT NULL OR EXISTS(SELECT 1 FROM artifacts WHERE training_run_id=r.id) OR EXISTS(SELECT 1 FROM model_versions WHERE training_run_id=r.id)) FROM training_runs r WHERE r.job_id=$1 FOR UPDATE`, job).Scan(&run, &protected)
	if err != nil {
		return err
	}
	if protected {
		return fmt.Errorf("任务已有模型或产物，不能删除: %w", review.ErrConflict)
	}
	if _, err = tx.Exec(ctx, `DELETE FROM training_runs WHERE id=$1`, run); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `DELETE FROM job_events WHERE job_id=$1`, job); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `DELETE FROM outbox_events WHERE aggregate_id=$1`, job); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `DELETE FROM jobs WHERE id=$1`, job); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

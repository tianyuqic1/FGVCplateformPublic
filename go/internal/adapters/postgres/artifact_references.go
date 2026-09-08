package postgres

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

type ArtifactReferences struct{ pool *pgxpool.Pool }

func NewArtifactReferences(pool *pgxpool.Pool) *ArtifactReferences {
	return &ArtifactReferences{pool: pool}
}

func (references *ArtifactReferences) PhysicalReferences(ctx context.Context, sha256, candidateArtifactID string) (artifact.PhysicalReferenceState, error) {
	var state artifact.PhysicalReferenceState
	err := references.pool.QueryRow(ctx, `
WITH candidate AS (
  SELECT id FROM artifacts WHERE id::text=$2 AND checksum=$1
), other_records AS (
  SELECT count(*) AS count FROM artifacts WHERE checksum=$1 AND id::text<>$2
), relationships AS (
  SELECT count(*) AS count FROM candidate c
  WHERE EXISTS (
    SELECT 1 FROM training_runs tr WHERE c.id IN (
      tr.feature_artifact_id,tr.model_artifact_id,tr.report_artifact_id,
      tr.calibration_artifact_id,tr.threshold_strategy_artifact_id
    )
  ) OR EXISTS (
    SELECT 1 FROM model_versions mv WHERE c.id IN (
      mv.model_artifact_id,mv.calibration_artifact_id,mv.threshold_strategy_artifact_id
    )
  ) OR EXISTS (
    SELECT 1 FROM inference_events ie WHERE c.id IN (
      ie.model_artifact_id,ie.feature_artifact_id,ie.threshold_strategy_artifact_id
    )
  )
)
SELECT EXISTS(SELECT 1 FROM candidate),
       (SELECT count FROM other_records),
       (SELECT count FROM relationships)`, sha256, candidateArtifactID).Scan(
		&state.CandidateExists, &state.OtherRecords, &state.Relationships,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return artifact.PhysicalReferenceState{}, nil
	}
	return state, err
}

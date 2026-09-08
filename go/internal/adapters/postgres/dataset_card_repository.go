package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"sort"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/datasetcard"
)

type DatasetCardRepository struct{ Pool *pgxpool.Pool }

func (r *DatasetCardRepository) Get(ctx context.Context, id string) (datasetcard.State, error) {
	state := datasetcard.State{Card: datasetcard.Card{Task: "image_classification", Domain: "general", KnownConfusions: []string{}}, Generations: []datasetcard.Generation{}}
	var metadata, splits, readiness []byte
	err := r.Pool.QueryRow(ctx, `SELECT dv.id::text,d.name,dv.class_count,dv.sample_count,COALESCE(a.artifact_metadata,'{}'),dv.split_summary,dv.readiness_report
 FROM dataset_versions dv JOIN datasets d ON d.id=dv.dataset_id LEFT JOIN artifacts a ON a.id=dv.manifest_artifact_id WHERE dv.id::text=$1 OR dv.version_key=$1`, id).Scan(&state.Facts.VersionID, &state.Facts.DatasetName, &state.Facts.ClassCount, &state.Facts.SampleCount, &metadata, &splits, &readiness)
	if errors.Is(err, pgx.ErrNoRows) {
		return state, datasetcard.ErrNotFound
	}
	if err != nil {
		return state, err
	}
	var source struct {
		Manifest struct {
			Classes []string `json:"classes"`
		} `json:"manifest"`
		Classes []string `json:"classes"`
	}
	if err = json.Unmarshal(metadata, &source); err != nil {
		return state, err
	}
	state.Facts.Classes = source.Manifest.Classes
	if len(state.Facts.Classes) == 0 {
		state.Facts.Classes = source.Classes
	}
	var counts map[string]map[string]int
	if err = json.Unmarshal(splits, &counts); err != nil {
		return state, err
	}
	state.Facts.SplitTotals = map[string]int{}
	state.Facts.SplitCounts = counts
	labels := map[string]bool{}
	for split, byClass := range counts {
		for label, n := range byClass {
			state.Facts.SplitTotals[split] += n
			labels[label] = true
		}
	}
	if len(state.Facts.Classes) == 0 {
		state.Facts.Classes = []string{}
		for label := range labels {
			state.Facts.Classes = append(state.Facts.Classes, label)
		}
		sort.Strings(state.Facts.Classes)
	}
	if err = json.Unmarshal(readiness, &state.Facts.Readiness); err != nil {
		return state, err
	}
	var card []byte
	err = r.Pool.QueryRow(ctx, `SELECT revision,card FROM dataset_card_revisions WHERE dataset_version_id=$1 ORDER BY revision DESC LIMIT 1`, state.Facts.VersionID).Scan(&state.Revision, &card)
	if err == nil {
		err = json.Unmarshal(card, &state.Card)
	} else if errors.Is(err, pgx.ErrNoRows) {
		// Preserve historical user cards as revision zero; subsequent writes use the new ledger only.
		err = r.Pool.QueryRow(ctx, `SELECT artifact_metadata->'dataset_card' FROM artifacts WHERE dataset_version_id=$1 AND artifact_type='dataset_card' ORDER BY created_at DESC LIMIT 1`, state.Facts.VersionID).Scan(&card)
		if err == nil && len(card) > 0 {
			err = json.Unmarshal(card, &state.Card)
		}
		if errors.Is(err, pgx.ErrNoRows) {
			err = nil
		}
	}
	if err != nil {
		return state, err
	}
	state.Card.Task = "image_classification"
	if state.Card.KnownConfusions == nil {
		state.Card.KnownConfusions = []string{}
	}
	rows, err := r.Pool.Query(ctx, `SELECT id::text,base_revision,input_sha256,
 CASE WHEN status='running' AND expires_at<now() THEN 'failed' ELSE status END,
 result,COALESCE(error_code,CASE WHEN status='running' AND expires_at<now() THEN 'GENERATION_EXPIRED' ELSE '' END),created_at,applied_revision
 FROM dataset_card_generations WHERE dataset_version_id=$1 ORDER BY created_at DESC LIMIT 10`, state.Facts.VersionID)
	if err != nil {
		return state, err
	}
	defer rows.Close()
	for rows.Next() {
		var g datasetcard.Generation
		var result []byte
		if err = rows.Scan(&g.ID, &g.BaseRevision, &g.InputSHA256, &g.Status, &result, &g.ErrorCode, &g.CreatedAt, &g.AppliedRevision); err != nil {
			return state, err
		}
		if len(result) > 0 {
			if err = json.Unmarshal(result, &g.Result); err != nil {
				return state, err
			}
		}
		state.Generations = append(state.Generations, g)
	}
	return state, rows.Err()
}
func (r *DatasetCardRepository) Save(ctx context.Context, id string, expected int, card datasetcard.Card, draft string) (datasetcard.State, error) {
	canonical := ""
	err := pgx.BeginFunc(ctx, r.Pool, func(tx pgx.Tx) error {
		err := tx.QueryRow(ctx, `SELECT id::text FROM dataset_versions WHERE id::text=$1 OR version_key=$1 FOR UPDATE`, id).Scan(&canonical)
		if errors.Is(err, pgx.ErrNoRows) {
			return datasetcard.ErrNotFound
		}
		if err != nil {
			return err
		}
		var revision int
		err = tx.QueryRow(ctx, `SELECT COALESCE(max(revision),0) FROM dataset_card_revisions WHERE dataset_version_id=$1`, canonical).Scan(&revision)
		if err != nil {
			return err
		}
		if revision != expected {
			return datasetcard.ErrConflict
		}
		if draft != "" {
			var base int
			err = tx.QueryRow(ctx, `SELECT base_revision FROM dataset_card_generations WHERE id::text=$1 AND dataset_version_id=$2 AND status='succeeded' AND applied_revision IS NULL FOR UPDATE`, draft, canonical).Scan(&base)
			if errors.Is(err, pgx.ErrNoRows) {
				return datasetcard.ErrConflict
			}
			if err != nil {
				return err
			}
			if base != revision {
				return datasetcard.ErrConflict
			}
		}
		encoded, err := json.Marshal(card)
		if err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `INSERT INTO dataset_card_revisions(dataset_version_id,revision,card,draft_id) VALUES($1,$2,$3,NULLIF($4,'')::uuid)`, canonical, revision+1, encoded, draft)
		if err != nil {
			return err
		}
		if draft != "" {
			_, err = tx.Exec(ctx, `UPDATE dataset_card_generations SET applied_revision=$2 WHERE id::text=$1`, draft, revision+1)
		}
		return err
	})
	if err != nil {
		return datasetcard.State{}, err
	}
	return r.Get(ctx, canonical)
}
func (r *DatasetCardRepository) Begin(ctx context.Context, id, requestID, hash string, revision int) (datasetcard.Generation, bool, error) {
	var generation datasetcard.Generation
	created := false
	err := pgx.BeginFunc(ctx, r.Pool, func(tx pgx.Tx) error {
		var locked string
		if err := tx.QueryRow(ctx, `SELECT id::text FROM dataset_versions WHERE id=$1 FOR UPDATE`, id).Scan(&locked); err != nil {
			return err
		}
		var current int
		if err := tx.QueryRow(ctx, `SELECT COALESCE(max(revision),0) FROM dataset_card_revisions WHERE dataset_version_id=$1`, id).Scan(&current); err != nil {
			return err
		}
		if current != revision {
			return datasetcard.ErrConflict
		}
		_, err := tx.Exec(ctx, `UPDATE dataset_card_generations SET status='failed',error_code='GENERATION_EXPIRED',finished_at=now() WHERE dataset_version_id=$1 AND status='running' AND expires_at<now()`, id)
		if err != nil {
			return err
		}
		var version string
		var result []byte
		err = tx.QueryRow(ctx, `SELECT dataset_version_id::text,id::text,base_revision,input_sha256,status,result,COALESCE(error_code,''),created_at,applied_revision FROM dataset_card_generations WHERE id=$1`, requestID).Scan(&version, &generation.ID, &generation.BaseRevision, &generation.InputSHA256, &generation.Status, &result, &generation.ErrorCode, &generation.CreatedAt, &generation.AppliedRevision)
		if err == nil {
			if version != id || generation.InputSHA256 != hash {
				return datasetcard.ErrConflict
			}
			if len(result) > 0 {
				return json.Unmarshal(result, &generation.Result)
			}
			return nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return err
		}
		err = tx.QueryRow(ctx, `INSERT INTO dataset_card_generations(id,dataset_version_id,base_revision,input_sha256,prompt_version,status) VALUES($1,$2,$3,$4,$5,'running') RETURNING id::text,base_revision,input_sha256,status,created_at`, requestID, id, revision, hash, datasetcard.PromptVersion).Scan(&generation.ID, &generation.BaseRevision, &generation.InputSHA256, &generation.Status, &generation.CreatedAt)
		created = err == nil
		return err
	})
	var pgerr *pgconn.PgError
	if errors.As(err, &pgerr) && pgerr.Code == "23505" {
		err = datasetcard.ErrConflict
	}
	return generation, created, err
}
func (r *DatasetCardRepository) Finish(ctx context.Context, id string, result *datasetcard.Generated, code string) error {
	data, err := json.Marshal(result)
	if err != nil {
		return err
	}
	state := "succeeded"
	if code != "" {
		state = "failed"
	}
	tag, err := r.Pool.Exec(ctx, `UPDATE dataset_card_generations SET status=$2,result=$3,error_code=NULLIF($4,''),finished_at=now() WHERE id=$1 AND status='running' AND expires_at>now()`, id, state, data, code)
	if err == nil && tag.RowsAffected() != 1 {
		return datasetcard.ErrConflict
	}
	return err
}

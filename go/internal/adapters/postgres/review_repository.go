package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/review"
)

type ReviewRepository struct{ Pool *pgxpool.Pool }

const reviewFrom = ` FROM review_items r JOIN datasets d ON d.id=r.dataset_id LEFT JOIN feedback_items f ON f.review_item_id=r.id `
const reviewJSON = `jsonb_build_object('id',r.review_key,'review_item_id',r.review_key,'inference_event_id',r.inference_event_id,'inference_run_id',r.inference_run_id,'dataset_id',d.dataset_key,'dataset_version_id',r.dataset_version_id,'model_version_id',r.model_version_id,'sample_id',r.sample_id,'image_url',r.context->>'image_url','input_ref',r.input_ref,'status',r.status,'risk_type',r.risk_type,'priority',r.priority,'reason',r.reason,'reason_codes',r.reason_codes,'context',r.context,'assistance_metadata',r.assistance_metadata,'created_at',r.created_at,'updated_at',r.updated_at,'submitted_at',r.submitted_at,'feedbacked_at',r.feedbacked_at,'completed_by',r.completed_by,'feedback',CASE WHEN f.id IS NULL THEN NULL ELSE to_jsonb(f)||jsonb_build_object('id',f.feedback_key,'feedback_item_id',f.feedback_key,'review_item_id',r.review_key,'dataset_id',d.dataset_key,'image_url',r.context->>'image_url','input_ref',r.input_ref) END)`
const feedbackJSON = `to_jsonb(f)||jsonb_build_object('id',f.feedback_key,'feedback_item_id',f.feedback_key,'review_item_id',r.review_key,'dataset_id',d.dataset_key,'image_url',r.context->>'image_url','input_ref',r.input_ref)`

func (s *ReviewRepository) List(ctx context.Context, filter review.Filter, feedback bool) ([]map[string]any, int, error) {
	expression, where, value := reviewJSON, ` WHERE ($1='' OR r.status=$1)`, filter.Status
	if feedback {
		expression, where, value = feedbackJSON, ` WHERE f.id IS NOT NULL AND ($1='' OR f.destination=$1)`, filter.Destination
	}
	where += ` AND ($2='' OR d.dataset_key=$2 OR d.id::text=$2)`
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, 0, err
	}
	defer tx.Rollback(ctx)
	var total int
	if err = tx.QueryRow(ctx, `SELECT count(*)`+reviewFrom+where, value, filter.Dataset).Scan(&total); err != nil {
		return nil, 0, err
	}
	order := ` ORDER BY r.priority ASC,r.created_at ASC,r.id`
	if feedback {
		order = ` ORDER BY f.created_at DESC,f.id`
	}
	rows, err := tx.Query(ctx, `SELECT `+expression+reviewFrom+where+order+` LIMIT $3 OFFSET $4`, value, filter.Dataset, filter.Limit, filter.Offset)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()
	result := []map[string]any{}
	for rows.Next() {
		var raw []byte
		if err = rows.Scan(&raw); err != nil {
			return nil, 0, err
		}
		var item map[string]any
		if err = json.Unmarshal(raw, &item); err != nil {
			return nil, 0, err
		}
		result = append(result, item)
	}
	if err = rows.Err(); err != nil {
		return nil, 0, err
	}
	rows.Close()
	if err = tx.Commit(ctx); err != nil {
		return nil, 0, err
	}
	return result, total, nil
}
func (s *ReviewRepository) Get(ctx context.Context, id string) (map[string]any, error) {
	var raw []byte
	err := s.Pool.QueryRow(ctx, `SELECT `+reviewJSON+reviewFrom+` WHERE r.review_key=$1 OR r.id::text=$1`, id).Scan(&raw)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, review.ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	var item map[string]any
	err = json.Unmarshal(raw, &item)
	return item, err
}
func (s *ReviewRepository) Submit(ctx context.Context, id string, input review.Submission) (map[string]any, error) {
	if err := input.Validate(); err != nil {
		return nil, err
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	var dbID, status string
	err = tx.QueryRow(ctx, `SELECT id::text,status FROM review_items WHERE review_key=$1 OR id::text=$1 FOR UPDATE`, id).Scan(&dbID, &status)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, review.ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	if status != "pending" {
		return nil, review.ErrConflict
	}
	if input.Destination == "training_candidate" {
		var valid bool
		err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM review_items r JOIN dataset_versions v ON v.id=r.dataset_version_id JOIN artifacts a ON a.id=v.manifest_artifact_id CROSS JOIN LATERAL jsonb_array_elements(a.artifact_metadata->'manifest'->'classes') c WHERE r.id=$1 AND (c#>>'{}'=$2 OR c->>'name'=$2 OR c->>'label'=$2 OR c->>'id'=$2))`, dbID, input.Label).Scan(&valid)
		if err != nil {
			return nil, err
		}
		if !valid {
			return nil, review.ErrInvalid
		}
	}
	_, err = tx.Exec(ctx, `INSERT INTO feedback_items(id,feedback_key,review_item_id,inference_event_id,inference_run_id,dataset_id,dataset_version_id,model_version_id,sample_id,final_label,final_outcome,destination,reviewer_note,feedback_metadata,created_by,created_at) SELECT $2,$3,id,inference_event_id,inference_run_id,dataset_id,dataset_version_id,model_version_id,sample_id,NULLIF($4,''),$5,$6,NULLIF($7,''),'{"source":"human_review_go"}'::jsonb,NULLIF($8,''),now() FROM review_items WHERE id=$1`, dbID, uuid.NewString(), "feedback-"+uuid.NewString(), input.Label, input.Outcome, input.Destination, input.Note, input.Reviewer)
	if err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `UPDATE review_items SET status='feedbacked',submitted_at=now(),feedbacked_at=now(),updated_at=now(),completed_by=NULLIF($2,'') WHERE id=$1`, dbID, input.Reviewer)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(ctx); err != nil {
		return nil, err
	}
	return s.Get(ctx, id)
}
func (s *ReviewRepository) SaveAssistance(ctx context.Context, id string, value map[string]any) error {
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	result, err := s.Pool.Exec(ctx, `UPDATE review_items SET assistance_metadata=assistance_metadata||jsonb_build_object('llm_assistance',$2::jsonb),updated_at=now() WHERE (review_key=$1 OR id::text=$1) AND status='pending'`, id, raw)
	if err != nil {
		return err
	}
	if result.RowsAffected() != 1 {
		return review.ErrConflict
	}
	return nil
}

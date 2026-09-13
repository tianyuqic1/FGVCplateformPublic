package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/review"
	"math"
	"sort"
	"strconv"
	"strings"
)

type PolicyRepository struct{ Pool *pgxpool.Pool }

const policySelect = `SELECT to_jsonb(p)||jsonb_build_object('policy_id',p.policy_key,'dataset_id',d.dataset_key) FROM abstention_policy_versions p JOIN datasets d ON d.id=p.dataset_id JOIN dataset_versions v ON v.id=p.dataset_version_id JOIN model_versions m ON m.id=p.model_version_id `

func policyLimit(f map[string]string) (int, error) {
	n := 50
	if f["limit"] != "" {
		var err error
		n, err = strconv.Atoi(f["limit"])
		if err != nil {
			return 0, review.ErrInvalid
		}
	}
	if n < 1 || n > 300 {
		return 0, review.ErrInvalid
	}
	return n, nil
}
func (s *PolicyRepository) Policies(ctx context.Context, f map[string]string) ([]map[string]any, error) {
	n, err := policyLimit(f)
	if err != nil {
		return nil, err
	}
	status := f["status"]
	if status == "all" {
		status = ""
	}
	if !policyMember(status, "", "shadow", "candidate", "active", "superseded", "deactivated", "archived") {
		return nil, review.ErrInvalid
	}
	return queryObjects(ctx, s.Pool, policySelect+`WHERE ($1='' OR p.status=$1) AND ($2='' OR v.id::text=$2 OR v.version_key=$2) AND ($3='' OR m.id::text=$3 OR m.model_key=$3) ORDER BY p.created_at DESC,p.id LIMIT $4`, status, f["dataset_version_id"], f["model_version_id"], n)
}
func policyMember(s string, values ...string) bool {
	for _, v := range values {
		if s == v {
			return true
		}
	}
	return false
}
func (s *PolicyRepository) Shadow(ctx context.Context, id string, f map[string]string) ([]map[string]any, error) {
	n, err := policyLimit(f)
	if err != nil {
		return nil, err
	}
	diff := f["diff"]
	if diff == "all" {
		diff = ""
	}
	if !policyMember(diff, "", "same", "new_accepts_old_abstains", "new_abstains_old_accepts", "new_rejects_ood", "other_change") {
		return nil, review.ErrInvalid
	}
	var exists bool
	if err = s.Pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM abstention_policy_versions WHERE policy_key=$1 OR id::text=$1)`, id).Scan(&exists); err != nil {
		return nil, err
	}
	if !exists {
		return nil, review.ErrNotFound
	}
	return queryObjects(ctx, s.Pool, `SELECT to_jsonb(s)||jsonb_build_object('shadow_decision_id',s.id,'policy_id',p.policy_key,'dataset_version_id',p.dataset_version_id,'model_version_id',p.model_version_id,'dataset_id',d.dataset_key) FROM abstention_shadow_decisions s JOIN abstention_policy_versions p ON p.id=s.policy_version_id JOIN datasets d ON d.id=p.dataset_id WHERE (p.policy_key=$1 OR p.id::text=$1) AND ($2='' OR s.decision_diff=$2) ORDER BY s.created_at DESC,s.id LIMIT $3`, id, diff, n)
}
func textInput(m map[string]any, k string) string { s, _ := m[k].(string); return strings.TrimSpace(s) }
func numberInput(m map[string]any, k string, fallback float64) float64 {
	if x, ok := m[k].(float64); ok {
		return x
	}
	if _, exists := m[k]; exists {
		return math.NaN()
	}
	return fallback
}
func (s *PolicyRepository) PolicyAction(ctx context.Context, id, action string, input map[string]any) (map[string]any, error) {
	if action == "propose" {
		return s.propose(ctx, input)
	}
	reason := textInput(input, action+"d_reason")
	actor := textInput(input, action+"d_by")
	if action == "activate" {
		reason = textInput(input, "activation_reason")
		actor = textInput(input, "activated_by")
	}
	if action == "deactivate" {
		reason = textInput(input, "deactivation_reason")
		actor = textInput(input, "deactivated_by")
	}
	if reason == "" || len(reason) > 8192 {
		return nil, review.ErrInvalid
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	// Lock the model scope first to serialize concurrent activations.
	var modelID string
	err = tx.QueryRow(ctx, `SELECT m.id::text FROM model_versions m JOIN abstention_policy_versions p ON p.model_version_id=m.id WHERE p.policy_key=$1 OR p.id::text=$1 FOR UPDATE OF m`, id).Scan(&modelID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, review.ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	var dbID, version, status string
	var count int
	var target, risk float64
	err = tx.QueryRow(ctx, `SELECT id::text,dataset_version_id::text,status,source_feedback_count,target_selective_risk,COALESCE((metrics->>'selective_risk')::float,1) FROM abstention_policy_versions WHERE policy_key=$1 OR id::text=$1 FOR UPDATE`, id).Scan(&dbID, &version, &status, &count, &target, &risk)
	if err != nil {
		return nil, err
	}
	if action == "activate" {
		if !policyMember(status, "shadow", "candidate", "superseded", "deactivated") || count < 5 || risk > target {
			return nil, review.ErrConflict
		}
		_, err = tx.Exec(ctx, `UPDATE abstention_policy_versions SET status='superseded',deactivated_at=now(),deactivated_by=$3,deactivation_reason=$4,updated_at=now() WHERE model_version_id=$1 AND dataset_version_id=$2 AND status='active'`, modelID, version, actor, "superseded by "+id)
		if err != nil {
			return nil, err
		}
		_, err = tx.Exec(ctx, `UPDATE abstention_policy_versions SET status='active',activated_at=now(),activated_by=$2,activation_reason=$3,deactivated_at=NULL,deactivated_by=NULL,deactivation_reason=NULL,updated_at=now() WHERE id=$1`, dbID, actor, reason)
	} else {
		if status != "active" {
			return nil, review.ErrConflict
		}
		_, err = tx.Exec(ctx, `UPDATE abstention_policy_versions SET status='deactivated',deactivated_at=now(),deactivated_by=$2,deactivation_reason=$3,updated_at=now() WHERE id=$1`, dbID, actor, reason)
	}
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(ctx); err != nil {
		return nil, err
	}
	return queryObject(ctx, s.Pool, policySelect+`WHERE p.id=$1`, dbID)
}

type feedbackScore struct {
	Confidence, Margin float64
	Ood                *float64
	Correct            bool
}

func candidates(values []float64, ood bool) []float64 {
	sort.Float64s(values)
	set := map[float64]bool{}
	if !ood {
		set[0] = true
		set[1] = true
	}
	if len(values) > 0 {
		if ood {
			set[0] = true
			set[math.Round((values[len(values)-1]+1e-6)*1e6)/1e6] = true
		}
		n := len(values) + 2
		if n > 11 {
			n = 11
		}
		for i := 0; i < n; i++ {
			pos := float64(i) * float64(len(values)-1) / float64(n-1)
			lo := int(pos)
			hi := int(math.Ceil(pos))
			value := values[lo] + (values[hi]-values[lo])*(pos-float64(lo))
			set[math.Round(value*1e6)/1e6] = true
		}
	}
	out := []float64{}
	for v := range set {
		out = append(out, v)
	}
	sort.Float64s(out)
	return out
}
func (s *PolicyRepository) propose(ctx context.Context, input map[string]any) (map[string]any, error) {
	target, cost := numberInput(input, "target_selective_risk", .05), numberInput(input, "review_cost_per_item", 1)
	if target < 0 || target > 1 || cost < 0 || math.IsNaN(target) || math.IsNaN(cost) || math.IsInf(cost, 0) {
		return nil, review.ErrInvalid
	}
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead})
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	var datasetID, versionID, modelID string
	err = tx.QueryRow(ctx, `SELECT m.dataset_id::text,m.dataset_version_id::text,m.id::text FROM model_versions m JOIN dataset_versions v ON v.id=m.dataset_version_id WHERE (m.id::text=$1 OR m.model_key=$1) AND (v.id::text=$2 OR v.version_key=$2)`, textInput(input, "model_version_id"), textInput(input, "dataset_version_id")).Scan(&datasetID, &versionID, &modelID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, review.ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	rows, err := tx.Query(ctx, `SELECT e.confidence,e.margin,e.ood_score,(f.final_outcome IN ('confirmed_label','corrected_label') AND f.final_label=COALESCE(e.result_payload#>>'{result,top_k,0,label}',e.result_payload#>>'{top_k,0,label}')) FROM feedback_items f JOIN inference_events e ON e.id=f.inference_event_id WHERE f.model_version_id=$1 AND f.dataset_version_id=$2 AND f.final_outcome IN ('confirmed_label','corrected_label','ood','bad_image') AND e.confidence IS NOT NULL AND e.margin IS NOT NULL`, modelID, versionID)
	if err != nil {
		return nil, err
	}
	samples := []feedbackScore{}
	confs, margins, oods := []float64{}, []float64{}, []float64{}
	for rows.Next() {
		var v feedbackScore
		var correct *bool
		if err = rows.Scan(&v.Confidence, &v.Margin, &v.Ood, &correct); err != nil {
			rows.Close()
			return nil, err
		}
		v.Correct = correct != nil && *correct
		samples = append(samples, v)
		confs = append(confs, v.Confidence)
		margins = append(margins, v.Margin)
		if v.Ood != nil {
			oods = append(oods, *v.Ood)
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	if len(samples) == 0 {
		return nil, fmt.Errorf("暂无可评估的人工反馈，请先完成复核: %w", review.ErrConflict)
	}
	confs = candidates(confs, false)
	margins = candidates(margins, false)
	oc := []*float64{nil}
	for _, o := range candidates(oods, true) {
		v := o
		oc = append(oc, &v)
	}
	bestAccepted, bestErrors := -1, 0
	bestConf, bestMargin := 1., 1.
	var bestOod *float64
	candidateCount := 0
	for _, c := range confs {
		for _, m := range margins {
			for _, o := range oc {
				candidateCount++
				accepted, wrong := 0, 0
				for _, sample := range samples {
					if sample.Confidence >= c && sample.Margin >= m && (o == nil || sample.Ood == nil || *sample.Ood <= *o) {
						accepted++
						if !sample.Correct {
							wrong++
						}
					}
				}
				risk := 0.
				if accepted > 0 {
					risk = float64(wrong) / float64(accepted)
				}
				if risk <= target && accepted > bestAccepted {
					bestAccepted, bestErrors, bestConf, bestMargin, bestOod = accepted, wrong, c, m, o
				}
			}
		}
	}
	if bestAccepted < 0 {
		return nil, fmt.Errorf("当前反馈无法满足目标风险，请检查目标或补充反馈: %w", review.ErrConflict)
	}
	risk := 0.
	if bestAccepted > 0 {
		risk = float64(bestErrors) / float64(bestAccepted)
	}
	metrics, _ := json.Marshal(map[string]any{"source_feedback_count": len(samples), "accepted_count": bestAccepted, "error_count": bestErrors, "coverage": float64(bestAccepted) / float64(len(samples)), "selective_risk": risk, "estimated_review_cost": float64(len(samples)-bestAccepted) * cost, "target_selective_risk": target})
	config, _ := json.Marshal(map[string]any{"selection_rule": "max_coverage_under_target_risk", "candidate_count": candidateCount, "eligible_feedback_count": len(samples)})
	id, key := uuid.NewString(), "policy-"+uuid.NewString()
	_, err = tx.Exec(ctx, `INSERT INTO abstention_policy_versions(id,policy_key,dataset_id,dataset_version_id,model_version_id,status,target_selective_risk,tau_conf,tau_margin,tau_ood,source_feedback_count,metrics,selection_config,created_by,created_at,updated_at) VALUES($1,$2,$3,$4,$5,'shadow',$6,$7,$8,$9,$10,$11,$12,$13,now(),now())`, id, key, datasetID, versionID, modelID, target, bestConf, bestMargin, bestOod, len(samples), metrics, config, textInput(input, "created_by"))
	if err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO abstention_shadow_decisions(id,policy_version_id,inference_event_id,current_decision,shadow_decision,decision_diff,score_snapshot,created_at) SELECT gen_random_uuid(),$1,id,decision,shadow,CASE WHEN decision=shadow THEN 'same' WHEN shadow='reject_ood' THEN 'new_rejects_ood' WHEN shadow='accept' AND decision='abstain' THEN 'new_accepts_old_abstains' WHEN shadow='abstain' AND decision='accept' THEN 'new_abstains_old_accepts' ELSE 'other_change' END,jsonb_build_object('confidence',confidence,'margin',margin,'ood_score',ood_score),now() FROM (SELECT e.*,CASE WHEN $4::float IS NOT NULL AND ood_score>$4 THEN 'reject_ood' WHEN confidence<$2 OR margin<$3 THEN 'abstain' ELSE 'accept' END shadow FROM inference_events e WHERE model_version_id=$5 AND dataset_version_id=$6) x`, id, bestConf, bestMargin, bestOod, modelID, versionID)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(ctx); err != nil {
		return nil, err
	}
	return queryObject(ctx, s.Pool, policySelect+`WHERE p.id=$1`, id)
}

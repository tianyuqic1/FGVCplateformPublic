package postgres_test

import (
	"context"
	"errors"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	postgres "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/postgres"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/review"
	"os"
	"strings"
	"sync"
	"testing"
)

func TestReviewFeedbackAndPolicyDatabaseFlow(t *testing.T) {
	url := os.Getenv("FINEVISION_TEST_GO_DATABASE_URL")
	if url == "" {
		t.Skip("requires isolated api_repair_test_ database")
	}
	cfg, err := pgxpool.ParseConfig(url)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(cfg.ConnConfig.Database, "api_repair_test_") {
		t.Skip("test writes only to api_repair_test_ database")
	}
	ctx := context.Background()
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	repo := &postgres.ReviewRepository{Pool: pool}
	readModels := postgres.NewReadModels(pool)
	if err = readModels.DeleteTrainingRun(ctx, "missing-run"); !errors.Is(err, review.ErrNotFound) {
		t.Fatal("missing delete", err)
	}
	var deletable string
	err = pool.QueryRow(ctx, `SELECT r.run_key FROM training_runs r JOIN jobs j ON j.id=r.job_id WHERE j.status IN ('failed','cancelled') AND r.feature_artifact_id IS NULL AND r.model_artifact_id IS NULL AND r.report_artifact_id IS NULL AND r.calibration_artifact_id IS NULL AND r.threshold_strategy_artifact_id IS NULL AND NOT EXISTS(SELECT 1 FROM artifacts WHERE training_run_id=r.id) AND NOT EXISTS(SELECT 1 FROM model_versions WHERE training_run_id=r.id) LIMIT 1`).Scan(&deletable)
	if err == nil {
		if err = readModels.DeleteTrainingRun(ctx, deletable); err != nil {
			t.Fatal("delete terminal run", err)
		}
	}
	policies := &postgres.PolicyRepository{Pool: pool}
	datasetID, version, model := "", "", ""
	if err = pool.QueryRow(ctx, `SELECT dataset_id::text,dataset_version_id::text,id::text FROM model_versions ORDER BY created_at DESC LIMIT 1`).Scan(&datasetID, &version, &model); err != nil {
		t.Fatal(err)
	}
	keys := []string{}
	for i := 0; i < 6; i++ {
		event, key := uuid.NewString(), "test-review-"+uuid.NewString()
		keys = append(keys, key)
		_, err = pool.Exec(ctx, `INSERT INTO inference_events(id,event_key,dataset_id,dataset_version_id,model_version_id,model_status,input_type,decision,confidence,margin,reasons,request_payload,result_payload,created_at) VALUES($1::uuid,$1::text,$2,$3,$4,'production','sample','abstain',0.4,0.1,'[]','{}','{"result":{"top_k":[{"label":"wrong"}]}}',now());`, event, datasetID, version, model)
		if err != nil {
			t.Fatal(err)
		}
		_, err = pool.Exec(ctx, `INSERT INTO review_items(id,review_key,inference_event_id,dataset_id,dataset_version_id,model_version_id,status,risk_type,priority,reason,context,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,'pending','low_confidence',50,'test','{}',now(),now())`, uuid.NewString(), key, event, datasetID, version, model)
		if err != nil {
			t.Fatal(err)
		}
	}
	if _, err = repo.Submit(ctx, keys[0], review.Submission{Outcome: "corrected_label", Destination: "training_candidate", Label: "not-a-real-class"}); !errors.Is(err, review.ErrInvalid) {
		t.Fatal("unknown label accepted", err)
	}
	input := review.Submission{Outcome: "ood", Destination: "ood_stress", Reviewer: "integration-test"}
	var wg sync.WaitGroup
	results := make(chan error, 2)
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); _, err := repo.Submit(ctx, keys[0], input); results <- err }()
	}
	wg.Wait()
	close(results)
	success, conflict := 0, 0
	for err := range results {
		if err == nil {
			success++
		} else if errors.Is(err, review.ErrConflict) {
			conflict++
		} else {
			t.Fatal(err)
		}
	}
	if success != 1 || conflict != 1 {
		t.Fatal(success, conflict)
	}
	for _, key := range keys[1:] {
		if _, err = repo.Submit(ctx, key, input); err != nil {
			t.Fatal(err)
		}
	}
	items, total, err := repo.List(ctx, review.Filter{Destination: "ood_stress", Dataset: datasetID, Limit: 1}, true)
	if err != nil || len(items) != 1 || total < 6 {
		t.Fatal(items, total, err)
	}
	if err = repo.SaveAssistance(ctx, keys[0], map[string]any{}); !errors.Is(err, review.ErrConflict) {
		t.Fatal("completed review mutated", err)
	}
	proposal, err := policies.PolicyAction(ctx, "", "propose", map[string]any{"dataset_version_id": version, "model_version_id": model, "target_selective_risk": 0.05})
	if err != nil {
		t.Fatal("proposal", err)
	}
	key := proposal["policy_id"].(string)
	if proposal["status"] != "shadow" {
		t.Fatal(proposal)
	}
	shadows, err := policies.Shadow(ctx, key, map[string]string{"limit": "10"})
	if err != nil || len(shadows) == 0 {
		t.Fatal("shadow", err)
	}
	active, err := policies.PolicyAction(ctx, key, "activate", map[string]any{"activation_reason": "integration-test", "activated_by": "test"})
	if err != nil || active["status"] != "active" {
		t.Fatal("activate", err)
	}
	inactive, err := policies.PolicyAction(ctx, key, "deactivate", map[string]any{"deactivation_reason": "integration-test", "deactivated_by": "test"})
	if err != nil || inactive["status"] != "deactivated" {
		t.Fatal("deactivate", err)
	}
}

package httpapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/agenttools"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/auth"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelcatalog"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/testerrors"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/workers"
)

type toolSessions struct {
	auth.Store
	sessions map[string]auth.Session
}

func TestTrainingToolErrorsPreserveUncertainCommitOutcomes(t *testing.T) {
	for _, test := range []struct {
		err    error
		status int
	}{
		{errors.New("database commit connection interrupted"), 503},
		{&training.DomainError{Code: training.CodeValidationFailed}, 422},
		{&training.DomainError{Code: training.CodeIdempotencyConflict}, 409},
		{&training.DomainError{Code: training.CodeInvalidState}, 409},
		{&training.DomainError{Code: training.CodeNotFound}, 404},
	} {
		if status := trainingToolStatus(test.err); status != test.status {
			t.Fatalf("%v: %d", test.err, status)
		}
	}
}

func (s toolSessions) FindSession(_ context.Context, hash []byte) (auth.Session, error) {
	v, ok := s.sessions[hex.EncodeToString(hash)]
	if !ok {
		return auth.Session{}, auth.ErrCredentials
	}
	return v, nil
}

type toolReadModels struct {
	emptyReadModels
	runID string
}

func (r toolReadModels) GetDatasetVersion(_ context.Context, id string) (map[string]any, error) {
	if id != "version-1" {
		return nil, ErrReadModelNotFound
	}
	return map[string]any{"dataset_version_id": id, "classes": []string{"bird"}, "split_counts": map[string]int{"train": 10}, "uri": "s3://private/archive"}, nil
}
func (r toolReadModels) GetTrainingRun(_ context.Context, id string) (map[string]any, error) {
	if id != r.runID {
		return nil, ErrReadModelNotFound
	}
	return map[string]any{"id": id, "head_config": map[string]any{"epochs": 10}}, nil
}
func (r toolReadModels) GetTrainingRunMetrics(_ context.Context, id string, q MetricsQuery) (map[string]any, error) {
	if id != r.runID {
		return nil, ErrReadModelNotFound
	}
	return map[string]any{"points": []map[string]any{{"name": "loss", "value": 0.5}}, "limit": q.Limit}, nil
}

type toolWorkers struct{ workers.Store }

func (toolWorkers) List(context.Context) ([]workers.Instance, error) {
	return []workers.Instance{}, nil
}

type toolInference struct {
	calls atomic.Int32
	wait  <-chan struct{}
}

func (s *toolInference) Predict(ctx context.Context, input map[string]any, _ []byte) (map[string]any, error) {
	s.calls.Add(1)
	if s.wait != nil {
		select {
		case <-s.wait:
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	return map[string]any{"inference_event_id": "event", "model_version_id": input["model_version_id"], "result": map[string]any{"top_k": []map[string]any{{"label": "bird", "score": 0.9}}}}, nil
}
func (*toolInference) Image(context.Context, string) ([]byte, string, error) {
	return nil, "", errors.New("unused")
}

type failingToolFinish struct{ agenttools.Store }

func (failingToolFinish) Finish(context.Context, string, string, int, json.RawMessage) error {
	return errors.New("simulated response persistence failure")
}

type toolTestErrorAnalyzer struct{}

func (toolTestErrorAnalyzer) Analyze(_ context.Context, id string, pairs, examples int) (testerrors.Report, error) {
	return testerrors.Report{RunID: id, Availability: "available", Evidence: testerrors.Evidence{Split: "test", Samples: 10, Errors: 2}}, nil
}

type toolFixture struct {
	server                *httptest.Server
	runID, modelA, modelB string
	inference             *toolInference
	store                 agenttools.Store
}

func toolCookie(label string) string {
	sum := sha256.Sum256([]byte(label))
	return hex.EncodeToString(sum[:])[:43]
}

func newToolFixture(t *testing.T, store agenttools.Store) *toolFixture {
	t.Helper()
	if store == nil {
		store = agenttools.NewMemoryStore()
	}
	owner := auth.User{ID: uuid.New(), Role: auth.Business, Status: auth.Active}
	sessions := map[string]auth.Session{}
	for _, entry := range []struct {
		cookie, csrf string
		user         auth.User
	}{{"owner", "csrf", owner}, {"owner-other-session", "other-csrf", owner}, {"other", "csrf", auth.User{ID: uuid.New(), Role: auth.Business, Status: auth.Active}}, {"annotator", "csrf", auth.User{ID: uuid.New(), Role: auth.Annotator, Status: auth.Active}}} {
		sum := sha256.Sum256([]byte(toolCookie(entry.cookie)))
		sessions[hex.EncodeToString(sum[:])] = auth.Session{User: entry.user, CSRFToken: entry.csrf}
	}
	runID, a, c := uuid.NewString(), uuid.NewString(), uuid.NewString()
	inference := &toolInference{}
	versions := modelregistry.NewMemoryRepository(modelregistry.Version{ID: a, DatasetID: "dataset", DatasetVersionID: "version-1", Metrics: map[string]any{"accuracy": 0.8}}, modelregistry.Version{ID: c, DatasetID: "dataset", DatasetVersionID: "version-1", Metrics: map[string]any{"accuracy": 0.9}})
	router := NewRouter(Dependencies{Auth: auth.New(toolSessions{sessions: sessions}), AgentTools: AgentToolOptions{TestErrors: toolTestErrorAnalyzer{}, SigningKey: []byte("01234567890123456789012345678901"), Operations: store}, ReadModels: toolReadModels{runID: runID}, ModelRegistry: modelregistry.NewService(versions), Workers: workers.Handler{Store: toolWorkers{}}, Inference: inference})
	s := httptest.NewServer(router)
	t.Cleanup(s.Close)
	return &toolFixture{server: s, runID: runID, modelA: a, modelB: c, inference: inference, store: store}
}
func (f *toolFixture) request(t *testing.T, method, path, cookie, csrf string, body any) (int, json.RawMessage) {
	t.Helper()
	var input []byte
	if body != nil {
		input, _ = json.Marshal(body)
	}
	r, err := http.NewRequest(method, f.server.URL+path, bytes.NewReader(input))
	if err != nil {
		t.Fatal(err)
	}
	if cookie != "" {
		r.AddCookie(&http.Cookie{Name: "fv_session", Value: toolCookie(cookie)})
	}
	r.Header.Set("X-CSRF-Token", csrf)
	r.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(r)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, raw
}
func (f *toolFixture) approve(t *testing.T, tool string, args any, id string) map[string]any {
	t.Helper()
	status, raw := f.request(t, "POST", "/api/agent-tools/approvals/"+tool, "owner", "csrf", map[string]any{"arguments": args, "operation_id": id})
	if status != 200 {
		t.Fatalf("approve %s: %d %s", tool, status, raw)
	}
	var receipt map[string]any
	_ = json.Unmarshal(raw, &receipt)
	return map[string]any{"arguments": receipt["arguments"], "operation_id": id, "approval": receipt["approval"]}
}
func TestAgentToolsAllFifteenReachPlatformApplications(t *testing.T) {
	f := newToolFixture(t, nil)
	for _, test := range []struct {
		name string
		args any
	}{
		{"datasets.list", map[string]any{}},
		{"datasets.get_version", map[string]any{"dataset_version_id": "version-1"}},
		{"training.list_runs", map[string]any{}},
		{"training.get_run", map[string]any{"run_id": f.runID}},
		{"training.get_metrics", map[string]any{"run_id": f.runID, "limit": 10}},
		{"training.analyze_test_errors", map[string]any{"run_id": f.runID, "max_examples": 3}},
		{"models.list_versions", map[string]any{}},
		{"models.get_version", map[string]any{"model_version_id": f.modelA}},
		{"models.compare", map[string]any{"model_version_ids": []string{f.modelA, f.modelB}}},
		{"catalog.list_backbones", map[string]any{}},
		{"workers.get_capacity", map[string]any{}},
		{"training.validate_plan", map[string]any{"dataset_version_id": "version-1", "backbone_key": "dinov3_vits16_lvd1689m"}},
	} {
		t.Run(test.name, func(t *testing.T) {
			status, raw := f.request(t, "POST", "/api/agent-tools/invoke/"+test.name, "owner", "csrf", map[string]any{"arguments": test.args})
			if status != 200 {
				t.Fatalf("%d %s", status, raw)
			}
			var result map[string]any
			_ = json.Unmarshal(raw, &result)
			if result["tool"] != test.name || result["data"] == nil || result["observed_at"] == nil {
				t.Fatalf("bad result %s", raw)
			}
			if bytes.Contains(raw, []byte("s3://")) || bytes.Contains(raw, []byte("cache_dir")) {
				t.Fatalf("storage paths exposed: %s", raw)
			}
		})
	}
	args := map[string]any{"dataset_version_id": "version-1", "backbone_key": "dinov3_vits16_lvd1689m"}
	body := f.approve(t, "training.submit_plan", args, uuid.NewString())
	status, raw := f.request(t, "POST", "/api/agent-tools/invoke/training.submit_plan", "owner", "csrf", body)
	if status != 202 {
		t.Fatalf("submit: %d %s", status, raw)
	}
	var result struct {
		Data struct {
			Run map[string]any `json:"training_run"`
		} `json:"data"`
	}
	_ = json.Unmarshal(raw, &result)
	cancel := f.approve(t, "training.cancel_run", map[string]any{"run_id": result.Data.Run["run_id"]}, uuid.NewString())
	status, raw = f.request(t, "POST", "/api/agent-tools/invoke/training.cancel_run", "owner", "csrf", cancel)
	if status != 200 {
		t.Fatalf("cancel: %d %s", status, raw)
	}
	predict := f.approve(t, "inference.run", map[string]any{"model_version_id": f.modelA, "dataset_version_id": "version-1", "sample_id": "sample"}, uuid.NewString())
	status, raw = f.request(t, "POST", "/api/agent-tools/invoke/inference.run", "owner", "csrf", predict)
	if status != 200 || f.inference.calls.Load() != 1 {
		t.Fatalf("inference: %d %s", status, raw)
	}
}
func TestAgentToolAuthorizationApprovalAndParameterBinding(t *testing.T) {
	f := newToolFixture(t, nil)
	args := map[string]any{"dataset_version_id": "version-1", "model_version_id": f.modelA, "sample_id": "sample"}
	body := f.approve(t, "inference.run", args, uuid.NewString())
	for _, test := range []struct {
		cookie, csrf string
		body         any
	}{{"", "", body}, {"owner", "", body}, {"other", "csrf", body}, {"owner-other-session", "other-csrf", body}, {"annotator", "csrf", body}, {"owner", "csrf", map[string]any{"arguments": args, "operation_id": uuid.NewString(), "approved": true}}, {"owner", "csrf", map[string]any{"arguments": map[string]any{"dataset_version_id": "version-1", "model_version_id": f.modelA, "sample_id": "changed"}, "operation_id": body["operation_id"], "approval": body["approval"]}}} {
		status, _ := f.request(t, "POST", "/api/agent-tools/invoke/inference.run", test.cookie, test.csrf, test.body)
		if status < 400 {
			t.Fatalf("unauthorized action accepted: %+v", test)
		}
	}
	if f.inference.calls.Load() != 0 {
		t.Fatal("unauthorized inference executed")
	}
	status, raw := f.request(t, "POST", "/api/agent-tools/invoke/datasets.list", "annotator", "csrf", map[string]any{"arguments": map[string]any{}})
	if status != 200 {
		t.Fatalf("annotator read blocked %d %s", status, raw)
	}
}
func TestConcurrentWritesReplayAndUnknownNeverRedispatch(t *testing.T) {
	f := newToolFixture(t, nil)
	body := f.approve(t, "inference.run", map[string]any{"dataset_version_id": "version-1", "model_version_id": f.modelA, "sample_id": "sample"}, uuid.NewString())
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			status, raw := f.request(t, "POST", "/api/agent-tools/invoke/inference.run", "owner", "csrf", body)
			if status != 200 && status != 409 {
				t.Errorf("%d %s", status, raw)
			}
		}()
	}
	wg.Wait()
	if f.inference.calls.Load() != 1 {
		t.Fatal("duplicate inference", f.inference.calls.Load())
	}
	status, _ := f.request(t, "GET", "/api/agent-tools/operations/"+body["operation_id"].(string), "other", "csrf", nil)
	if status != 404 {
		t.Fatal("cross-user operation exposed")
	}
	failure := newToolFixture(t, failingToolFinish{agenttools.NewMemoryStore()})
	b := failure.approve(t, "inference.run", map[string]any{"dataset_version_id": "version-1", "model_version_id": failure.modelA, "sample_id": "sample"}, uuid.NewString())
	status, raw := failure.request(t, "POST", "/api/agent-tools/invoke/inference.run", "owner", "csrf", b)
	if status != 503 || !bytes.Contains(raw, []byte("OPERATION_OUTCOME_UNKNOWN")) {
		t.Fatalf("unknown lost: %d %s", status, raw)
	}
	status, _ = failure.request(t, "POST", "/api/agent-tools/invoke/inference.run", "owner", "csrf", b)
	if status != 409 || failure.inference.calls.Load() != 1 {
		t.Fatal("unknown replayed")
	}
}
func TestTrainingPreviewAndCreationShareNormalization(t *testing.T) {
	f := newToolFixture(t, nil)
	for _, approved := range modelcatalog.Approved() {
		backbone := approved.Key
		body := f.approve(t, "training.submit_plan", map[string]any{"dataset_version_id": "version-1", "backbone_key": backbone}, uuid.NewString())
		status, raw := f.request(t, "POST", "/api/agent-tools/invoke/training.submit_plan", "owner", "csrf", body)
		if status != 202 {
			t.Fatalf("normalized %s: %d %s", backbone, status, raw)
		}
		status, again := f.request(t, "POST", "/api/agent-tools/invoke/training.submit_plan", "owner", "csrf", body)
		if status != 202 || !bytes.Equal(raw, again) {
			t.Fatalf("replay changed: %d %s", status, again)
		}
	}
	status, _ := f.request(t, "POST", "/api/agent-tools/invoke/training.validate_plan", "owner", "csrf", map[string]any{"arguments": map[string]any{"dataset_version_id": "version-1", "backbone_key": "imagenet_resnet50_a1_in1k", "head_config": map[string]any{"lora_enabled": true}}})
	if status != 422 {
		t.Fatal("invalid LoRA accepted")
	}
}

func TestTestErrorToolRequiresAuthenticationAndCurrentRole(t *testing.T) {
	f := newToolFixture(t, nil)
	for _, c := range []struct {
		cookie, csrf string
		status       int
	}{{"", "", 401}, {"owner", "", 403}, {"annotator", "csrf", 403}, {"owner", "csrf", 200}} {
		status, raw := f.request(t, "POST", "/api/agent-tools/invoke/training.analyze_test_errors", c.cookie, c.csrf, map[string]any{"arguments": map[string]any{"run_id": f.runID}})
		if status != c.status {
			t.Fatalf("auth %s: %d %s", c.cookie, status, raw)
		}
	}
	for _, n := range []int{0, 21} {
		status, _ := f.request(t, "POST", "/api/agent-tools/invoke/training.analyze_test_errors", "owner", "csrf", map[string]any{"arguments": map[string]any{"run_id": f.runID, "max_examples": n}})
		if status != 422 {
			t.Fatal("unbounded samples accepted", status)
		}
	}
}

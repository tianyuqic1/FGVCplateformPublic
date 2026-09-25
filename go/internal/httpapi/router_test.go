package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
)

func TestHealthReportsControlPlaneIdentity(t *testing.T) {
	server := httptest.NewServer(NewRouter(Dependencies{}))
	t.Cleanup(server.Close)

	response, err := http.Get(server.URL + "/api/health")
	if err != nil {
		t.Fatalf("GET /api/health: %v", err)
	}
	defer response.Body.Close()

	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusOK)
	}
	var payload struct {
		Status  string `json:"status"`
		Runtime string `json:"runtime"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if payload.Status != "ok" || payload.Runtime != "go-control-plane" {
		t.Fatalf("payload = %#v", payload)
	}
}

func TestSwaggerUIAndEmbeddedSpecifications(t *testing.T) {
	handler := NewRouter(Dependencies{})

	redirect := httptest.NewRecorder()
	handler.ServeHTTP(redirect, httptest.NewRequest(http.MethodGet, "/swagger", nil))
	if redirect.Code != http.StatusPermanentRedirect || redirect.Header().Get("Location") != "/swagger/" {
		t.Fatalf("swagger redirect = %d %q", redirect.Code, redirect.Header().Get("Location"))
	}

	page := httptest.NewRecorder()
	handler.ServeHTTP(page, httptest.NewRequest(http.MethodGet, "/swagger/", nil))
	if page.Code != http.StatusOK ||
		!strings.Contains(page.Body.String(), "FineVision API") ||
		!strings.Contains(page.Body.String(), "/openapi/finevision.yaml") ||
		!strings.Contains(page.Body.String(), "/openapi/hardware.yaml") ||
		!strings.Contains(page.Body.String(), `defaultModelsExpandDepth: -1`) ||
		!strings.Contains(page.Body.String(), `docExpansion: "list"`) {
		t.Fatalf("swagger page = %d %q", page.Code, page.Body.String())
	}

	asset := httptest.NewRecorder()
	handler.ServeHTTP(asset, httptest.NewRequest(http.MethodGet, "/swagger/swagger-ui-bundle.js", nil))
	if asset.Code != http.StatusOK || !strings.Contains(asset.Header().Get("Content-Type"), "javascript") {
		t.Fatalf("swagger asset = %d %q", asset.Code, asset.Header().Get("Content-Type"))
	}

	for _, specification := range []struct {
		path  string
		title string
	}{
		{path: "/openapi/finevision.yaml", title: "FineVision Control Plane API"},
		{path: "/openapi/hardware.yaml", title: "FineVision Hardware Monitoring"},
	} {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, specification.path, nil))
		if response.Code != http.StatusOK || !strings.Contains(response.Header().Get("Content-Type"), "application/yaml") || !strings.Contains(response.Body.String(), specification.title) {
			t.Fatalf("specification %s = %d %q", specification.path, response.Code, response.Body.String())
		}
	}
}

type fakeLLMGateway struct{}

func (fakeLLMGateway) Generate(context.Context, llm.GatewayRequest) (llm.GatewayResult, error) {
	return llm.GatewayResult{Provider: "fake", Model: "fixed", Text: `{"summary":"check the margin"}`}, nil
}

func TestLLMAssistanceIsServedByGoApplication(t *testing.T) {
	server := httptest.NewServer(NewRouter(Dependencies{LLMApplication: llm.NewApplication(fakeLLMGateway{})}))
	t.Cleanup(server.Close)
	response, err := http.Post(server.URL+"/api/llm/assist", "application/json", bytes.NewBufferString(`{
        "task":"inference_explanation","context":{"confidence":0.4}
    }`))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", response.StatusCode)
	}
	var payload map[string]map[string]any
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		t.Fatal(err)
	}
	if payload["assistance"]["advisory_only"] != true || payload["assistance"]["summary"] != "check the margin" {
		t.Fatalf("assistance = %#v", payload)
	}
}

func TestModelWeightCatalogExposesOnlyApprovedPhaseTwoBackbonesAndPreservesCanonicalObject(t *testing.T) {
	server := httptest.NewServer(NewRouter(Dependencies{}))
	t.Cleanup(server.Close)
	response, err := http.Get(server.URL + "/api/model-weights")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	var catalog map[string][]map[string]any
	if err := json.NewDecoder(response.Body).Decode(&catalog); err != nil {
		t.Fatal(err)
	}
	if len(catalog["weights"]) != 3 || catalog["weights"][0]["backbone_key"] != "dinov3_vits16_lvd1689m" ||
		catalog["weights"][1]["backbone_key"] != "imagenet_vits16_augreg_in21k_ft_in1k" ||
		catalog["weights"][2]["backbone_key"] != "imagenet_resnet50_a1_in1k" {
		t.Fatalf("catalog = %#v", catalog)
	}
	request, _ := http.NewRequest(http.MethodDelete, server.URL+"/api/model-weights/dinov3_vits", nil)
	eviction, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer eviction.Body.Close()
	if eviction.StatusCode != http.StatusNotFound && eviction.StatusCode != http.StatusMethodNotAllowed {
		t.Fatalf("weight deletion must be unavailable, got %d", eviction.StatusCode)
	}
}

func TestCreateTrainingRunAcceptsStableImageNetBackboneKeyAndRejectsIdentityOverride(t *testing.T) {
	server := httptest.NewServer(NewRouter(Dependencies{}))
	t.Cleanup(server.Close)

	response, err := http.Post(server.URL+"/api/training-runs", "application/json", bytes.NewBufferString(`{
        "dataset_version_id":"version-1",
        "backbone_key":"imagenet_vits16_augreg_in21k_ft_in1k",
        "head_config":{"head_type":"ridge_linear"}
    }`))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusAccepted {
		body, _ := io.ReadAll(response.Body)
		t.Fatalf("create status = %d: %s", response.StatusCode, body)
	}
	var created map[string]map[string]any
	if err := json.NewDecoder(response.Body).Decode(&created); err != nil {
		t.Fatal(err)
	}
	config := created["training_run"]["extractor_config"].(map[string]any)
	if config["model_name"] != "vit_small_patch16_224.augreg_in21k_ft_in1k" || config["pretrained_sha256"] == "" {
		t.Fatalf("extractor config = %#v", config)
	}

	rejected, err := http.Post(server.URL+"/api/training-runs", "application/json", bytes.NewBufferString(`{
        "dataset_version_id":"version-1",
        "backbone_key":"imagenet_resnet50_a1_in1k",
        "extractor_config":{"model_name":"unmanaged_model"}
    }`))
	if err != nil {
		t.Fatal(err)
	}
	defer rejected.Body.Close()
	if rejected.StatusCode != http.StatusUnprocessableEntity {
		t.Fatalf("override status = %d", rejected.StatusCode)
	}
}

func TestTrainingLifecycleHTTPContract(t *testing.T) {
	server := httptest.NewServer(NewRouter(Dependencies{}))
	t.Cleanup(server.Close)

	createdResponse, err := http.Post(server.URL+"/api/training-runs", "application/json", bytes.NewBufferString(`{
        "dataset_id":"dataset-1",
        "dataset_version_id":"version-1",
        "backbone_id":"dinov3_vits16",
        "head_config":{"head_type":"ridge_linear"}
    }`))
	if err != nil {
		t.Fatal(err)
	}
	defer createdResponse.Body.Close()
	if createdResponse.StatusCode != http.StatusAccepted {
		t.Fatalf("create status = %d", createdResponse.StatusCode)
	}
	var created map[string]map[string]any
	if err := json.NewDecoder(createdResponse.Body).Decode(&created); err != nil {
		t.Fatal(err)
	}
	jobID := created["training_run"]["job_id"].(string)

	claimResponse, err := http.Post(server.URL+"/internal/training-jobs/"+jobID+"/claim", "application/json", bytes.NewBufferString(`{
        "dispatch_generation":1,
        "worker_id":"worker-test"
    }`))
	if err != nil {
		t.Fatal(err)
	}
	defer claimResponse.Body.Close()
	if claimResponse.StatusCode != http.StatusOK {
		t.Fatalf("claim status = %d", claimResponse.StatusCode)
	}
	var claim map[string]any
	if err := json.NewDecoder(claimResponse.Body).Decode(&claim); err != nil {
		t.Fatal(err)
	}
	if claim["disposition"] != "claimed" || claim["attempt_id"] == "" {
		t.Fatalf("claim = %#v", claim)
	}
}

func TestCreateTrainingRunAcceptsExistingFrontendContract(t *testing.T) {
	server := httptest.NewServer(NewRouter(Dependencies{}))
	t.Cleanup(server.Close)

	createdResponse, err := http.Post(server.URL+"/api/training-runs", "application/json", bytes.NewBufferString(`{
        "dataset_version_id":"version-1",
        "extractor":"dinov3_vits",
        "feature_batch_size":4,
        "image_size":224,
        "feature_pool":"cls",
        "head_config":{"head_type":"ridge_linear"}
    }`))
	if err != nil {
		t.Fatal(err)
	}
	defer createdResponse.Body.Close()
	if createdResponse.StatusCode != http.StatusAccepted {
		body, _ := io.ReadAll(createdResponse.Body)
		t.Fatalf("create status = %d, want %d: %s", createdResponse.StatusCode, http.StatusAccepted, body)
	}
	var created map[string]map[string]any
	if err := json.NewDecoder(createdResponse.Body).Decode(&created); err != nil {
		t.Fatal(err)
	}
	jobID := created["training_run"]["job_id"].(string)
	claimResponse, err := http.Post(server.URL+"/internal/training-jobs/"+jobID+"/claim", "application/json", bytes.NewBufferString(`{
        "dispatch_generation":1,"worker_id":"worker-test"
    }`))
	if err != nil {
		t.Fatal(err)
	}
	defer claimResponse.Body.Close()
	var claim map[string]any
	if err := json.NewDecoder(claimResponse.Body).Decode(&claim); err != nil {
		t.Fatal(err)
	}
	payload := claim["payload"].(map[string]any)
	if payload["extractor"] != "dinov3_vits" || payload["batch_size"] != float64(4) {
		t.Fatalf("claim payload = %#v", payload)
	}
	extractorConfig := payload["extractor_config"].(map[string]any)
	if extractorConfig["model_name"] != "vit_small_patch16_dinov3" || extractorConfig["feature_pool"] != "cls" {
		t.Fatalf("extractor config = %#v", extractorConfig)
	}
}

func TestFencedWorkerReceivesStableErrorEnvelope(t *testing.T) {
	server := httptest.NewServer(NewRouter(Dependencies{}))
	t.Cleanup(server.Close)
	missingJob := "11111111-1111-4111-8111-111111111111"
	request, err := http.NewRequest(http.MethodPost, server.URL+"/internal/training-jobs/"+missingJob+"/heartbeat", bytes.NewBufferString(`{
        "attempt_id":"22222222-2222-4222-8222-222222222222",
        "execution_epoch":1
    }`))
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("status = %d", response.StatusCode)
	}
	var payload struct {
		Error struct {
			Code      string `json:"code"`
			RequestID string `json:"request_id"`
		} `json:"error"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		t.Fatal(err)
	}
	if payload.Error.Code != "NOT_FOUND" || payload.Error.RequestID == "" {
		t.Fatalf("error = %#v", payload.Error)
	}
}

type metricsReadModels struct {
	emptyReadModels
	query MetricsQuery
}

func (models *metricsReadModels) GetTrainingRunMetrics(_ context.Context, runID string, query MetricsQuery) (map[string]any, error) {
	models.query = query
	return map[string]any{
		"training_run_id": runID,
		"run_status":      "running",
		"metric_points":   []map[string]any{{"id": 8, "metric_name": "eval_accuracy", "step": 4, "value": .94}},
		"next_cursor":     8,
		"attempts":        []map[string]any{},
		"poll_after_ms":   2000,
	}, nil
}

func TestTrainingMetricHTTPContractsAcceptPointsAndExposeCursor(t *testing.T) {
	repository := training.NewMemoryRepository()
	lifecycle := training.NewService(repository, time.Now, 2*time.Minute)
	reads := &metricsReadModels{}
	server := httptest.NewServer(NewRouter(Dependencies{Lifecycle: lifecycle, ReadModels: reads}))
	t.Cleanup(server.Close)
	created, err := lifecycle.Create(context.Background(), training.CreateCommand{
		DatasetID: "dataset-1", DatasetVersionID: "version-1", BackboneID: "dinov3_vits16",
		Payload: map[string]any{}, MaxAttempts: 2,
	})
	if err != nil {
		t.Fatal(err)
	}
	claim, err := lifecycle.Claim(context.Background(), training.ClaimCommand{JobID: created.JobID, DispatchGeneration: 1, WorkerID: "worker"})
	if err != nil {
		t.Fatal(err)
	}
	progressBody := fmt.Sprintf(`{
        "attempt_id":%q,"execution_epoch":%d,"progress":{"current_stage":"head"},
        "metric_points":[{"name":"eval_accuracy","step":4,"value":0.94,"context":{"split":"validation"}}]
    }`, claim.AttemptID, claim.ExecutionEpoch)
	response, err := http.Post(server.URL+"/internal/training-jobs/"+created.JobID+"/progress", "application/json", bytes.NewBufferString(progressBody))
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusNoContent {
		t.Fatalf("progress status = %d", response.StatusCode)
	}
	aggregate, _ := repository.Get(context.Background(), created.JobID)
	if len(aggregate.Metrics) != 1 || aggregate.Metrics[0].Step != 4 {
		t.Fatalf("metrics = %#v", aggregate.Metrics)
	}

	metricsResponse, err := http.Get(server.URL + "/api/training-runs/" + created.TrainingRunID + "/metrics?metric_name=eval_accuracy&after_id=3&limit=25")
	if err != nil {
		t.Fatal(err)
	}
	defer metricsResponse.Body.Close()
	if metricsResponse.StatusCode != http.StatusOK {
		t.Fatalf("metrics status = %d", metricsResponse.StatusCode)
	}
	var payload map[string]any
	if err := json.NewDecoder(metricsResponse.Body).Decode(&payload); err != nil {
		t.Fatal(err)
	}
	if payload["next_cursor"] != float64(8) || reads.query.MetricName != "eval_accuracy" || reads.query.AfterID != 3 || reads.query.Limit != 25 {
		t.Fatalf("payload/query = %#v / %#v", payload, reads.query)
	}
}

func TestModelRegistryHTTPContractsComparePromoteAndSetAlias(t *testing.T) {
	firstID := "11111111-1111-4111-8111-111111111111"
	secondID := "22222222-2222-4222-8222-222222222222"
	repository := modelregistry.NewMemoryRepository(
		modelregistry.Version{ID: firstID, ModelKey: "model-one", DatasetID: "birds", DatasetVersionID: "birds-v1", Status: modelregistry.StatusCandidate, Metrics: map[string]any{"accuracy": .94}, EvaluationContext: map[string]any{"protocol_fingerprint": "eval-v1"}},
		modelregistry.Version{ID: secondID, ModelKey: "model-two", DatasetID: "birds", DatasetVersionID: "birds-v1", Status: modelregistry.StatusCandidate, Metrics: map[string]any{"accuracy": .91}, EvaluationContext: map[string]any{"protocol_fingerprint": "eval-v1"}},
	)
	server := httptest.NewServer(NewRouter(Dependencies{ModelRegistry: modelregistry.NewService(repository)}))
	t.Cleanup(server.Close)

	listResponse, err := http.Get(server.URL + "/api/model-versions?dataset_id=birds&status=candidate")
	if err != nil {
		t.Fatal(err)
	}
	defer listResponse.Body.Close()
	var listPayload map[string][]map[string]any
	if err := json.NewDecoder(listResponse.Body).Decode(&listPayload); err != nil {
		t.Fatal(err)
	}
	if len(listPayload["model_versions"]) != 2 {
		t.Fatalf("model list = %#v", listPayload)
	}

	compareResponse, err := http.Post(server.URL+"/api/model-version-comparisons", "application/json", bytes.NewBufferString(fmt.Sprintf(`{"model_version_ids":[%q,%q]}`, firstID, secondID)))
	if err != nil {
		t.Fatal(err)
	}
	defer compareResponse.Body.Close()
	var comparison map[string]map[string]any
	if err := json.NewDecoder(compareResponse.Body).Decode(&comparison); err != nil {
		t.Fatal(err)
	}
	if comparison["comparison"]["comparable"] != true {
		t.Fatalf("comparison = %#v", comparison)
	}

	for _, target := range []string{"staging"} {
		response, err := http.Post(server.URL+"/api/model-versions/"+firstID+"/promote", "application/json", bytes.NewBufferString(fmt.Sprintf(`{"target_status":%q,"actor":"tester","reason":"verified"}`, target)))
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusOK {
			t.Fatalf("promote %s status = %d", target, response.StatusCode)
		}
	}
	blocked, err := http.Post(server.URL+"/api/model-versions/"+firstID+"/promote", "application/json", bytes.NewBufferString(`{"target_status":"production","actor":"tester","reason":"no exporter"}`))
	if err != nil {
		t.Fatal(err)
	}
	blocked.Body.Close()
	if blocked.StatusCode != http.StatusConflict {
		t.Fatalf("publication without exporter = %d", blocked.StatusCode)
	}
	request, _ := http.NewRequest(http.MethodPut, server.URL+"/api/model-aliases/challenger", bytes.NewBufferString(fmt.Sprintf(`{"dataset_id":"birds","model_version_id":%q,"actor":"tester","reason":"release"}`, firstID)))
	request.Header.Set("Content-Type", "application/json")
	aliasResponse, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer aliasResponse.Body.Close()
	if aliasResponse.StatusCode != http.StatusOK {
		t.Fatalf("alias status = %d", aliasResponse.StatusCode)
	}
}

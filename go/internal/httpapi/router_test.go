package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
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

func TestModelWeightCatalogOnlyExposesManagedViTSAndPreservesCanonicalObject(t *testing.T) {
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
	if len(catalog["weights"]) != 1 || catalog["weights"][0]["preset"] != "dinov3_vits" {
		t.Fatalf("catalog = %#v", catalog)
	}
	request, _ := http.NewRequest(http.MethodDelete, server.URL+"/api/model-weights/dinov3_vits", nil)
	eviction, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer eviction.Body.Close()
	var result map[string]any
	if err := json.NewDecoder(eviction.Body).Decode(&result); err != nil {
		t.Fatal(err)
	}
	if result["deleted"] != false || result["canonical_preserved"] != true {
		t.Fatalf("eviction = %#v", result)
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

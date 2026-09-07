package llmgateway_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llmgateway"
)

type fakeProvider struct{ request llm.GatewayRequest }

func (provider *fakeProvider) Generate(_ context.Context, request llm.GatewayRequest) (llm.GatewayResult, error) {
	provider.request = request
	return llm.GatewayResult{Provider: "fake", Model: "fixed-model", Text: `{"summary":"safe"}`}, nil
}

func TestGatewayRequiresInternalCredentialAndDoesNotAcceptModelOverride(t *testing.T) {
	t.Parallel()
	provider := &fakeProvider{}
	server := httptest.NewServer(llmgateway.NewRouter(provider, "internal-secret"))
	t.Cleanup(server.Close)
	body := []byte(`{"request_id":"r1","task":"review_assistance","prompt":"inspect","model":"attacker-model"}`)

	unauthorized, err := http.Post(server.URL+"/internal/v1/generate", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	unauthorized.Body.Close()
	if unauthorized.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unauthorized status = %d", unauthorized.StatusCode)
	}
	request, _ := http.NewRequest(http.MethodPost, server.URL+"/internal/v1/generate", bytes.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer internal-secret")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", response.StatusCode)
	}
	var payload map[string]json.RawMessage
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		t.Fatal(err)
	}
	if provider.request.Prompt != "inspect" || provider.request.Task != "review_assistance" {
		t.Fatalf("provider request = %#v", provider.request)
	}
}

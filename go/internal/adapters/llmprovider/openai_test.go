package llmprovider_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/llmprovider"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
)

func TestProviderUsesContainerModelAndCredential(t *testing.T) {
	t.Parallel()
	var received map[string]any
	providerServer := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Bearer secret" {
			t.Fatalf("authorization = %q", request.Header.Get("Authorization"))
		}
		if err := json.NewDecoder(request.Body).Decode(&received); err != nil {
			t.Fatal(err)
		}
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"choices":[{"message":{"content":"{\"summary\":\"ok\"}"}}],"usage":{"prompt_tokens":10,"completion_tokens":4}}`))
	}))
	t.Cleanup(providerServer.Close)
	provider := llmprovider.NewOpenAICompatible(providerServer.URL, "configured-model", "secret", providerServer.Client())

	result, err := provider.Generate(context.Background(), llm.GatewayRequest{
		RequestID: "request-1", Prompt: "explain", ImageDataURL: "data:image/png;base64,AA==",
	})
	if err != nil {
		t.Fatal(err)
	}
	if received["model"] != "configured-model" || result.Model != "configured-model" || result.Text == "" {
		t.Fatalf("request/result = %#v / %#v", received, result)
	}
	messages := received["messages"].([]any)
	content := messages[0].(map[string]any)["content"].([]any)
	if content[1].(map[string]any)["image_url"].(map[string]any)["url"] != "data:image/png;base64,AA==" || received["max_tokens"] != float64(4096) {
		t.Fatal("vision payload or output budget missing")
	}
}

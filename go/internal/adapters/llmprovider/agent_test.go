package llmprovider

import (
	"context"
	"encoding/json"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/modelcontract"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestAgentChatWireProtocolSupportsToolOnlyMessages(t *testing.T) {
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]json.RawMessage
		if json.NewDecoder(r.Body).Decode(&body) != nil {
			t.Error("invalid request")
		}
		if _, ok := body["response_format"]; ok {
			t.Error("chat must not force JSON output")
		}
		if r.Header.Get("Authorization") != "Bearer key" || r.Header.Get("X-Request-ID") != "run:0" {
			t.Error("missing provider metadata")
		}
		var messages []modelcontract.Message
		_ = json.Unmarshal(body["messages"], &messages)
		if len(messages) != 4 || messages[3].ToolCallID != "old" {
			t.Error("lost tool context")
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"model":"test","choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"next","type":"function","function":{"name":"datasets__list","arguments":"{}"}}]}}],"usage":{"prompt_tokens":12,"completion_tokens":3}}`))
	}))
	defer provider.Close()
	client := NewOpenAICompatible(provider.URL, "test", "key", provider.Client())
	out, e := client.Chat(context.Background(), modelcontract.Request{RequestID: "run:0", Messages: []modelcontract.Message{{Role: "system", Content: "help"}, {Role: "user", Content: "list"}, {Role: "assistant", ToolCalls: []modelcontract.Call{{ID: "old", Type: "function", Function: modelcontract.Function{Name: "datasets__list", Arguments: `{}`}}}}, {Role: "tool", ToolCallID: "old", Content: `{}`}}})
	if e != nil || len(out.Message.ToolCalls) != 1 || out.InputTokens != 12 {
		t.Fatalf("tool-only response rejected %+v %v", out, e)
	}
}

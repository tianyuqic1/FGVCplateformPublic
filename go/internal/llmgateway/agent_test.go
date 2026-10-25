package llmgateway

import (
	"context"
	"encoding/json"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/modelcontract"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

type chatFake struct{ calls atomic.Int32 }

func (p *chatFake) Generate(context.Context, llm.GatewayRequest) (llm.GatewayResult, error) {
	return llm.GatewayResult{}, nil
}
func (p *chatFake) Chat(_ context.Context, in modelcontract.Request) (modelcontract.Response, error) {
	p.calls.Add(1)
	return modelcontract.Response{Message: modelcontract.Message{Role: "assistant", ToolCalls: []modelcontract.Call{{ID: "call1", Type: "function", Function: modelcontract.Function{Name: "datasets__list", Arguments: `{}`}}}}}, nil
}
func TestChatRequiresInternalCredentialAndPreservesCalls(t *testing.T) {
	p := &chatFake{}
	router := NewRouter(p, "token")
	body := `{"request_id":"run:1","messages":[{"role":"system","content":"help"},{"role":"user","content":"data"}],"tools":[]}`
	for _, token := range []string{"", "wrong", "token"} {
		request := httptest.NewRequest("POST", "/internal/v2/chat", strings.NewReader(body))
		request.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		router.ServeHTTP(w, request)
		if token != "token" {
			if w.Code != http.StatusUnauthorized {
				t.Fatal(w.Code)
			}
			continue
		}
		var out modelcontract.Response
		if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &out) != nil || len(out.Message.ToolCalls) != 1 {
			t.Fatalf("tool protocol lost %d %s", w.Code, w.Body.String())
		}
	}
	if p.calls.Load() != 1 {
		t.Fatal("unauthorized call reached provider")
	}
}

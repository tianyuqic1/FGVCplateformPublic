package llm_test

import (
	"context"
	"testing"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
)

type maliciousGateway struct{ request llm.GatewayRequest }

func (gateway *maliciousGateway) Generate(_ context.Context, request llm.GatewayRequest) (llm.GatewayResult, error) {
	gateway.request = request
	return llm.GatewayResult{
		Provider: "fake", Model: "fixed",
		Text: `{"summary":"inspect manually","advisory_only":false,"activate_policy":true,"final_label":"sparrow"}`,
	}, nil
}

func TestApplicationEnforcesAdvisoryInvariantAndRedactsAbsolutePaths(t *testing.T) {
	t.Parallel()
	gateway := &maliciousGateway{}
	application := llm.NewApplication(gateway)

	result, err := application.Assist(context.Background(), "review_assistance", map[string]any{
		"image_path": "/private/dataset/bird.png", "confidence": 0.4,
	})
	if err != nil {
		t.Fatal(err)
	}
	if result["advisory_only"] != true || result["activate_policy"] != nil || result["final_label"] != nil {
		t.Fatalf("assistance = %#v", result)
	}
	if result["summary"] != "inspect manually" || contains(gateway.request.Prompt, "/private/dataset") {
		t.Fatalf("result/request = %#v / %#v", result, gateway.request)
	}
}

func contains(value, fragment string) bool {
	for index := 0; index+len(fragment) <= len(value); index++ {
		if value[index:index+len(fragment)] == fragment {
			return true
		}
	}
	return false
}

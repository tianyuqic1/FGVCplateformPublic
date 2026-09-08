package einocard

import (
	"context"
	"errors"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/datasetcard"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
	"testing"
)

type fakeGateway struct {
	text    string
	err     error
	calls   int
	request llm.GatewayRequest
}

func (f *fakeGateway) Generate(_ context.Context, r llm.GatewayRequest) (llm.GatewayResult, error) {
	f.calls++
	f.request = r
	return llm.GatewayResult{Text: f.text, Model: "fixture", InputTokens: 10, OutputTokens: 20}, f.err
}
func TestWorkflowDoesNotSendImagesAndValidatesOutput(t *testing.T) {
	for _, tc := range []struct {
		name, text string
		valid      bool
	}{
		{"valid", `{"domain":"几何","summary":"仅根据标签推测形状任务","ood_policy":"待确认","review_guidance":"人工检查","confusion_pairs":[{"label_a":"circle","label_b":"square","reason":"可能易混"}]}`, true},
		{"unknown field", `{"domain":"x","summary":"x","ood_policy":"x","review_guidance":"x","confusion_pairs":[],"class_count":999}`, false},
		{"invented label", `{"domain":"x","summary":"x","ood_policy":"x","review_guidance":"x","confusion_pairs":[{"label_a":"bird","label_b":"square","reason":"x"}]}`, false},
		{"missing fields", `{"summary":"nonempty"}`, false},
		{"trailing json", `{"summary":"x"} {}`, false},
		{"empty", `{"summary":""}`, false},
		{"markdown", "```json\n{}\n```", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			fake := &fakeGateway{text: tc.text}
			g, err := New(context.Background(), fake)
			if err != nil {
				t.Fatal(err)
			}
			got, err := g.Generate(context.Background(), datasetcard.Input{Facts: datasetcard.Facts{Classes: []string{"circle", "square"}, SampleCount: 12}})
			if (err == nil) != tc.valid {
				t.Fatalf("valid=%v err=%v", tc.valid, err)
			}
			if fake.calls != 1 || fake.request.ImageDataURL != "" || fake.request.Task != "dataset_card_generation" {
				t.Fatal("wrong request boundary")
			}
			if tc.valid && (got.Basis != "labels_and_statistics_only" || got.Card.Task != "image_classification" || got.Model != "fixture") {
				t.Fatal(got)
			}
		})
	}
}
func TestProviderFailureIsNotRetried(t *testing.T) {
	fake := &fakeGateway{err: errors.New("provider unavailable")}
	g, err := New(context.Background(), fake)
	if err != nil {
		t.Fatal(err)
	}
	_, err = g.Generate(context.Background(), datasetcard.Input{})
	if err == nil || fake.calls != 1 {
		t.Fatal("provider failure must propagate without hidden retry")
	}
}

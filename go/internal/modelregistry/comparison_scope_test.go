package modelregistry_test

import (
	"context"
	"errors"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
	"testing"
)

func TestComparisonScopeAndProtocol(t *testing.T) {
	for _, tc := range []struct {
		name, dataset, version, protocol string
		reject, comparable               bool
	}{
		{"same scope different backbone", "birds", "v1", "p1", false, true},
		{"different dataset", "flowers", "v1", "p1", true, false},
		{"different data version", "birds", "v2", "p1", true, false},
		{"missing dataset", "", "v1", "p1", true, false},
		{"missing version", "birds", "", "p1", true, false},
		{"different protocol", "birds", "v1", "p2", false, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a := modelregistry.Version{ID: "a", DatasetID: "birds", DatasetVersionID: "v1", BackboneKey: "dino", ReleaseVersion: "v1.0.0", EvaluationContext: map[string]any{"protocol_fingerprint": "p1"}}
			b := modelregistry.Version{ID: "b", DatasetID: tc.dataset, DatasetVersionID: tc.version, BackboneKey: "resnet", ReleaseVersion: "v2.0.0", Metrics: map[string]any{"macro_f1": 0.8}, EvaluationContext: map[string]any{"protocol_fingerprint": tc.protocol}}
			result, err := modelregistry.NewService(modelregistry.NewMemoryRepository(a, b)).Compare(context.Background(), []string{"a", "b"})
			if tc.reject {
				if !errors.Is(err, modelregistry.ErrInvalid) {
					t.Fatalf("expected validation error: %v", err)
				}
				return
			}
			if err != nil || result.Comparable != tc.comparable {
				t.Fatalf("result=%+v err=%v", result, err)
			}
			if _, exists := result.Versions[1].Metrics["accuracy"]; exists {
				t.Fatal("missing accuracy must remain absent")
			}
		})
	}
}

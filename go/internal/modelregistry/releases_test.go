package modelregistry_test

import (
	"context"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
	"sync"
	"testing"
)

func TestDatasetScopedReleaseRules(t *testing.T) {
	base := modelregistry.Version{ID: "base", DatasetID: "birds", DatasetVersionID: "dv1", BackboneKey: "dino", TrainingConfig: map[string]any{"lora_enabled": false}}
	first, _, seq := modelregistry.NextRelease(base, nil)
	if first != "v1.0.0" || seq != 1 {
		t.Fatal(first, seq)
	}
	previous := base
	previous.ReleaseVersion = first
	previous.ReleaseSequence = seq
	previous.ReleaseSignature = modelregistry.ModelSignature(base)
	for _, scenario := range []string{"patch", "data", "backbone", "weights", "lora", "rename", "epochs", "both"} {
		t.Run(scenario, func(t *testing.T) {
			v := base
			v.TrainingConfig = map[string]any{"lora_enabled": false}
			want := "v1.0.1"
			switch scenario {
			case "data":
				v.DatasetVersionID = "dv2"
				want = "v1.1.0"
			case "backbone":
				v.BackboneKey = "resnet"
				want = "v2.0.0"
			case "weights":
				v.TrainingConfig["pretrained_sha256"] = "changed"
				want = "v2.0.0"
			case "lora":
				v.TrainingConfig["lora_enabled"] = true
				want = "v2.0.0"
			case "rename":
				v.DatasetName = "renamed"
			case "epochs":
				v.TrainingConfig["epochs"] = 100
			case "both":
				v.DatasetVersionID = "dv2"
				v.BackboneKey = "new"
				want = "v2.0.0"
			}
			got, _, _ := modelregistry.NextRelease(v, &previous)
			if got != want {
				t.Fatalf("got %s want %s", got, want)
			}
		})
	}
	previous.TrainingConfig = map[string]any{"lora_enabled": true, "lora_rank": 8}
	previous.ReleaseSignature = modelregistry.ModelSignature(previous)
	base.TrainingConfig = map[string]any{"lora_enabled": true, "lora_rank": 16}
	if got, _, _ := modelregistry.NextRelease(base, &previous); got != "v2.0.0" {
		t.Fatal(got)
	}
}

func TestAdditionalPrecisionAndIndependentDataset(t *testing.T) {
	ctx := context.Background()
	repo := modelregistry.NewMemoryRepository(
		modelregistry.Version{ID: "a", DatasetID: "one", Status: modelregistry.StatusCandidate},
		modelregistry.Version{ID: "b", DatasetID: "two", Status: modelregistry.StatusCandidate})
	service := modelregistry.NewService(repo).WithPublication(testExporter{}, testVerifier{})
	for _, id := range []string{"a", "b"} {
		v, err := service.Publish(ctx, id, "test", "initial")
		if err != nil || v.ReleaseVersion != "v1.0.0" {
			t.Fatal(v, err)
		}
	}
	v, err := service.PublishPrecision(ctx, "a", "test", "half", "FP16")
	if err != nil || v.ReleaseVersion != "v1.0.0" || len(v.Artifacts) != 4 {
		t.Fatal(v, err)
	}
	v, err = service.PublishPrecision(ctx, "a", "test", "retry", "FP16")
	if err != nil || len(v.Artifacts) != 4 {
		t.Fatal(v, err)
	}
	if _, err = service.PublishPrecision(ctx, "a", "test", "bad", "INT8"); err == nil {
		t.Fatal("invalid precision accepted")
	}
}

func TestConcurrentDatasetAllocations(t *testing.T) {
	repo := modelregistry.NewMemoryRepository(modelregistry.Version{ID: "a", DatasetID: "one", Status: modelregistry.StatusCandidate}, modelregistry.Version{ID: "b", DatasetID: "one", Status: modelregistry.StatusCandidate})
	service := modelregistry.NewService(repo).WithPublication(testExporter{}, testVerifier{})
	var wg sync.WaitGroup
	for _, id := range []string{"a", "b"} {
		wg.Add(1)
		go func(id string) {
			defer wg.Done()
			if _, err := service.Publish(context.Background(), id, "test", "release"); err != nil {
				t.Error(err)
			}
		}(id)
	}
	wg.Wait()
	a, _ := service.Get(context.Background(), "a")
	b, _ := service.Get(context.Background(), "b")
	if a.ReleaseVersion == b.ReleaseVersion {
		t.Fatal("duplicate versions")
	}
}

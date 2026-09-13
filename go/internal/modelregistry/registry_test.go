package modelregistry_test

import (
	"context"
	"errors"
	"testing"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
)

func TestComparisonRejectsIncompatibleDatasetVersionsAndPreservesMissingMetrics(t *testing.T) {
	t.Parallel()
	repository := modelregistry.NewMemoryRepository(
		modelregistry.Version{ID: "one", DatasetID: "dataset", DatasetVersionID: "v1", Status: modelregistry.StatusCandidate,
			Metrics: map[string]any{"accuracy": .9}, EvaluationContext: map[string]any{"protocol_fingerprint": "p1"}},
		modelregistry.Version{ID: "two", DatasetID: "dataset", DatasetVersionID: "v2", Status: modelregistry.StatusCandidate,
			Metrics: map[string]any{"macro_f1": .8}, EvaluationContext: map[string]any{"protocol_fingerprint": "p2"}},
	)
	_, err := modelregistry.NewService(repository).Compare(context.Background(), []string{"one", "two"})
	if !errors.Is(err, modelregistry.ErrInvalid) {
		t.Fatalf("expected scope rejection, got %v", err)
	}
}

func TestGovernedPromotionAndDatasetScopedAliases(t *testing.T) {
	t.Parallel()
	repository := modelregistry.NewMemoryRepository(
		modelregistry.Version{ID: "candidate", DatasetID: "dataset", Status: modelregistry.StatusCandidate},
	)
	service := modelregistry.NewService(repository)
	if _, err := service.Promote(context.Background(), "candidate", modelregistry.StatusProduction, "operator", "skip staging"); !errors.Is(err, modelregistry.ErrConflict) {
		t.Fatalf("direct production promotion = %v", err)
	}
	if _, err := service.Promote(context.Background(), "candidate", modelregistry.StatusStaging, "operator", "validated"); err != nil {
		t.Fatal(err)
	}
	service.WithPublication(testExporter{}, testVerifier{})
	if _, err := service.Promote(context.Background(), "candidate", modelregistry.StatusProduction, "operator", "release"); err != nil {
		t.Fatal(err)
	}
	alias, err := service.SetAlias(context.Background(), "dataset", "champion", "candidate", "operator", "release")
	if err != nil {
		t.Fatal(err)
	}
	if alias.ModelVersion.Status != modelregistry.StatusProduction || len(alias.ModelVersion.Aliases) != 1 {
		t.Fatalf("alias = %#v", alias)
	}
}

func TestComparisonWithoutProtocolFingerprintIsNotRankable(t *testing.T) {
	t.Parallel()
	repository := modelregistry.NewMemoryRepository(
		modelregistry.Version{ID: "one", DatasetID: "dataset", DatasetVersionID: "v1", Status: modelregistry.StatusCandidate},
		modelregistry.Version{ID: "two", DatasetID: "dataset", DatasetVersionID: "v1", Status: modelregistry.StatusCandidate},
	)
	comparison, err := modelregistry.NewService(repository).Compare(context.Background(), []string{"one", "two"})
	if err != nil {
		t.Fatal(err)
	}
	if comparison.Comparable || len(comparison.Warnings) != 1 || comparison.Warnings[0].Code != "EVALUATION_PROTOCOL_MISSING" {
		t.Fatalf("comparison = %#v", comparison)
	}
}

func TestReassigningAliasRemovesItFromPreviousVersion(t *testing.T) {
	t.Parallel()
	repository := modelregistry.NewMemoryRepository(
		modelregistry.Version{ID: "one", DatasetID: "dataset", Status: modelregistry.StatusProduction},
		modelregistry.Version{ID: "two", DatasetID: "dataset", Status: modelregistry.StatusProduction},
	)
	service := modelregistry.NewService(repository)
	if _, err := service.SetAlias(context.Background(), "dataset", "champion", "one", "operator", "first"); err != nil {
		t.Fatal(err)
	}
	if _, err := service.SetAlias(context.Background(), "dataset", "champion", "two", "operator", "replacement"); err != nil {
		t.Fatal(err)
	}
	previous, _ := service.Get(context.Background(), "one")
	current, _ := service.Get(context.Background(), "two")
	if len(previous.Aliases) != 0 || len(current.Aliases) != 1 || current.Aliases[0] != "champion" {
		t.Fatalf("previous/current aliases = %#v/%#v", previous.Aliases, current.Aliases)
	}
}

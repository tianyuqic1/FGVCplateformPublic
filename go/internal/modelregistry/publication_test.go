package modelregistry_test

import (
	"context"
	"errors"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
	"testing"
)

type testExporter struct {
	fail         bool
	beforeReturn func()
	wrongScope   bool
	full         bool
}

func (e testExporter) Export(_ context.Context, v modelregistry.Version) ([]artifact.Descriptor, error) {
	if e.fail {
		return nil, errors.New("conversion failed")
	}
	if e.beforeReturn != nil {
		e.beforeReturn()
	}
	result := []artifact.Descriptor{}
	kinds := []string{"head_pt", "head_onnx"}
	if e.full {
		kinds = []string{"full_pt", "full_onnx"}
	}
	for _, kind := range kinds {
		a := artifact.Descriptor{ArtifactID: kind, ArtifactType: kind, URI: "s3://test/" + kind, SizeBytes: 4, DatasetVersionID: v.DatasetVersionID, TrainingRunID: v.TrainingRunID, Metadata: map[string]any{"model_version_id": v.ID, "parity_passed": true, "precision": v.ReleasePrecision}}
		if e.wrongScope {
			a.DatasetVersionID = "wrong"
		}
		result = append(result, a)
	}
	return result, nil
}

func TestFullModelPublicationRejectsHeadOnlyDowngrade(t *testing.T) {
	for _, full := range []bool{false, true} {
		repo := modelregistry.NewMemoryRepository(modelregistry.Version{ID: "v", DatasetVersionID: "dv", TrainingRunID: "tr", HeadType: "image_classifier_v2", Status: modelregistry.StatusCandidate})
		service := modelregistry.NewService(repo).WithPublication(testExporter{full: full}, testVerifier{})
		result, err := service.Publish(context.Background(), "v", "tester", "full model")
		if !full && err == nil {
			t.Fatal("accepted head-only export for full model")
		}
		if full && (err != nil || result.Status != modelregistry.StatusProduction) {
			t.Fatalf("full publication: %v", err)
		}
	}
}

type testVerifier struct{ fail bool }

func (v testVerifier) Verify(context.Context, artifact.Descriptor) error {
	if v.fail {
		return errors.New("bad sha")
	}
	return nil
}

func TestPublicationFailsClosedAndCommitsBothArtifacts(t *testing.T) {
	for _, scenario := range []string{"success", "conversion", "integrity", "scope", "archive_race"} {
		t.Run(scenario, func(t *testing.T) {
			ctx := context.Background()
			repo := modelregistry.NewMemoryRepository(modelregistry.Version{ID: "v", DatasetVersionID: "dv", TrainingRunID: "tr", Status: modelregistry.StatusCandidate})
			exporter := testExporter{fail: scenario == "conversion", wrongScope: scenario == "scope"}
			service := modelregistry.NewService(repo)
			if scenario == "archive_race" {
				exporter.beforeReturn = func() {
					_, err := service.Archive(ctx, "v", "tester", "withdraw")
					if err != nil {
						t.Fatal(err)
					}
				}
			}
			service.WithPublication(exporter, testVerifier{fail: scenario == "integrity"})
			result, err := service.Promote(ctx, "v", modelregistry.StatusProduction, "tester", "release")
			actual, _ := service.Get(ctx, "v")
			if scenario == "success" {
				if err != nil || result.Status != modelregistry.StatusProduction || len(result.Artifacts) != 2 {
					t.Fatalf("result=%+v err=%v", result, err)
				}
				retry, err := service.Promote(ctx, "v", modelregistry.StatusProduction, "tester", "retry")
				if err != nil || len(retry.Artifacts) != 2 {
					t.Fatal("retry duplicated artifacts")
				}
			} else if err == nil || actual.Status == modelregistry.StatusProduction || len(actual.Artifacts) != 0 || actual.ReleaseVersion != "" {
				t.Fatalf("failed export became published: %+v %v", actual, err)
			}
		})
	}
}

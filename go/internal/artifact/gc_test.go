package artifact_test

import (
	"context"
	"testing"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

type staticReferences struct {
	state artifact.PhysicalReferenceState
}

func (repository staticReferences) PhysicalReferences(context.Context, string, string) (artifact.PhysicalReferenceState, error) {
	return repository.state, nil
}

func TestGarbageCollectionRequiresZeroLogicalAndSharedReferences(t *testing.T) {
	digest := "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	tests := []struct {
		name  string
		state artifact.PhysicalReferenceState
		want  bool
	}{
		{name: "orphan", state: artifact.PhysicalReferenceState{CandidateExists: true}, want: true},
		{name: "shared bytes", state: artifact.PhysicalReferenceState{CandidateExists: true, OtherRecords: 1}},
		{name: "model or inference reference", state: artifact.PhysicalReferenceState{CandidateExists: true, Relationships: 1}},
		{name: "missing record", state: artifact.PhysicalReferenceState{}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			allowed, err := artifact.NewGarbageCollectionGuard(staticReferences{test.state}).CanDeletePhysicalObject(context.Background(), digest, "artifact-1")
			if err != nil || allowed != test.want {
				t.Fatalf("allowed/error = %t/%v", allowed, err)
			}
		})
	}
}

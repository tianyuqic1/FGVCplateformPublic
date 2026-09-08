package artifact

import (
	"context"
	"errors"
	"strings"
)

var ErrReferenceCheck = errors.New("artifact reference check failed")

type PhysicalReferenceState struct {
	CandidateExists bool
	OtherRecords    int64
	Relationships   int64
}

type ReferenceRepository interface {
	PhysicalReferences(context.Context, string, string) (PhysicalReferenceState, error)
}

// GarbageCollectionGuard separates logical archive/delete from physical object
// deletion. A content-addressed object is eligible only after the selected
// Artifact Record is unreferenced and no other record shares its SHA-256.
type GarbageCollectionGuard struct {
	references ReferenceRepository
}

func NewGarbageCollectionGuard(references ReferenceRepository) *GarbageCollectionGuard {
	return &GarbageCollectionGuard{references: references}
}

func (guard *GarbageCollectionGuard) CanDeletePhysicalObject(ctx context.Context, sha256, candidateArtifactID string) (bool, error) {
	if len(sha256) != 64 || strings.Trim(sha256, "0123456789abcdef") != "" || candidateArtifactID == "" {
		return false, ErrReferenceCheck
	}
	state, err := guard.references.PhysicalReferences(ctx, sha256, candidateArtifactID)
	if err != nil {
		return false, err
	}
	return state.CandidateExists && state.OtherRecords == 0 && state.Relationships == 0, nil
}

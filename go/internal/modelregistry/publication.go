package modelregistry

import (
	"context"
	"fmt"
	"strings"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

type HeadExporter interface {
	Export(context.Context, Version) ([]artifact.Descriptor, error)
}
type ArtifactVerifier interface {
	Verify(context.Context, artifact.Descriptor) error
}
type PublicationRepository interface {
	CompletePublication(context.Context, Version, []artifact.Descriptor, string, string) (Version, error)
}

func (s *Service) WithPublication(exporter HeadExporter, verifier ArtifactVerifier) *Service {
	s.exporter, s.verifier = exporter, verifier
	return s
}

func (s *Service) Publish(ctx context.Context, id, actor, reason string) (Version, error) {
	if strings.TrimSpace(actor) == "" || strings.TrimSpace(reason) == "" {
		return Version{}, fmt.Errorf("%w: actor and reason required", ErrInvalid)
	}
	current, err := s.repository.Get(ctx, id)
	if err != nil {
		return Version{}, err
	}
	if current.Status != StatusCandidate && current.Status != StatusStaging && current.Status != StatusProduction {
		return Version{}, fmt.Errorf("%w: model cannot be published", ErrConflict)
	}
	if current.Status == StatusProduction {
		for _, a := range current.Artifacts {
			if ((current.HeadType == "image_classifier_v2" && a.ArtifactType == "full_onnx") || (current.HeadType != "image_classifier_v2" && a.ArtifactType == "head_onnx")) && a.Metadata["model_version_id"] == current.ID {
				return current, nil
			}
		}
	}
	repository, ok := s.repository.(PublicationRepository)
	if !ok || s.exporter == nil || s.verifier == nil {
		return Version{}, fmt.Errorf("%w: model export is unavailable", ErrConflict)
	}
	artifacts, err := s.exporter.Export(ctx, current)
	if err != nil {
		return Version{}, fmt.Errorf("%w: model export failed; model remains unpublished", ErrConflict)
	}
	if len(artifacts) != 2 {
		return Version{}, fmt.Errorf("%w: incomplete publication artifacts", ErrConflict)
	}
	kinds := map[string]bool{}
	ptKind, onnxKind := "head_pt", "head_onnx"
	if current.HeadType == "image_classifier_v2" {
		ptKind, onnxKind = "full_pt", "full_onnx"
	}
	for _, a := range artifacts {
		if (a.ArtifactType != ptKind && a.ArtifactType != onnxKind) || kinds[a.ArtifactType] || !strings.HasPrefix(a.URI, "s3://") || a.SizeBytes <= 0 || a.DatasetVersionID != current.DatasetVersionID || a.TrainingRunID != current.TrainingRunID || a.Metadata["model_version_id"] != current.ID || a.Metadata["parity_passed"] != true {
			return Version{}, fmt.Errorf("%w: publication artifact contract mismatch", ErrConflict)
		}
		if err := s.verifier.Verify(ctx, a); err != nil {
			return Version{}, fmt.Errorf("%w: publication integrity verification failed", ErrConflict)
		}
		kinds[a.ArtifactType] = true
	}
	return repository.CompletePublication(ctx, current, artifacts, actor, reason)
}

func (r *MemoryRepository) CompletePublication(_ context.Context, expected Version, artifacts []artifact.Descriptor, actor, reason string) (Version, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	current, ok := r.versions[expected.ID]
	if !ok {
		return Version{}, ErrNotFound
	}
	if current.Status != expected.Status || !current.UpdatedAt.Equal(expected.UpdatedAt) {
		return Version{}, ErrConflict
	}
	current.Status = StatusProduction
	current.Artifacts = append(current.Artifacts, artifacts...)
	current.UpdatedAt = r.clock().UTC()
	current.Events = append(current.Events, Event{EventType: "published", Actor: actor, Reason: reason, CreatedAt: current.UpdatedAt})
	r.versions[current.ID] = current
	return cloneVersion(current), nil
}

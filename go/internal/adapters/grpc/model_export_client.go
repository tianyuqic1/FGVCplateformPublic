package grpcadapter

import (
	"context"
	"encoding/json"
	"fmt"
	computev1 "github.com/tianyuqic1/FGVCplateformPublic/go/api/proto/finevision/compute/v1"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
)

type HeadExporter struct{ Client computev1.ModelExportClient }

func (e HeadExporter) Export(ctx context.Context, version modelregistry.Version) ([]artifact.Descriptor, error) {
	var source *artifact.Descriptor
	for _, a := range version.Artifacts {
		if a.ArtifactType == "model_bundle" {
			copy := a
			source = &copy
		}
	}
	if source == nil {
		return nil, fmt.Errorf("source bundle missing")
	}
	result, err := e.Client.ExportHead(ctx, &computev1.ExportHeadRequest{
		Precision:      version.ReleasePrecision,
		ModelVersionId: version.ID, DatasetVersionId: version.DatasetVersionID, TrainingRunId: version.TrainingRunID,
		SourceBundle: &computev1.ArtifactDescriptor{ArtifactId: source.ArtifactID, ArtifactType: source.ArtifactType, Uri: source.URI, Sha256: source.SHA256, SizeBytes: source.SizeBytes, ContentType: source.ContentType, StorageVersion: source.StorageVersion, Producer: source.Producer, DatasetVersionId: source.DatasetVersionID, TrainingRunId: source.TrainingRunID, SchemaVersion: int32(source.SchemaVersion)},
	})
	if err != nil {
		return nil, err
	}
	data, err := json.Marshal(result.AsMap())
	if err != nil {
		return nil, err
	}
	var payload struct {
		Artifacts []artifact.Descriptor `json:"artifacts"`
	}
	err = json.Unmarshal(data, &payload)
	return payload.Artifacts, err
}

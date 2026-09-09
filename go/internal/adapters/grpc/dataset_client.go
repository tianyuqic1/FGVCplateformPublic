package grpcadapter

import (
	"context"
	computev1 "github.com/tianyuqic1/FGVCplateformPublic/go/api/proto/finevision/compute/v1"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type DatasetScanner struct {
	Client computev1.DatasetComputeClient
}

func (s DatasetScanner) Scan(ctx context.Context, a artifact.Descriptor, datasetID, versionID string) (map[string]any, error) {
	result, err := s.Client.Scan(ctx, &computev1.ScanDatasetRequest{DatasetId: datasetID, DatasetVersionId: versionID, Archive: &computev1.ArtifactDescriptor{ArtifactId: a.ArtifactID, ArtifactType: a.ArtifactType, Uri: a.URI, Sha256: a.SHA256, SizeBytes: a.SizeBytes, ContentType: a.ContentType, StorageVersion: a.StorageVersion, Producer: a.Producer, DatasetVersionId: versionID, SchemaVersion: 1}}, grpc.MaxCallRecvMsgSize(128<<20))
	if status.Code(err) == codes.InvalidArgument {
		return nil, dataset.ErrInvalidArchive
	}
	if err != nil {
		return nil, err
	}
	return result.AsMap(), nil
}

package dataset

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"os"
)

const MaxUploadImages = 100_000
const MaxUploadBytes int64 = 5 << 30

var ErrConflict = errors.New("dataset version already exists")
var ErrInvalidArchive = errors.New("invalid dataset archive")

type Scanner interface {
	Scan(context.Context, artifact.Descriptor, string, string) (map[string]any, error)
}
type Repository interface {
	Save(context.Context, string, string, string, artifact.Descriptor, artifact.Descriptor, map[string]any) error
}
type Service struct {
	Store      artifact.Store
	Scanner    Scanner
	Repository Repository
}

func (s *Service) Import(ctx context.Context, key, versionKey, archivePath string) (map[string]any, error) {
	versionID := uuid.NewString()
	archive, err := s.Store.PutFile(ctx, archivePath, artifact.PutRequest{ArtifactID: uuid.NewString(), ArtifactType: "dataset_archive", ContentType: "application/zip", Producer: "go-dataset-upload", DatasetVersionID: versionID})
	if err != nil {
		return nil, err
	}
	manifest, err := s.Scanner.Scan(ctx, archive, key, versionID)
	if err != nil {
		return nil, err
	}
	readiness, ok := manifest["readiness"].(map[string]any)
	if !ok {
		return nil, errors.New("scanner returned no readiness report")
	}
	for _, field := range []string{"sample_count", "class_count"} {
		value, ok := readiness[field].(float64)
		if !ok || value < 1 || value > MaxUploadImages || value != float64(int(value)) {
			return nil, ErrInvalidArchive
		}
	}
	data, err := json.Marshal(manifest)
	if err != nil {
		return nil, err
	}
	file, err := os.CreateTemp("", "dataset-manifest-*")
	if err != nil {
		return nil, err
	}
	defer os.Remove(file.Name())
	_, err = file.Write(data)
	closeErr := file.Close()
	if err != nil {
		return nil, err
	}
	if closeErr != nil {
		return nil, closeErr
	}
	descriptor, err := s.Store.PutFile(ctx, file.Name(), artifact.PutRequest{ArtifactID: uuid.NewString(), ArtifactType: "dataset_manifest", ContentType: "application/json", Producer: "python-dataset-scan", DatasetVersionID: versionID})
	if err != nil {
		return nil, err
	}
	if err = s.Repository.Save(ctx, key, versionKey, versionID, archive, descriptor, manifest); err != nil {
		return nil, err
	}
	status := "draft"
	if readiness["ready"] == true {
		status = "ready"
	}
	summary := map[string]any{"id": key, "dataset_id": key, "name": key, "dataset_version_id": versionID, "latest_version_id": versionID, "version_key": versionKey, "classes": manifest["classes"], "class_count": readiness["class_count"], "sample_count": readiness["sample_count"], "readiness": readiness, "split_counts": manifest["split_counts"], "status": status}
	return map[string]any{"dataset": summary, "version": summary, "upload": map[string]any{"image_count": readiness["sample_count"], "class_count": readiness["class_count"]}}, nil
}

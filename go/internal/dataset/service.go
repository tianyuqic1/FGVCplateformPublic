package dataset

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

const MaxUploadImages = 100_000
const MaxUploadBytes int64 = 5 << 30

var ErrConflict = errors.New("数据集已更新或请求重复，请刷新后重试")
var ErrInvalidArchive = errors.New("图片损坏或 ImageFolder 目录结构不合法")
var ErrNotFound = errors.New("数据集版本不存在")
var ErrInvalid = errors.New("数据集输入无效")

type Scanner interface {
	Scan(context.Context, artifact.Descriptor, string, string) (map[string]any, error)
}
type Snapshot struct {
	DatasetID, DatasetKey, Name, VersionID string
	Number                                 int
	Archive                                artifact.Descriptor
	Manifest                               map[string]any
}
type Feedback struct {
	ID              string               `json:"id"`
	Label           string               `json:"label"`
	InputRef        string               `json:"-"`
	SourceVersionID string               `json:"source_version_id"`
	ModelVersionID  string               `json:"model_version_id"`
	ReviewID        string               `json:"review_id"`
	InferenceRunID  string               `json:"inference_run_id"`
	ImageArtifact   *artifact.Descriptor `json:"-"`
}
type Publication struct {
	DatasetID, DatasetKey, Name, VersionID, ParentID, RequestID, Fingerprint, SourceType string
	Number                                                                               int
	Archive, ManifestArtifact                                                            artifact.Descriptor
	Manifest, Changes                                                                    map[string]any
	Feedback                                                                             []Feedback
}
type Repository interface {
	FindPublication(context.Context, string, string) (map[string]any, error)
	Base(context.Context, string, string) (Snapshot, error)
	Candidates(context.Context, string) ([]Feedback, error)
	Save(context.Context, Publication) (map[string]any, error)
}
type Service struct {
	Store      artifact.Store
	Scanner    Scanner
	Repository Repository
	// Legacy reviewed uploads may be read only under this explicitly configured root.
	UploadRoot string
}

func (s *Service) Import(ctx context.Context, name, requestID, archivePath string) (map[string]any, error) {
	name = strings.TrimSpace(name)
	if name == "" || utf8.RuneCountInString(name) > 120 {
		return nil, fmt.Errorf("%w: 请填写 1–120 字的数据集名称", ErrInvalid)
	}
	return s.publish(ctx, "", "", name, requestID, archivePath, nil)
}
func (s *Service) Expand(ctx context.Context, datasetID, baseID, requestID, archivePath string, feedbackIDs []string) (map[string]any, error) {
	if datasetID == "" || baseID == "" {
		return nil, fmt.Errorf("%w: 必须指定数据集和基础版本", ErrInvalid)
	}
	return s.publish(ctx, datasetID, baseID, "", requestID, archivePath, feedbackIDs)
}
func (s *Service) publish(ctx context.Context, datasetID, baseID, name, requestID, archivePath string, feedbackIDs []string) (map[string]any, error) {
	if _, err := uuid.Parse(requestID); err != nil {
		return nil, fmt.Errorf("%w: request_id 必须是 UUID", ErrInvalid)
	}
	feedbackIDs = append([]string{}, feedbackIDs...)
	sort.Strings(feedbackIDs)
	for i, id := range feedbackIDs {
		if id == "" || (i > 0 && feedbackIDs[i-1] == id) {
			return nil, fmt.Errorf("%w: 重复反馈记录", ErrInvalid)
		}
	}
	if len(feedbackIDs) > 10000 {
		return nil, ErrInvalid
	}
	p := Publication{DatasetID: uuid.NewString(), VersionID: uuid.NewString(), RequestID: requestID, Name: name, Number: 1, SourceType: "initial_import", Changes: map[string]any{}}
	p.DatasetKey = p.DatasetID
	var incoming artifact.Descriptor
	if archivePath != "" {
		var err error
		incoming, err = s.Store.PutFile(ctx, archivePath, artifact.PutRequest{ArtifactID: uuid.NewString(), ArtifactType: "dataset_archive", ContentType: "application/zip", Producer: "go-dataset-upload", DatasetVersionID: p.VersionID})
		if err != nil {
			return nil, err
		}
	}
	encoded, _ := json.Marshal([]any{datasetID, baseID, name, incoming.SHA256, feedbackIDs})
	digest := sha256.Sum256(encoded)
	p.Fingerprint = hex.EncodeToString(digest[:])
	existing, err := s.Repository.FindPublication(ctx, requestID, p.Fingerprint)
	if err != nil || existing != nil {
		return existing, err
	}
	p.Archive = incoming
	if datasetID != "" {
		base, err := s.Repository.Base(ctx, datasetID, baseID)
		if err != nil {
			return nil, err
		}
		p.DatasetID, p.DatasetKey, p.Name, p.ParentID, p.Number = base.DatasetID, base.DatasetKey, base.Name, base.VersionID, base.Number+1
		p.SourceType = "manual_expansion"
		if len(feedbackIDs) > 0 {
			candidates, err := s.Repository.Candidates(ctx, p.DatasetID)
			if err != nil {
				return nil, err
			}
			byID := map[string]Feedback{}
			for _, f := range candidates {
				byID[f.ID] = f
			}
			for _, id := range feedbackIDs {
				f, ok := byID[id]
				if !ok {
					return nil, fmt.Errorf("%w: 反馈不属于此数据集、尚未确认标签或已经纳入版本", ErrInvalid)
				}
				p.Feedback = append(p.Feedback, f)
			}
			p.SourceType = "review_feedback"
			if archivePath != "" {
				p.SourceType = "mixed_expansion"
			}
		}
		path, changes, err := s.merge(ctx, base, archivePath, p.Feedback)
		if err != nil {
			return nil, err
		}
		defer os.Remove(path)
		p.Changes = changes
		p.Archive, err = s.Store.PutFile(ctx, path, artifact.PutRequest{ArtifactID: uuid.NewString(), ArtifactType: "dataset_archive", ContentType: "application/zip", Producer: "go-dataset-expansion", DatasetVersionID: p.VersionID})
		if err != nil {
			return nil, err
		}
	}
	if p.Archive.URI == "" {
		return nil, fmt.Errorf("%w: 请选择图片或复核候选", ErrInvalid)
	}
	manifest, err := s.Scanner.Scan(ctx, p.Archive, p.DatasetKey, p.VersionID)
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
	if p.ParentID == "" {
		p.Changes = map[string]any{"added_count": readiness["sample_count"], "duplicate_count": 0}
	}
	manifest["parent_version_id"] = p.ParentID
	manifest["change_summary"] = p.Changes
	if len(p.Feedback) > 0 {
		manifest["feedback_sources"] = p.Feedback
	}
	p.Manifest = manifest
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
	p.ManifestArtifact, err = s.Store.PutFile(ctx, file.Name(), artifact.PutRequest{ArtifactID: uuid.NewString(), ArtifactType: "dataset_manifest", ContentType: "application/json", Producer: "go-dataset-publish", DatasetVersionID: p.VersionID})
	if err != nil {
		return nil, err
	}
	return s.Repository.Save(ctx, p)
}

func Result(p Publication) map[string]any {
	readiness, _ := p.Manifest["readiness"].(map[string]any)
	state := "draft"
	if readiness["ready"] == true {
		state = "ready"
	}
	summary := map[string]any{"id": p.DatasetKey, "dataset_id": p.DatasetKey, "name": p.Name, "dataset_version_id": p.VersionID, "latest_version_id": p.VersionID, "version_key": p.VersionID, "version_number": p.Number, "parent_version_id": p.ParentID, "source_type": p.SourceType, "change_summary": p.Changes, "classes": p.Manifest["classes"], "class_count": readiness["class_count"], "sample_count": readiness["sample_count"], "readiness": readiness, "split_counts": p.Manifest["split_counts"], "status": state, "has_weights": false, "model_count": 0}
	upload := map[string]any{"image_count": readiness["sample_count"], "class_count": readiness["class_count"]}
	for key, value := range p.Changes {
		if key != "sample_sources" {
			upload[key] = value
		}
	}
	delete(summary, "change_summary")
	summary["change_summary"] = upload
	return map[string]any{"dataset": summary, "version": summary, "upload": upload}
}

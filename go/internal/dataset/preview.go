package dataset

import (
	"archive/zip"
	"context"
	"encoding/json"
	"fmt"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"net/http"
	"os"
	"sync"
)

type PreviewRepository interface {
	PreviewSnapshot(context.Context, string) (Snapshot, error)
}
type PreviewSample struct {
	ID    string `json:"sample_id"`
	Label string `json:"label"`
	Split string `json:"split"`
	Path  string `json:"path,omitempty"`
}
type PreviewService struct {
	Repository                PreviewRepository
	Store                     artifact.Store
	mu                        sync.Mutex
	cache, filename, identity string
}

func (s *PreviewService) Snapshot(ctx context.Context, id string) (Snapshot, []PreviewSample, error) {
	snapshot, err := s.Repository.PreviewSnapshot(ctx, id)
	if err != nil {
		return snapshot, nil, err
	}
	raw, err := json.Marshal(snapshot.Manifest["samples"])
	if err != nil {
		return snapshot, nil, err
	}
	var samples []PreviewSample
	if err = json.Unmarshal(raw, &samples); err != nil {
		return snapshot, nil, err
	}
	return snapshot, samples, nil
}
func (s *PreviewService) Image(ctx context.Context, version, id string) ([]byte, string, error) {
	snapshot, samples, err := s.Snapshot(ctx, version)
	if err != nil {
		return nil, "", err
	}
	var name string
	for _, sample := range samples {
		if sample.ID == id {
			name = sample.Path
			break
		}
	}
	if name == "" {
		return nil, "", ErrNotFound
	}
	if !safePath(name) || !imageExtension(name) {
		return nil, "", ErrInvalidArchive
	}
	a := snapshot.Archive
	if a.SizeBytes <= 0 || a.SizeBytes > MaxUploadBytes+(128<<20) || len(a.SHA256) != 64 {
		return nil, "", ErrInvalidArchive
	}
	// Keep at most one verified archive per process. Serialize replacement/read so
	// another request cannot remove a ZIP while it is being opened. No extraction.
	s.mu.Lock()
	defer s.mu.Unlock()
	if err = ctx.Err(); err != nil {
		return nil, "", err
	}
	identity := fmt.Sprintf("%s:%s:%d", a.URI, a.SHA256, a.SizeBytes)
	if s.identity != identity {
		if s.cache != "" {
			if err = os.RemoveAll(s.cache); err != nil {
				return nil, "", err
			}
		}
		s.cache, s.filename, s.identity = "", "", ""
		s.cache, err = os.MkdirTemp("", "dataset-preview-")
		if err != nil {
			return nil, "", err
		}
		s.filename, err = s.Store.MaterializeVerified(ctx, a, s.cache)
		if err != nil {
			os.RemoveAll(s.cache)
			s.cache = ""
			return nil, "", err
		}
		s.identity = identity
	}
	archive, err := zip.OpenReader(s.filename)
	if err != nil {
		return nil, "", err
	}
	defer archive.Close()
	for _, entry := range archive.File {
		if entry.Name != name {
			continue
		}
		if entry.UncompressedSize64 > maxImageBytes || entry.FileInfo().Mode()&os.ModeSymlink != 0 {
			return nil, "", ErrInvalidArchive
		}
		reader, err := entry.Open()
		if err != nil {
			return nil, "", err
		}
		data, err := readImage(reader)
		reader.Close()
		if err != nil {
			return nil, "", err
		}
		kind := http.DetectContentType(data)
		switch kind {
		case "image/jpeg", "image/png", "image/webp", "image/bmp":
		default:
			return nil, "", ErrInvalidArchive
		}
		return data, kind, nil
	}
	return nil, "", ErrNotFound
}
func (s *PreviewService) Close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.cache != "" {
		os.RemoveAll(s.cache)
	}
	s.cache, s.identity, s.filename = "", "", ""
}

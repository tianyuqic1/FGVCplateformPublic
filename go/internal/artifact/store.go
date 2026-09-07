package artifact

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var ErrIntegrity = errors.New("artifact integrity mismatch")

type Descriptor struct {
	ArtifactID       string         `json:"artifact_id"`
	ArtifactType     string         `json:"artifact_type"`
	URI              string         `json:"uri"`
	SHA256           string         `json:"sha256"`
	SizeBytes        int64          `json:"size_bytes"`
	ContentType      string         `json:"content_type"`
	StorageVersion   string         `json:"storage_version"`
	Producer         string         `json:"producer"`
	DatasetVersionID string         `json:"dataset_version_id,omitempty"`
	TrainingRunID    string         `json:"training_run_id,omitempty"`
	AttemptID        string         `json:"attempt_id,omitempty"`
	SchemaVersion    int            `json:"schema_version"`
	CreatedAt        time.Time      `json:"created_at"`
	VerifiedAt       time.Time      `json:"verified_at"`
	Metadata         map[string]any `json:"metadata"`
}

type PutRequest struct {
	ArtifactID       string
	ArtifactType     string
	ContentType      string
	Producer         string
	DatasetVersionID string
	TrainingRunID    string
	AttemptID        string
	Metadata         map[string]any
}

type Store interface {
	PutFile(context.Context, string, PutRequest) (Descriptor, error)
	MaterializeVerified(context.Context, Descriptor, string) (string, error)
	Verify(context.Context, Descriptor) error
}

type LocalStore struct {
	root string
}

func NewLocalStore(root string) *LocalStore {
	return &LocalStore{root: root}
}

func (s *LocalStore) PutFile(ctx context.Context, source string, request PutRequest) (Descriptor, error) {
	sha, size, err := digestPath(ctx, source)
	if err != nil {
		return Descriptor{}, err
	}
	destination := filepath.Join(s.root, request.ArtifactType, sha[:2], sha)
	if err := os.MkdirAll(filepath.Dir(destination), 0o750); err != nil {
		return Descriptor{}, err
	}
	if _, err := os.Stat(destination); err == nil {
		existingSHA, existingSize, digestErr := digestPath(ctx, destination)
		if digestErr != nil {
			return Descriptor{}, digestErr
		}
		if existingSHA != sha || existingSize != size {
			return Descriptor{}, fmt.Errorf("%w at immutable destination", ErrIntegrity)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return Descriptor{}, err
	} else if err := atomicCopy(ctx, source, destination, sha, size); err != nil {
		return Descriptor{}, err
	}
	absolute, err := filepath.Abs(destination)
	if err != nil {
		return Descriptor{}, err
	}
	now := time.Now().UTC()
	metadata := request.Metadata
	if metadata == nil {
		metadata = map[string]any{}
	}
	return Descriptor{
		ArtifactID: request.ArtifactID, ArtifactType: request.ArtifactType,
		URI: (&url.URL{Scheme: "file", Path: absolute}).String(), SHA256: sha, SizeBytes: size,
		ContentType: request.ContentType, StorageVersion: "local-v1", Producer: request.Producer,
		DatasetVersionID: request.DatasetVersionID, TrainingRunID: request.TrainingRunID,
		AttemptID: request.AttemptID, SchemaVersion: 1, CreatedAt: now, VerifiedAt: now, Metadata: metadata,
	}, nil
}

func (s *LocalStore) MaterializeVerified(ctx context.Context, descriptor Descriptor, cacheRoot string) (string, error) {
	source, err := LocalPath(descriptor.URI)
	if err != nil {
		return "", err
	}
	return MaterializeReader(ctx, descriptor, cacheRoot, func() (io.ReadCloser, error) {
		return os.Open(source)
	})
}

func (s *LocalStore) Verify(ctx context.Context, descriptor Descriptor) error {
	path, err := LocalPath(descriptor.URI)
	if err != nil {
		return err
	}
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	return VerifyReader(ctx, file, descriptor.SHA256, descriptor.SizeBytes)
}

// MaterializeReader streams bytes into the shared SHA cache and only publishes
// them after both the SHA-256 and size have matched the descriptor.
func MaterializeReader(ctx context.Context, descriptor Descriptor, cacheRoot string, open func() (io.ReadCloser, error)) (string, error) {
	cachePath := filepath.Join(cacheRoot, "sha256", descriptor.SHA256)
	if cachedSHA, cachedSize, digestErr := digestPath(ctx, cachePath); digestErr == nil {
		if cachedSHA == descriptor.SHA256 && cachedSize == descriptor.SizeBytes {
			return cachePath, nil
		}
		if err := os.Remove(cachePath); err != nil {
			return "", err
		}
	} else if !errors.Is(digestErr, os.ErrNotExist) {
		return "", digestErr
	}
	if err := os.MkdirAll(filepath.Dir(cachePath), 0o750); err != nil {
		return "", err
	}
	reader, err := open()
	if err != nil {
		return "", err
	}
	defer reader.Close()
	if err := atomicCopyReader(ctx, reader, cachePath, descriptor.SHA256, descriptor.SizeBytes); err != nil {
		return "", err
	}
	return cachePath, nil
}

// InspectFile computes the canonical identity used by every ArtifactStore adapter.
func InspectFile(ctx context.Context, path string) (string, int64, error) {
	return digestPath(ctx, path)
}

// VerifyReader consumes a stream and compares it with a canonical descriptor.
func VerifyReader(ctx context.Context, reader io.Reader, expectedSHA string, expectedSize int64) error {
	actualSHA, actualSize, err := digestReader(ctx, reader)
	if err != nil {
		return err
	}
	if actualSHA != expectedSHA || actualSize != expectedSize {
		return fmt.Errorf("%w: expected sha256=%s size=%d, actual sha256=%s size=%d", ErrIntegrity, expectedSHA, expectedSize, actualSHA, actualSize)
	}
	return nil
}

func LocalPath(uri string) (string, error) {
	parsed, err := url.Parse(uri)
	if err != nil {
		return "", err
	}
	if parsed.Scheme != "file" || parsed.Host != "" {
		return "", fmt.Errorf("local artifact URI must use file://")
	}
	return filepath.FromSlash(parsed.Path), nil
}

func IsIntegrityError(err error) bool {
	return errors.Is(err, ErrIntegrity)
}

func digestPath(ctx context.Context, path string) (string, int64, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer file.Close()
	return digestReader(ctx, file)
}

func digestReader(ctx context.Context, reader io.Reader) (string, int64, error) {
	hash := sha256.New()
	buffer := make([]byte, 1024*1024)
	var size int64
	for {
		if err := ctx.Err(); err != nil {
			return "", 0, err
		}
		read, err := reader.Read(buffer)
		if read > 0 {
			_, _ = hash.Write(buffer[:read])
			size += int64(read)
		}
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return "", 0, err
		}
	}
	return fmt.Sprintf("%x", hash.Sum(nil)), size, nil
}

func atomicCopy(ctx context.Context, source, destination, expectedSHA string, expectedSize int64) error {
	if len(expectedSHA) != 64 || strings.Trim(expectedSHA, "0123456789abcdef") != "" {
		return fmt.Errorf("invalid SHA-256 descriptor")
	}
	input, err := os.Open(source)
	if err != nil {
		return err
	}
	defer input.Close()
	return atomicCopyReader(ctx, input, destination, expectedSHA, expectedSize)
}

func atomicCopyReader(ctx context.Context, input io.Reader, destination, expectedSHA string, expectedSize int64) error {
	if len(expectedSHA) != 64 || strings.Trim(expectedSHA, "0123456789abcdef") != "" {
		return fmt.Errorf("invalid SHA-256 descriptor")
	}
	temporary, err := os.CreateTemp(filepath.Dir(destination), ".artifact-*")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	hash := sha256.New()
	written, copyErr := io.Copy(io.MultiWriter(temporary, hash), &contextReader{ctx: ctx, reader: input})
	if copyErr != nil {
		_ = temporary.Close()
		return copyErr
	}
	if err := temporary.Sync(); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	actualSHA := fmt.Sprintf("%x", hash.Sum(nil))
	if actualSHA != expectedSHA || written != expectedSize {
		return fmt.Errorf("%w: expected sha256=%s size=%d, actual sha256=%s size=%d", ErrIntegrity, expectedSHA, expectedSize, actualSHA, written)
	}
	return os.Rename(temporaryPath, destination)
}

type contextReader struct {
	ctx    context.Context
	reader io.Reader
}

func (reader *contextReader) Read(buffer []byte) (int, error) {
	if err := reader.ctx.Err(); err != nil {
		return 0, err
	}
	return reader.reader.Read(buffer)
}

package s3artifact_test

import (
	"bytes"
	"context"
	"io"
	"os"
	"path/filepath"
	"sync"
	"testing"

	awss3 "github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/s3artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

type memoryClient struct {
	mu      sync.Mutex
	objects map[string][]byte
}

func newMemoryClient() *memoryClient { return &memoryClient{objects: map[string][]byte{}} }

func (client *memoryClient) PutObject(_ context.Context, input *awss3.PutObjectInput, _ ...func(*awss3.Options)) (*awss3.PutObjectOutput, error) {
	payload, err := io.ReadAll(input.Body)
	if err != nil {
		return nil, err
	}
	client.mu.Lock()
	client.objects[*input.Bucket+"/"+*input.Key] = payload
	client.mu.Unlock()
	return &awss3.PutObjectOutput{}, nil
}

func (client *memoryClient) HeadObject(_ context.Context, input *awss3.HeadObjectInput, _ ...func(*awss3.Options)) (*awss3.HeadObjectOutput, error) {
	client.mu.Lock()
	payload := client.objects[*input.Bucket+"/"+*input.Key]
	client.mu.Unlock()
	size := int64(len(payload))
	return &awss3.HeadObjectOutput{ContentLength: &size}, nil
}

func (client *memoryClient) GetObject(_ context.Context, input *awss3.GetObjectInput, _ ...func(*awss3.Options)) (*awss3.GetObjectOutput, error) {
	client.mu.Lock()
	payload := append([]byte(nil), client.objects[*input.Bucket+"/"+*input.Key]...)
	client.mu.Unlock()
	return &awss3.GetObjectOutput{Body: io.NopCloser(bytes.NewReader(payload))}, nil
}

func TestStorePublishesAndMaterializesVerifiedBytes(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	source := filepath.Join(root, "feature.bin")
	if err := os.WriteFile(source, []byte("feature-vector"), 0o600); err != nil {
		t.Fatal(err)
	}
	client := newMemoryClient()
	store := s3artifact.New(client, "finevision-artifacts", "tests")

	descriptor, err := store.PutFile(context.Background(), source, artifact.PutRequest{
		ArtifactID: "feature-1", ArtifactType: "features", ContentType: "application/octet-stream", Producer: "go-test",
	})
	if err != nil {
		t.Fatal(err)
	}
	if descriptor.URI[:len("s3://finevision-artifacts/")] != "s3://finevision-artifacts/" {
		t.Fatalf("URI = %s", descriptor.URI)
	}
	path, err := store.MaterializeVerified(context.Background(), descriptor, filepath.Join(root, "cache"))
	if err != nil {
		t.Fatal(err)
	}
	if payload, err := os.ReadFile(path); err != nil || string(payload) != "feature-vector" {
		t.Fatalf("materialized payload = %q, err = %v", payload, err)
	}
}

func TestStoreDetectsCorruptionOnReadBack(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	source := filepath.Join(root, "model.bin")
	if err := os.WriteFile(source, []byte("model"), 0o600); err != nil {
		t.Fatal(err)
	}
	client := newMemoryClient()
	store := s3artifact.New(client, "finevision-artifacts", "")
	descriptor, err := store.PutFile(context.Background(), source, artifact.PutRequest{
		ArtifactID: "model-1", ArtifactType: "models", ContentType: "application/octet-stream", Producer: "go-test",
	})
	if err != nil {
		t.Fatal(err)
	}
	client.mu.Lock()
	for key := range client.objects {
		client.objects[key] = []byte("tampered")
	}
	client.mu.Unlock()

	_, err = store.MaterializeVerified(context.Background(), descriptor, filepath.Join(root, "cache"))
	if !artifact.IsIntegrityError(err) {
		t.Fatalf("expected integrity error, got %v", err)
	}
}

package artifact_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

func TestLocalStorePublishesAndMaterializesVerifiedBytes(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	source := filepath.Join(root, "model.bin")
	payload := []byte("finevision-model")
	if err := os.WriteFile(source, payload, 0o600); err != nil {
		t.Fatal(err)
	}
	store := artifact.NewLocalStore(filepath.Join(root, "objects"))

	descriptor, err := store.PutFile(context.Background(), source, artifact.PutRequest{
		ArtifactID: "artifact-1", ArtifactType: "model", ContentType: "application/octet-stream", Producer: "go-test",
	})
	if err != nil {
		t.Fatal(err)
	}
	wantSHA := fmt.Sprintf("%x", sha256.Sum256(payload))
	if descriptor.SHA256 != wantSHA || descriptor.SizeBytes != int64(len(payload)) {
		t.Fatalf("descriptor = %#v", descriptor)
	}

	path, err := store.MaterializeVerified(context.Background(), descriptor, filepath.Join(root, "cache"))
	if err != nil {
		t.Fatal(err)
	}
	actual, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(actual, payload) {
		t.Fatalf("materialized bytes = %q", actual)
	}
}

func TestLocalStoreFailsClosedForCorruptedObject(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	source := filepath.Join(root, "model.bin")
	if err := os.WriteFile(source, []byte("expected"), 0o600); err != nil {
		t.Fatal(err)
	}
	store := artifact.NewLocalStore(filepath.Join(root, "objects"))
	descriptor, err := store.PutFile(context.Background(), source, artifact.PutRequest{
		ArtifactID: "artifact-1", ArtifactType: "model", ContentType: "application/octet-stream", Producer: "go-test",
	})
	if err != nil {
		t.Fatal(err)
	}
	objectPath, err := artifact.LocalPath(descriptor.URI)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(objectPath, []byte("corrupted"), 0o600); err != nil {
		t.Fatal(err)
	}

	_, err = store.MaterializeVerified(context.Background(), descriptor, filepath.Join(root, "cache"))
	if !artifact.IsIntegrityError(err) {
		t.Fatalf("expected integrity error, got %v", err)
	}
}

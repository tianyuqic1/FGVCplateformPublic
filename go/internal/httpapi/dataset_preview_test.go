package httpapi

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type previewRepository struct {
	uploadRepository
	snapshot dataset.Snapshot
}

func (r *previewRepository) PreviewSnapshot(_ context.Context, id string) (dataset.Snapshot, error) {
	if id != "version" {
		return dataset.Snapshot{}, dataset.ErrNotFound
	}
	return r.snapshot, nil
}
func TestDatasetPreviewRoutes(t *testing.T) {
	image, _ := base64.StdEncoding.DecodeString("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
	for _, tc := range []struct {
		name, path, sha string
		data            []byte
		status          int
	}{
		{"valid", "class/a.png", "", image, 200},
		{"traversal", "../a.png", "", image, 422},
		{"non image", "class/a.png", "", []byte("<html>not image</html>"), 422},
		{"checksum mismatch", "class/a.png", strings.Repeat("0", 64), image, 503},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var buffer bytes.Buffer
			writer := zip.NewWriter(&buffer)
			entry, _ := writer.Create(tc.path)
			entry.Write(tc.data)
			writer.Close()
			path := filepath.Join(t.TempDir(), "archive.zip")
			if err := os.WriteFile(path, buffer.Bytes(), 0600); err != nil {
				t.Fatal(err)
			}
			store := artifact.NewLocalStore(t.TempDir())
			descriptor, err := store.PutFile(context.Background(), path, artifact.PutRequest{ArtifactID: "test", ArtifactType: "dataset_archive"})
			if err != nil {
				t.Fatal(err)
			}
			if tc.sha != "" {
				descriptor.SHA256 = tc.sha
			}
			repo := &previewRepository{snapshot: dataset.Snapshot{VersionID: "version", DatasetKey: "dataset", Archive: descriptor, Manifest: map[string]any{"samples": []map[string]string{
				{"sample_id": "sample", "path": tc.path, "label": "class", "split": "train"}, {"sample_id": "second", "path": tc.path, "label": "other", "split": "val"},
			}}}}
			service := &dataset.Service{Store: store, Repository: repo}
			router := NewRouter(Dependencies{DatasetImport: service})
			response := httptest.NewRecorder()
			router.ServeHTTP(response, httptest.NewRequest("GET", "/api/dataset-versions/version/sample-previews?limit=2", nil))
			if response.Code != 200 {
				t.Fatal(response.Body.String())
			}
			var payload struct{ Samples []map[string]string }
			if err = json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
				t.Fatal(err)
			}
			if len(payload.Samples) != 2 || payload.Samples[0]["path"] != "" {
				t.Fatal(payload)
			}
			if tc.status == 200 {
				if payload.Samples[0]["availability"] != "available" || payload.Samples[0]["image_url"] == "" {
					t.Fatal("valid preview must advertise a working image", payload)
				}
				response = httptest.NewRecorder()
				router.ServeHTTP(response, httptest.NewRequest("GET", payload.Samples[0]["image_url"], nil))
				if response.Code != 200 || !bytes.Equal(response.Body.Bytes(), image) || response.Header().Get("Content-Type") != "image/png" || response.Header().Get("X-Content-Type-Options") != "nosniff" {
					t.Fatal("image bytes or headers changed")
				}
			} else if payload.Samples[0]["availability"] != "unavailable" || payload.Samples[0]["image_url"] != "" {
				t.Fatal("broken preview must not advertise an image URL", payload)
			}
			for _, route := range []string{"/api/dataset-versions/missing/sample-previews", "/api/dataset-versions/version/samples/missing/image", "/api/dataset-versions/missing/samples/sample/image"} {
				response = httptest.NewRecorder()
				router.ServeHTTP(response, httptest.NewRequest("GET", route, nil))
				if response.Code != 404 {
					t.Fatalf("%s: %d", route, response.Code)
				}
			}
			for _, limit := range []string{"0", "25", "invalid", "-1"} {
				response = httptest.NewRecorder()
				router.ServeHTTP(response, httptest.NewRequest("GET", "/api/dataset-versions/version/sample-previews?limit="+limit, nil))
				if response.Code != 422 {
					t.Fatalf("limit %s: %d", limit, response.Code)
				}
			}
		})
	}
}

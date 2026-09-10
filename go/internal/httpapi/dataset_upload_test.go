package httpapi

import (
	"archive/zip"
	"bytes"
	"context"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
)

type uploadScanner struct{ t *testing.T }

func (s uploadScanner) Scan(ctx context.Context, a artifact.Descriptor, key, version string) (map[string]any, error) {
	p, err := artifact.LocalPath(a.URI)
	if err != nil {
		return nil, err
	}
	archive, err := zip.OpenReader(p)
	if err != nil {
		return nil, err
	}
	defer archive.Close()
	if len(archive.File) != 1 || archive.File[0].Name != "class/image.png" {
		s.t.Fatalf("archive entries: %#v", archive.File)
	}
	return map[string]any{"classes": []string{"class"}, "readiness": map[string]any{"ready": true, "sample_count": float64(1), "class_count": float64(1)}}, nil
}

type uploadRepository struct {
	conflict bool
	saved    bool
}

func (r *uploadRepository) Save(ctx context.Context, key, versionKey, versionID string, a, m artifact.Descriptor, data map[string]any) error {
	if r.conflict {
		return dataset.ErrConflict
	}
	r.saved = true
	return nil
}
func TestUploadImagefolderPublicRoute(t *testing.T) {
	for _, tc := range []struct {
		name, path string
		conflict   bool
		status     int
	}{
		{"browser folder", "selected/class/image.png", false, 202},
		{"duplicate version", "selected/class/image.png", true, 409},
		{"queue full", "selected/class/image.png", false, 429},
		{"traversal", "selected/../image.png", false, 422},
		{"flat file", "image.png", false, 422},
	} {
		t.Run(tc.name, func(t *testing.T) {
			repository := &uploadRepository{conflict: tc.conflict}
			service := &dataset.Service{Store: artifact.NewLocalStore(t.TempDir()), Scanner: uploadScanner{t}, Repository: repository}
			jobs := &testImportJobs{}
			if tc.conflict {
				jobs.enqueueErr = dataset.ErrConflict
			}
			if tc.status == 429 {
				jobs.enqueueErr = dataset.ErrQueueFull
			}
			queue := &dataset.ImportQueue{Service: service, Repository: jobs}
			handler := NewRouter(Dependencies{DatasetImport: service, DatasetQueue: queue})
			var body bytes.Buffer
			form := multipart.NewWriter(&body)
			form.WriteField("dataset_id", "example")
			form.WriteField("dataset_version_id", "example-v1")
			file, _ := form.CreateFormFile("files", tc.path)
			file.Write([]byte("image compute validates bytes"))
			form.Close()
			req := httptest.NewRequest(http.MethodPost, "/api/datasets/upload-imagefolder", &body)
			req.Header.Set("Content-Type", form.FormDataContentType())
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, req)
			result, _ := io.ReadAll(response.Result().Body)
			if response.Code != tc.status {
				t.Fatalf("status %d: %s", response.Code, result)
			}
			if repository.saved {
				t.Fatal("unexpected registration")
			}
			if tc.status == 202 {
				if err := queue.ProcessNext(context.Background()); err != nil {
					t.Fatal(err)
				}
				if !repository.saved {
					t.Fatal("worker did not register dataset")
				}
			}
		})
	}
}

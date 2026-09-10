package httpapi

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"github.com/google/uuid"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
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

func (r *uploadRepository) FindPublication(context.Context, string, string) (map[string]any, error) {
	return nil, nil
}
func (r *uploadRepository) Base(context.Context, string, string) (dataset.Snapshot, error) {
	return dataset.Snapshot{}, dataset.ErrNotFound
}
func (r *uploadRepository) Candidates(context.Context, string) ([]dataset.Feedback, error) {
	return nil, nil
}
func (r *uploadRepository) Save(ctx context.Context, p dataset.Publication) (map[string]any, error) {
	if r.conflict {
		return nil, dataset.ErrConflict
	}
	r.saved = true
	return dataset.Result(p), nil
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
			form.WriteField("name", "鸟类识别")
			form.WriteField("request_id", uuid.NewString())
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
				var accepted struct{ Job dataset.ImportJob }
				if err := json.Unmarshal(result, &accepted); err != nil || accepted.Job.Name != "鸟类识别" {
					t.Fatalf("invalid queued response: %s", result)
				}
				if err := queue.ProcessNext(context.Background()); err != nil {
					t.Fatal(err)
				}
				if !repository.saved {
					t.Fatal("worker did not register dataset")
				}
				published := jobs.job.Result["dataset"].(map[string]any)
				if published["name"] != "鸟类识别" || published["version_number"] != 1 {
					t.Fatalf("name/version: %#v", published)
				}
				for _, key := range []string{"dataset_id", "dataset_version_id"} {
					if _, err := uuid.Parse(published[key].(string)); err != nil {
						t.Fatal(err)
					}
				}
				if strings.Contains(published["dataset_id"].(string), "鸟") {
					t.Fatal("name used as identity")
				}
			}
		})
	}
}

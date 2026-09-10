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
		{"browser folder", "selected/class/image.png", false, 201},
		{"duplicate version", "selected/class/image.png", true, 409},
		{"traversal", "selected/../image.png", false, 422},
		{"flat file", "image.png", false, 422},
	} {
		t.Run(tc.name, func(t *testing.T) {
			repository := &uploadRepository{conflict: tc.conflict}
			handler := NewRouter(Dependencies{DatasetImport: &dataset.Service{Store: artifact.NewLocalStore(t.TempDir()), Scanner: uploadScanner{t}, Repository: repository}})
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
			if tc.status == 201 {
				var payload struct{ Dataset map[string]any }
				if err := json.Unmarshal(result, &payload); err != nil {
					t.Fatal(err)
				}
				if payload.Dataset["name"] != "鸟类识别" || payload.Dataset["version_number"] != float64(1) {
					t.Fatalf("name/version: %s", result)
				}
				for _, key := range []string{"dataset_id", "dataset_version_id"} {
					if _, err := uuid.Parse(payload.Dataset[key].(string)); err != nil {
						t.Fatal(err)
					}
				}
				if strings.Contains(payload.Dataset["dataset_id"].(string), "鸟") {
					t.Fatal("name used as identity")
				}
			}
			if repository.saved != (tc.status == 201) {
				t.Fatal("unexpected registration")
			}
		})
	}
}

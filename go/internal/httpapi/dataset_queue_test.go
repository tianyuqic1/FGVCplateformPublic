package httpapi

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"mime/multipart"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
)

type testImportJobs struct {
	job        dataset.ImportJob
	enqueueErr error
}

func (r *testImportJobs) Enqueue(_ context.Context, j dataset.ImportJob) error {
	if r.enqueueErr != nil {
		return r.enqueueErr
	}
	r.job = j
	return nil
}
func (r *testImportJobs) List(context.Context) ([]dataset.ImportJob, error) {
	return []dataset.ImportJob{r.job}, nil
}
func (r *testImportJobs) Claim(context.Context) (dataset.ImportJob, error) {
	r.job.Status = "running"
	return r.job, nil
}
func (r *testImportJobs) Renew(context.Context, dataset.ImportJob) error { return nil }
func (r *testImportJobs) Finish(_ context.Context, j dataset.ImportJob, result map[string]any, message string) error {
	r.job.Result = result
	r.job.Error = message
	r.job.Status = "succeeded"
	if message != "" {
		r.job.Status = "failed"
	}
	return nil
}

func TestDatasetUploadReturnsBeforeScanAndSurvivesRequestCancellation(t *testing.T) {
	repository := &uploadRepository{}
	service := &dataset.Service{Store: artifact.NewLocalStore(t.TempDir()), Scanner: uploadScanner{t}, Repository: repository}
	jobs := &testImportJobs{}
	queue := &dataset.ImportQueue{Repository: jobs, Service: service}
	handler := NewRouter(Dependencies{DatasetImport: service, DatasetQueue: queue})
	var body bytes.Buffer
	form := multipart.NewWriter(&body)
	form.WriteField("dataset_id", "example")
	form.WriteField("dataset_version_id", "example-v1")
	part, _ := form.CreateFormFile("files", "selected/class/image.png")
	part.Write([]byte("compute checks bytes"))
	form.Close()
	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest("POST", "/api/datasets/upload-imagefolder", &body).WithContext(ctx)
	req.Header.Set("Content-Type", form.FormDataContentType())
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, req)
	cancel()
	if response.Code != 202 {
		t.Fatalf("%d %s", response.Code, response.Body.String())
	}
	if repository.saved {
		t.Fatal("upload waited for registration")
	}
	var payload struct {
		Job dataset.ImportJob `json:"job"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil || payload.Job.Status != "queued" || payload.Job.ID == "" {
		t.Fatalf("invalid accepted response: %s", response.Body.String())
	}
	if err := queue.ProcessNext(context.Background()); err != nil {
		t.Fatal(err)
	}
	if !repository.saved || jobs.job.Status != "succeeded" {
		t.Fatal("background import did not finish")
	}
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest("GET", "/api/dataset-imports", nil))
	if response.Code != 200 || !bytes.Contains(response.Body.Bytes(), []byte("succeeded")) {
		t.Fatal(response.Body.String())
	}
}

func TestBackgroundImportFailureIsPersisted(t *testing.T) {
	store := artifact.NewLocalStore(t.TempDir())
	repository := &uploadRepository{conflict: true}
	service := &dataset.Service{Store: store, Scanner: uploadScanner{t}, Repository: repository}
	jobs := &testImportJobs{}
	queue := &dataset.ImportQueue{Service: service, Repository: jobs}
	var data bytes.Buffer
	writer := zip.NewWriter(&data)
	part, err := writer.Create("class/image.png")
	if err != nil {
		t.Fatal(err)
	}
	part.Write([]byte("compute checks bytes"))
	writer.Close()
	path := filepath.Join(t.TempDir(), "upload.zip")
	if err := os.WriteFile(path, data.Bytes(), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := queue.Enqueue(context.Background(), "example", "v1", path); err != nil {
		t.Fatal(err)
	}
	if err := queue.ProcessNext(context.Background()); err != nil {
		t.Fatal(err)
	}
	if jobs.job.Status != "failed" || jobs.job.Error == "" || repository.saved {
		t.Fatalf("failed import not reported: %+v", jobs.job)
	}
}

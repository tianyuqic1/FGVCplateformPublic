package annotation_test

import (
	"archive/zip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	postgres "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/postgres"
	. "github.com/tianyuqic1/FGVCplateformPublic/go/internal/annotation"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
)

type publicationScanner struct{ fail bool }

func fixtureProject() Project {
	return Project{Name: "release test", Classes: []Class{{ID: "a", Name: "Alpha"}, {ID: "b", Name: "Beta"}}, Method: "D", Domain: "general"}
}

func (s *publicationScanner) Scan(ctx context.Context, a artifact.Descriptor, key, version string) (map[string]any, error) {
	if s.fail {
		return nil, errors.New("simulated scanner outage")
	}
	p, e := artifact.LocalPath(a.URI)
	if e != nil {
		return nil, e
	}
	z, e := zip.OpenReader(p)
	if e != nil {
		return nil, e
	}
	defer z.Close()
	samples := []any{}
	classes := map[string]bool{}
	counts := map[string]map[string]int{"train": {}, "val": {}, "test": {}}
	for _, f := range z.File {
		parts := strings.Split(f.Name, "/")
		samples = append(samples, map[string]any{"sample_id": f.Name, "path": f.Name, "label": parts[1], "split": parts[0]})
		classes[parts[1]] = true
		counts[parts[0]][parts[1]]++
	}
	names := []string{}
	for name := range classes {
		names = append(names, name)
	}
	value := map[string]any{"samples": samples, "classes": names, "split_counts": counts, "readiness": map[string]any{"ready": true, "sample_count": len(samples), "class_count": len(classes)}}
	raw, _ := json.Marshal(value)
	_ = json.Unmarshal(raw, &value)
	return value, nil
}
func TestReleaseRequestValidation(t *testing.T) {
	in := ReleaseRequest{ID: uuid.NewString(), Source: "annotation", ProjectID: uuid.NewString(), Name: "test", IDs: []string{uuid.NewString()}, Split: dataset.SplitPlan{Train: 80, Val: 10, Test: 10}}
	if e := in.Validate(); e != nil {
		t.Fatal(e)
	}
	in.Mapping = map[string]string{"a": "Beta"}
	if in.Validate() == nil {
		t.Fatal("manual relabeling accepted")
	}
	in.Mapping = nil
	in.Source = "feedback"
	if in.Validate() == nil {
		t.Fatal("feedback published as new dataset")
	}
	in.DatasetID = "d"
	in.BaseID = "v"
	in.TrainOnly = true
	if e := in.Validate(); e != nil {
		t.Fatal(e)
	}
	in.TrainOnly = false
	if in.Validate() == nil {
		t.Fatal("feedback into evaluation")
	}
}
func TestPublicationDatabaseLifecycle(t *testing.T) {
	url := os.Getenv("ANNOTATION_TEST_DATABASE_URL")
	if url == "" {
		t.Skip("requires disposable annotation_test_ database")
	}
	cfg, e := pgxpool.ParseConfig(url)
	if e != nil {
		t.Fatal(e)
	}
	if !strings.HasPrefix(cfg.ConnConfig.Database, "annotation_test_") {
		t.Fatal("refusing non-test database")
	}
	ctx := context.Background()
	pool, e := pgxpool.NewWithConfig(ctx, cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer pool.Close()
	repo := &Repository{Pool: pool}
	store := artifact.NewLocalStore(t.TempDir())
	scanner := &publicationScanner{}
	service := &dataset.Service{Store: store, Scanner: scanner, Repository: postgres.DatasetRepository{Pool: pool}}
	p := &Publisher{Repo: repo, Datasets: service}
	fixture := fixtureProject()
	fixture.Classes = append(fixture.Classes, Class{ID: "c", Name: "Gamma"})
	project, e := repo.Create(ctx, fixture)
	if e != nil {
		t.Fatal(e)
	}
	add := func(label, body string) Task {
		file := filepath.Join(t.TempDir(), "sample.png")
		if e := os.WriteFile(file, []byte(body), 0600); e != nil {
			t.Fatal(e)
		}
		a, e := store.PutFile(ctx, file, artifact.PutRequest{ArtifactID: uuid.NewString(), ArtifactType: "annotation_image"})
		if e != nil {
			t.Fatal(e)
		}
		task, e := repo.Add(ctx, project.ID, "sample.png", a)
		if e != nil {
			t.Fatal(e)
		}
		if e = repo.Confirm(ctx, task.ID, label, "test human"); e != nil {
			t.Fatal(e)
		}
		return task
	}
	ids := []string{}
	for i := 0; i < 20; i++ {
		label := "a"
		if i >= 10 {
			label = "b"
		}
		ids = append(ids, add(label, fmt.Sprintf("test pixel %s %d", project.ID, i)).ID)
	}
	in := ReleaseRequest{ID: uuid.NewString(), Source: "annotation", ProjectID: project.ID, Name: "publication isolated test", IDs: ids, Split: dataset.SplitPlan{Train: 80, Val: 10, Test: 10, Seed: 42}}
	job, e := p.Preview(ctx, in)
	if e != nil {
		t.Fatal(e)
	}
	if job.Status != "preview" {
		t.Fatal(job.Status)
	}
	if _, e = p.Preview(ctx, in); e != nil {
		t.Fatal("idempotent preview", e)
	}
	changed := in
	changed.Name = "different"
	if _, e = p.Preview(ctx, changed); !errors.Is(e, ErrConflict) {
		t.Fatal("changed request accepted", e)
	}
	competing := in
	competing.ID = uuid.NewString()
	other, e := p.Preview(ctx, competing)
	if e != nil {
		t.Fatal(e)
	}
	var wg sync.WaitGroup
	errs := make(chan error, 2)
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); errs <- p.Transition(ctx, in.ID, "publish") }()
	}
	wg.Wait()
	close(errs)
	for e := range errs {
		if e != nil {
			t.Fatal(e)
		}
	}
	if e = p.Transition(ctx, other.ID, "publish"); !errors.Is(e, ErrConflict) {
		t.Fatal("duplicate source reserved", e)
	}
	if e = p.Transition(ctx, other.ID, "cancel"); e != nil {
		t.Fatal(e)
	}
	scanner.fail = true
	if e = p.ProcessNext(ctx); e != nil {
		t.Fatal(e)
	}
	job, _ = p.Get(ctx, in.ID)
	if job.Status != "failed" {
		t.Fatal("expected durable failure", job.Status)
	}
	scanner.fail = false
	if e = p.Transition(ctx, in.ID, "retry"); e != nil {
		t.Fatal(e)
	}
	if e = p.ProcessNext(ctx); e != nil {
		t.Fatal(e)
	}
	job, _ = p.Get(ctx, in.ID)
	if job.Status != "published" {
		t.Fatal(job.Status, job.Error)
	}
	var result map[string]any
	if e = json.Unmarshal(job.Result, &result); e != nil {
		t.Fatal(e)
	}
	version := result["version"].(map[string]any)
	datasetID := version["dataset_id"].(string)
	baseID := version["dataset_version_id"].(string)
	baseline, e := service.Repository.Base(ctx, datasetID, baseID)
	if e != nil {
		t.Fatal(e)
	}
	original, _ := json.Marshal(baseline.Manifest)
	// Simulate crash after registration committed but before the ACK reached the batch.
	if _, e = pool.Exec(ctx, `UPDATE annotation_publications SET status='registering',result='{}' WHERE id=$1`, job.ID); e != nil {
		t.Fatal(e)
	}
	if e = p.ProcessNext(ctx); e != nil {
		t.Fatal(e)
	}
	var count int
	if e = pool.QueryRow(ctx, `SELECT count(*) FROM dataset_versions WHERE import_request_id=$1`, job.ID).Scan(&count); e != nil || count != 1 {
		t.Fatal("duplicate registration", count, e)
	}
	// Two distinct batches explicitly branch from the same old version. They get
	// unique monotonically increasing numbers without silently merging each other.
	for i := 0; i < 2; i++ {
		label := "a"
		if i == 1 {
			label = "c"
		}
		task := add(label, fmt.Sprintf("new pixels %s %d", project.ID, i))
		request := ReleaseRequest{ID: uuid.NewString(), Source: "annotation", ProjectID: project.ID, DatasetID: datasetID, BaseID: baseID, IDs: []string{task.ID}, TrainOnly: true, Split: in.Split}
		j, e := p.Preview(ctx, request)
		if e != nil {
			t.Fatal(e)
		}
		if e = p.Transition(ctx, j.ID, "publish"); e != nil {
			t.Fatal(e)
		}
		if e = p.ProcessNext(ctx); e != nil {
			t.Fatal(e)
		}
		j, _ = p.Get(ctx, j.ID)
		if j.Status != "published" {
			t.Fatal(j.Error)
		}
		var out map[string]any
		json.Unmarshal(j.Result, &out)
		v := out["version"].(map[string]any)
		if v["version_number"] != float64(i+2) || v["parent_version_id"] != baseID {
			t.Fatal(v)
		}
		if i == 1 {
			if v["class_count"] != float64(3) {
				t.Fatal("new category not registered", v)
			}
			if v["change_summary"].(map[string]any)["new_class_additions"].(map[string]any)["Gamma"] != float64(1) {
				t.Fatal("new class preview missing", v)
			}
		} else if v["class_count"] != float64(2) {
			t.Fatal("same-name category duplicated", v)
		}
	}
	after, e := service.Repository.Base(ctx, datasetID, baseID)
	if e != nil {
		t.Fatal(e)
	}
	afterRaw, _ := json.Marshal(after.Manifest)
	if string(original) != string(afterRaw) {
		t.Fatal("registered labels/splits mutated")
	}
	// Build a metadata-only completed model; no training or external worker runs.
	lifecycle := training.NewService(postgres.NewTrainingRepository(pool), time.Now, time.Minute)
	run, e := lifecycle.Create(ctx, training.CreateCommand{DatasetID: datasetID, DatasetVersionID: baseID, BackboneID: "dinov3_vits16_lvd1689m", Payload: map[string]any{}, MaxAttempts: 2})
	if e != nil {
		t.Fatal(e)
	}
	claim, e := lifecycle.Claim(ctx, training.ClaimCommand{JobID: run.JobID, DispatchGeneration: 1, WorkerID: "publication-test"})
	if e != nil {
		t.Fatal(e)
	}
	modelFile := filepath.Join(t.TempDir(), "weight")
	os.WriteFile(modelFile, []byte("metadata fixture"), 0600)
	model, e := store.PutFile(ctx, modelFile, artifact.PutRequest{ArtifactID: uuid.NewString(), ArtifactType: "model", ContentType: "application/octet-stream", Producer: "release-test", DatasetVersionID: baseID, TrainingRunID: run.TrainingRunID, AttemptID: claim.AttemptID})
	if e != nil {
		t.Fatal(e)
	}
	completed, e := lifecycle.Complete(ctx, training.CompleteCommand{JobID: run.JobID, AttemptID: claim.AttemptID, ExecutionEpoch: claim.ExecutionEpoch, CompletionKey: "release-fixture", ResultDigest: model.SHA256, Artifacts: []artifact.Descriptor{model}, Metrics: map[string]any{"accuracy": 1.0}})
	if e != nil {
		t.Fatal(e)
	}
	service.UploadRoot = t.TempDir()
	upload := filepath.Join(service.UploadRoot, "review.png")
	os.WriteFile(upload, []byte("reviewed pixels"), 0600)
	event, review, feedback := uuid.NewString(), uuid.NewString(), uuid.NewString()
	_, e = pool.Exec(ctx, `INSERT INTO inference_events(id,event_key,dataset_id,dataset_version_id,model_version_id,model_status,input_type,input_ref,decision,request_payload,result_payload,created_at) VALUES($1::uuid,$1::text,$2,$3,$4,'candidate','upload',$5,'abstain','{}','{}',now())`, event, datasetID, baseID, completed.ModelVersionID, upload)
	if e != nil {
		t.Fatal(e)
	}
	_, e = pool.Exec(ctx, `INSERT INTO review_items(id,review_key,inference_event_id,dataset_id,dataset_version_id,model_version_id,input_ref,status,risk_type,reason,created_at,updated_at) VALUES($1::uuid,$1::text,$2,$3,$4,$5,$6,'feedbacked','low_confidence','test',now(),now())`, review, event, datasetID, baseID, completed.ModelVersionID, upload)
	if e != nil {
		t.Fatal(e)
	}
	_, e = pool.Exec(ctx, `INSERT INTO feedback_items(id,feedback_key,review_item_id,inference_event_id,dataset_id,dataset_version_id,model_version_id,final_label,final_outcome,destination,created_at) VALUES($1::uuid,$1::text,$2,$3,$4,$5,$6,'Alpha','confirmed_label','training_candidate',now())`, feedback, review, event, datasetID, baseID, completed.ModelVersionID)
	if e != nil {
		t.Fatal(e)
	}
	feedbackReq := ReleaseRequest{ID: uuid.NewString(), Source: "feedback", DatasetID: datasetID, BaseID: baseID, IDs: []string{feedback}, TrainOnly: true, Split: in.Split}
	fj, e := p.Preview(ctx, feedbackReq)
	if e != nil {
		t.Fatal("feedback preview", e)
	}
	if e = p.Transition(ctx, fj.ID, "publish"); e != nil {
		t.Fatal(e)
	}
	if e = p.ProcessNext(ctx); e != nil {
		t.Fatal(e)
	}
	fj, _ = p.Get(ctx, fj.ID)
	if fj.Status != "published" {
		t.Fatal("feedback publication", fj.Error)
	}
	var fResult map[string]any
	json.Unmarshal(fj.Result, &fResult)
	fv := fResult["version"].(map[string]any)
	if fv["version_number"] != float64(4) || fv["parent_version_id"] != baseID {
		t.Fatal(fv)
	}
	candidates, e := service.Repository.Candidates(ctx, datasetID)
	if e != nil || len(candidates) != 0 {
		t.Fatal("feedback not consumed", e, candidates)
	}
	if _, e = pool.Exec(ctx, `UPDATE annotation_publications SET status='registering' WHERE id=$1`, fj.ID); e != nil {
		t.Fatal(e)
	}
	if e = p.ProcessNext(ctx); e != nil {
		t.Fatal(e)
	}
	fj, _ = p.Get(ctx, fj.ID)
	if fj.Status != "published" {
		t.Fatal("feedback crash recovery", fj.Error)
	}
	// Even when cancelled after a failure, a source is reusable only by explicit action.
	task := add("a", "cancel-release-"+project.ID)
	cancelReq := in
	cancelReq.ID = uuid.NewString()
	cancelReq.IDs = []string{task.ID}
	j, e := p.Preview(ctx, cancelReq)
	if e != nil {
		t.Fatal(e)
	}
	if e = p.Transition(ctx, j.ID, "publish"); e != nil {
		t.Fatal(e)
	}
	scanner.fail = true
	p.ProcessNext(ctx)
	if e = p.Transition(ctx, j.ID, "cancel"); e != nil {
		t.Fatal(e)
	}
	var reserved int
	pool.QueryRow(ctx, `SELECT count(*) FROM annotation_publication_members WHERE publication_id=$1`, j.ID).Scan(&reserved)
	if reserved != 0 {
		t.Fatal("reservation not released")
	}
}

package postgres_test

import (
	"archive/zip"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	postgresadapter "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/postgres"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
)

type versionScanner struct{}

func (versionScanner) Scan(ctx context.Context, a artifact.Descriptor, key, version string) (map[string]any, error) {
	p, err := artifact.LocalPath(a.URI)
	if err != nil {
		return nil, err
	}
	archive, err := zip.OpenReader(p)
	if err != nil {
		return nil, err
	}
	defer archive.Close()
	samples := []any{}
	splits := map[string]any{"train": map[string]int{}, "val": map[string]int{}, "test": map[string]int{}}
	for _, f := range archive.File {
		parts := strings.Split(f.Name, "/")
		split, label := parts[0], parts[1]
		samples = append(samples, map[string]any{"sample_id": f.Name, "path": f.Name, "split": split, "label": label})
		splits[split].(map[string]int)[label]++
	}
	// Match gRPC's JSON number/map representation.
	result := map[string]any{"samples": samples, "classes": []string{"bird"}, "split_counts": splits, "readiness": map[string]any{"ready": true, "sample_count": len(samples), "class_count": 1}}
	data, _ := json.Marshal(result)
	json.Unmarshal(data, &result)
	return result, nil
}
func versionArchive(t *testing.T, files map[string]string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "images.zip")
	file, err := os.Create(p)
	if err != nil {
		t.Fatal(err)
	}
	w := zip.NewWriter(file)
	for name, data := range files {
		out, err := w.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		io.WriteString(out, data)
	}
	if err = w.Close(); err != nil {
		t.Fatal(err)
	}
	file.Close()
	return p
}
func TestDatasetVersionsAtomicLineageAndWeights(t *testing.T) {
	url := os.Getenv("FINEVISION_TEST_GO_DATABASE_URL")
	if url == "" {
		t.Skip("requires migrated PostgreSQL")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	repository := postgresadapter.DatasetRepository{Pool: pool}
	store := artifact.NewLocalStore(t.TempDir())
	s := dataset.Service{Repository: repository, Store: store, Scanner: versionScanner{}, UploadRoot: t.TempDir()}
	archive := versionArchive(t, map[string]string{"train/bird/a.png": "training", "val/bird/b.png": "validation", "test/bird/c.png": "testing"})
	requestID := uuid.NewString()
	initial, err := s.Import(ctx, "中文数据集名称", requestID, archive)
	if err != nil {
		t.Fatal(err)
	}
	summary := initial["dataset"].(map[string]any)
	id := summary["dataset_id"].(string)
	v1 := summary["dataset_version_id"].(string)
	if id == v1 || summary["name"] != "中文数据集名称" {
		t.Fatal(summary)
	}
	again, err := s.Import(ctx, "中文数据集名称", requestID, archive)
	if err != nil || again["version"].(map[string]any)["dataset_version_id"] != v1 {
		t.Fatalf("idempotency: %v %v", again, err)
	}
	if _, err = s.Import(ctx, "不同请求", requestID, archive); !errors.Is(err, dataset.ErrConflict) {
		t.Fatalf("request reuse: %v", err)
	}

	// A successfully completed v1 model must remain attached to v1 after expansions.
	lifecycle := training.NewService(postgresadapter.NewTrainingRepository(pool), time.Now, time.Minute)
	run, err := lifecycle.Create(ctx, training.CreateCommand{DatasetID: id, DatasetVersionID: v1, BackboneID: "dinov3_vits16_lvd1689m", Payload: map[string]any{}, MaxAttempts: 2})
	if err != nil {
		t.Fatal(err)
	}
	claim, err := lifecycle.Claim(ctx, training.ClaimCommand{JobID: run.JobID, DispatchGeneration: 1, WorkerID: "version-test"})
	if err != nil {
		t.Fatal(err)
	}
	modelFile := filepath.Join(t.TempDir(), "weight")
	os.WriteFile(modelFile, []byte("test weights"), 0600)
	model, err := store.PutFile(ctx, modelFile, artifact.PutRequest{ArtifactID: uuid.NewString(), ArtifactType: "model", ContentType: "application/octet-stream", Producer: "version-test", DatasetVersionID: v1, TrainingRunID: run.TrainingRunID, AttemptID: claim.AttemptID})
	if err != nil {
		t.Fatal(err)
	}
	completed, err := lifecycle.Complete(ctx, training.CompleteCommand{JobID: run.JobID, AttemptID: claim.AttemptID, ExecutionEpoch: claim.ExecutionEpoch, CompletionKey: "version-test", ResultDigest: model.SHA256, Artifacts: []artifact.Descriptor{model}, Metrics: map[string]any{"accuracy": 1.0}})
	if err != nil {
		t.Fatal(err)
	}

	addition := versionArchive(t, map[string]string{"train/bird/new.png": "new image"})
	req2 := uuid.NewString()
	v2result, err := s.Expand(ctx, id, v1, req2, addition, nil)
	if err != nil {
		t.Fatal(err)
	}
	v2 := v2result["version"].(map[string]any)["dataset_version_id"].(string)
	if _, err = s.Expand(ctx, id, v1, uuid.NewString(), addition, nil); !errors.Is(err, dataset.ErrConflict) {
		t.Fatalf("stale base: %v", err)
	}
	reads := postgresadapter.NewReadModels(pool)
	detail, err := reads.GetDataset(ctx, id)
	if err != nil {
		t.Fatal(err)
	}
	versions := detail["versions"].([]any)
	latest, old := versions[0].(map[string]any), versions[1].(map[string]any)
	if latest["has_weights"] != false || latest["training_status"] != "untrained" || old["has_weights"] != true || old["model_count"] != float64(1) || latest["parent_version_id"] != v1 {
		t.Fatalf("weights/lineage: %#v", detail)
	}
	if old["sample_count"] != float64(3) || latest["sample_count"] != float64(4) {
		t.Fatal("snapshot mutated", versions)
	}
	replay, err := s.Expand(ctx, id, v1, req2, addition, nil)
	if err != nil || replay["version"].(map[string]any)["dataset_version_id"] != v2 {
		t.Fatal("retry after advancement", err)
	}

	// Reviewed predictions made by the old v1 model flow into the latest v2 base.
	upload := filepath.Join(s.UploadRoot, "review.png")
	os.WriteFile(upload, []byte("reviewed image"), 0600)
	event, review, feedback := uuid.NewString(), uuid.NewString(), uuid.NewString()
	_, err = pool.Exec(ctx, `INSERT INTO inference_events(id,event_key,dataset_id,dataset_version_id,model_version_id,model_status,input_type,input_ref,decision,request_payload,result_payload,created_at) VALUES($1::uuid,$1::text,$2,$3,$4,'candidate','upload',$5,'abstain','{}','{}',now())`, event, id, v1, completed.ModelVersionID, upload)
	if err != nil {
		t.Fatal(err)
	}
	_, err = pool.Exec(ctx, `INSERT INTO review_items(id,review_key,inference_event_id,dataset_id,dataset_version_id,model_version_id,input_ref,status,risk_type,reason,created_at,updated_at) VALUES($1::uuid,$1::text,$2,$3,$4,$5,$6,'feedbacked','low_confidence','test',now(),now())`, review, event, id, v1, completed.ModelVersionID, upload)
	if err != nil {
		t.Fatal(err)
	}
	_, err = pool.Exec(ctx, `INSERT INTO feedback_items(id,feedback_key,review_item_id,inference_event_id,dataset_id,dataset_version_id,model_version_id,final_label,final_outcome,destination,created_at) VALUES($1::uuid,$1::text,$2,$3,$4,$5,$6,'bird','confirmed_label','training_candidate',now())`, feedback, review, event, id, v1, completed.ModelVersionID)
	if err != nil {
		t.Fatal(err)
	}
	candidates, err := repository.Candidates(ctx, id)
	if err != nil || len(candidates) != 1 {
		t.Fatalf("candidates: %v %v", candidates, err)
	}
	v3result, err := s.Expand(ctx, id, v2, uuid.NewString(), "", []string{feedback})
	if err != nil {
		t.Fatal(err)
	}
	v3 := v3result["version"].(map[string]any)["dataset_version_id"].(string)
	base, err := repository.Base(ctx, id, v3)
	if err != nil || base.Number != 3 {
		t.Fatal("v3", err)
	}
	sources := base.Manifest["feedback_sources"].([]any)[0].(map[string]any)
	if sources["source_version_id"] != v1 || base.Manifest["parent_version_id"] != v2 {
		t.Fatal("source version incorrectly used as base", sources)
	}
	candidates, err = repository.Candidates(ctx, id)
	if err != nil || len(candidates) != 0 {
		t.Fatal("feedback consumed twice", err)
	}

	// Only one concurrent publication from the same base may advance the main line.
	nextFiles := []string{versionArchive(t, map[string]string{"train/bird/next.png": "concurrent A"}), versionArchive(t, map[string]string{"train/bird/next.png": "concurrent B"})}
	errs := make([]error, 2)
	var group sync.WaitGroup
	for i := range nextFiles {
		group.Add(1)
		go func(i int) {
			defer group.Done()
			_, errs[i] = s.Expand(ctx, id, v3, uuid.NewString(), nextFiles[i], nil)
		}(i)
	}
	group.Wait()
	successful, conflicts := 0, 0
	for _, err := range errs {
		if err == nil {
			successful++
		} else if errors.Is(err, dataset.ErrConflict) {
			conflicts++
		} else {
			t.Fatal(err)
		}
	}
	if successful != 1 || conflicts != 1 {
		t.Fatalf("concurrent versions: %v", errs)
	}
	var count, maximum int
	if err = pool.QueryRow(ctx, `SELECT count(*),max(version_number) FROM dataset_versions WHERE dataset_id=$1`, id).Scan(&count, &maximum); err != nil || count != 4 || maximum != 4 {
		t.Fatalf("version allocation %d/%d: %v", count, maximum, err)
	}
}

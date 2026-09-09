package dataset

import (
	"archive/zip"
	"context"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

func testArchive(t *testing.T, files map[string]string) string {
	t.Helper()
	name := filepath.Join(t.TempDir(), "images.zip")
	file, err := os.Create(name)
	if err != nil {
		t.Fatal(err)
	}
	writer := zip.NewWriter(file)
	names := make([]string, 0, len(files))
	for name := range files {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		out, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = io.WriteString(out, files[name]); err != nil {
			t.Fatal(err)
		}
	}
	if err = writer.Close(); err != nil {
		t.Fatal(err)
	}
	file.Close()
	return name
}
func TestTrainingExpansionPreservesEveryEvaluationImage(t *testing.T) {
	ctx := context.Background()
	store := artifact.NewLocalStore(t.TempDir())
	s := Service{Store: store}
	old := testArchive(t, map[string]string{"bird/train.png": "old training", "bird/val.png": "validation", "bird/test.png": "testing"})
	descriptor, err := store.PutFile(ctx, old, artifact.PutRequest{ArtifactID: uuid.NewString(), ArtifactType: "dataset_archive"})
	if err != nil {
		t.Fatal(err)
	}
	samples := []any{}
	for _, split := range []string{"train", "val", "test"} {
		samples = append(samples, map[string]any{"path": "bird/" + split + ".png", "label": "bird", "split": split})
	}
	base := Snapshot{Archive: descriptor, Manifest: map[string]any{"samples": samples}}
	incoming := testArchive(t, map[string]string{"bird/new.png": "new image", "bird/duplicate-evaluation.png": "testing"})
	result, changes, err := s.merge(ctx, base, incoming, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(result)
	if changes["added_count"] != 1 || changes["duplicate_count"] != 1 {
		t.Fatalf("changes: %#v", changes)
	}
	archive, err := zip.OpenReader(result)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	contents := map[string][]string{}
	for _, file := range archive.File {
		reader, _ := file.Open()
		data, _ := io.ReadAll(reader)
		reader.Close()
		split := strings.Split(file.Name, "/")[0]
		contents[split] = append(contents[split], string(data))
	}
	if len(contents["train"]) != 2 || len(contents["val"]) != 1 || contents["val"][0] != "validation" || len(contents["test"]) != 1 || contents["test"][0] != "testing" {
		t.Fatalf("evaluation membership changed: %#v", contents)
	}
	if err = store.Verify(ctx, descriptor); err != nil {
		t.Fatalf("original archive changed: %v", err)
	}
	for _, tc := range []struct {
		name    string
		files   map[string]string
		message string
	}{
		{"test addition", map[string]string{"test/bird/new.png": "new"}, "仅支持扩充训练集"},
		{"validation addition", map[string]string{"val/bird/new.png": "new"}, "仅支持扩充训练集"},
		{"duplicate only", map[string]string{"bird/new.png": "old training"}, "没有新增图片"},
		{"conflicting label", map[string]string{"cat/new.png": "testing"}, "不同标签"},
		{"unsafe label", map[string]string{"../new.png": "new"}, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, _, err := s.merge(ctx, base, testArchive(t, tc.files), nil)
			if err == nil || !strings.Contains(err.Error(), tc.message) {
				t.Fatalf("unexpected error %v", err)
			}
		})
	}
	t.Run("reviewed upload", func(t *testing.T) {
		s.UploadRoot = t.TempDir()
		file := filepath.Join(s.UploadRoot, "reviewed.png")
		if err = os.WriteFile(file, []byte("reviewed image"), 0600); err != nil {
			t.Fatal(err)
		}
		feedback := Feedback{ID: uuid.NewString(), Label: "bird", InputRef: file, SourceVersionID: "v1", ModelVersionID: "model-a", ReviewID: "review-a"}
		result, changes, err := s.merge(ctx, base, "", []Feedback{feedback})
		if err != nil {
			t.Fatal(err)
		}
		defer os.Remove(result)
		if changes["added_count"] != 1 {
			t.Fatal(changes)
		}
		feedback.InputRef = filepath.Join(t.TempDir(), "outside.png")
		os.WriteFile(feedback.InputRef, []byte("outside"), 0600)
		if _, _, err = s.merge(ctx, base, "", []Feedback{feedback}); err == nil {
			t.Fatal("read outside configured upload root")
		}
	})
}

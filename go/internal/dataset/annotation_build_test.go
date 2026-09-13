package dataset

import (
	"archive/zip"
	"context"
	"fmt"
	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestAnnotationSplits(t *testing.T) {
	for _, p := range []SplitPlan{{80, 10, 10, 42}, {100, 0, 0, 42}, {1, 49, 50, 9}} {
		if e := p.Validate(); e != nil {
			t.Fatal(e)
		}
		for n := 1; n < 101; n++ {
			a := SplitLabels(n, p)
			if a[0]+a[1]+a[2] != n || a[0] < 1 {
				t.Fatal(n, a)
			}
		}
	}
	if (SplitPlan{80, 20, 10, 42}).Validate() == nil {
		t.Fatal("accepted invalid ratio")
	}
	if got := SplitLabels(10, SplitPlan{80, 10, 10, 42}); got != [3]int{8, 1, 1} {
		t.Fatal(got)
	}
}
func TestAnnotationArchiveImmutabilityAndDeterminism(t *testing.T) {
	ctx := context.Background()
	store := artifact.NewLocalStore(t.TempDir())
	s := Service{Store: store}
	image := func(label, body string) LabeledImage {
		f := filepath.Join(t.TempDir(), "sample.png")
		if e := os.WriteFile(f, []byte(body), 0600); e != nil {
			t.Fatal(e)
		}
		a, e := store.PutFile(ctx, f, artifact.PutRequest{ArtifactID: uuid.NewString(), ArtifactType: "annotation_image"})
		if e != nil {
			t.Fatal(e)
		}
		return LabeledImage{ID: uuid.NewString(), Label: label, Filename: "sample.png", Image: a}
	}
	images := []LabeledImage{}
	for _, label := range []string{"Alpha", "Beta"} {
		for i := 0; i < 10; i++ {
			images = append(images, image(label, fmt.Sprintf("%s-%d", label, i)))
		}
	}
	p := SplitPlan{80, 10, 10, 42}
	first, changes, e := s.BuildLabeledArchive(ctx, nil, images, p, false)
	if e != nil {
		t.Fatal(e)
	}
	defer os.Remove(first)
	if changes["added_count"] != 20 || !reflect.DeepEqual(changes["split_counts"], map[string]map[string]int{"train": {"Alpha": 8, "Beta": 8}, "val": {"Alpha": 1, "Beta": 1}, "test": {"Alpha": 1, "Beta": 1}}) {
		t.Fatal(changes)
	}
	for i, j := 0, len(images)-1; i < j; i, j = i+1, j-1 {
		images[i], images[j] = images[j], images[i]
	}
	second, _, e := s.BuildLabeledArchive(ctx, nil, images, p, false)
	if e != nil {
		t.Fatal(e)
	}
	defer os.Remove(second)
	a, _ := os.ReadFile(first)
	b, _ := os.ReadFile(second)
	if string(a) != string(b) {
		t.Fatal("upload order changed reproducible archive")
	}
	descriptor, e := store.PutFile(ctx, first, artifact.PutRequest{ArtifactID: uuid.NewString(), ArtifactType: "dataset_archive"})
	if e != nil {
		t.Fatal(e)
	}
	z, _ := zip.OpenReader(first)
	defer z.Close()
	samples := []any{}
	original := map[string]string{}
	var evaluation, training LabeledImage
	for _, f := range z.File {
		parts := strings.Split(f.Name, "/")
		samples = append(samples, map[string]any{"path": f.Name, "label": parts[1], "split": parts[0]})
		r, _ := f.Open()
		data, _ := io.ReadAll(r)
		r.Close()
		original[string(data)] = parts[0] + ":" + parts[1]
		if parts[0] == "test" {
			evaluation = image(parts[1], string(data))
		}
		if parts[0] == "train" {
			training = image(parts[1], string(data))
		}
	}
	base := Snapshot{Archive: descriptor, Manifest: map[string]any{"samples": samples}}
	next, report, e := s.BuildLabeledArchive(ctx, &base, []LabeledImage{image("Alpha", "new image"), training}, p, true)
	if e != nil {
		t.Fatal(e)
	}
	defer os.Remove(next)
	if report["added_count"] != 1 || report["duplicate_count"] != 1 {
		t.Fatal(report)
	}
	nz, _ := zip.OpenReader(next)
	defer nz.Close()
	for _, f := range nz.File {
		r, _ := f.Open()
		data, _ := io.ReadAll(r)
		r.Close()
		if want, ok := original[string(data)]; ok {
			parts := strings.Split(f.Name, "/")
			if parts[0]+":"+parts[1] != want {
				t.Fatal("old label/split changed")
			}
		}
	}
	if _, _, e = s.BuildLabeledArchive(ctx, &base, []LabeledImage{evaluation}, p, true); e == nil || !strings.Contains(e.Error(), "验证集") {
		t.Fatal(e)
	}
	training.Label = "Other"
	if _, _, e = s.BuildLabeledArchive(ctx, &base, []LabeledImage{training}, p, true); e == nil || !strings.Contains(e.Error(), "标签冲突") {
		t.Fatal(e)
	}
	if e = store.Verify(ctx, descriptor); e != nil {
		t.Fatal("baseline mutated", e)
	}
	newArchive, newReport, e := s.BuildLabeledArchive(ctx, &base, []LabeledImage{image("Gamma", "new category image"), image("Alpha", "same category image")}, p, true)
	if e != nil {
		t.Fatal(e)
	}
	defer os.Remove(newArchive)
	if !reflect.DeepEqual(newReport["new_classes"], []string{"Gamma"}) || !reflect.DeepEqual(newReport["existing_class_additions"], map[string]int{"Alpha": 1}) || !reflect.DeepEqual(newReport["new_class_additions"], map[string]int{"Gamma": 1}) {
		t.Fatal(newReport)
	}
}

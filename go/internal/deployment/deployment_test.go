package deployment

import (
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
	"testing"
)

func fixture() modelregistry.Version {
	b := artifact.Descriptor{ArtifactID: "bundle", ArtifactType: "model_bundle", SHA256: "sha-bundle"}
	v := modelregistry.Version{ID: "model", Status: "production", HeadType: "image_classifier_v2", DatasetVersionID: "dataset", TrainingRunID: "run"}
	for _, p := range []string{"FP16", "FP32"} {
		v.Artifacts = append(v.Artifacts, artifact.Descriptor{ArtifactID: p, ArtifactType: "full_onnx", SHA256: p, DatasetVersionID: "dataset", TrainingRunID: "run", Metadata: map[string]any{"precision": p, "model_version_id": "model", "source_bundle_sha256": b.SHA256}})
	}
	v.Artifacts = append(v.Artifacts, b)
	return v
}
func TestPortableOrderingAndPrecision(t *testing.T) {
	v := fixture()
	_, r, e := Resolve(v, nil, "")
	if e != nil || r.Precision != "FP32" {
		t.Fatal(r, e)
	}
	_, r, e = Resolve(v, nil, "onnx:FP16")
	if e != nil || r.Precision != "FP16" {
		t.Fatal(r, e)
	}
	v.Status = "archived"
	if _, _, e = Resolve(v, nil, ""); e == nil {
		t.Fatal("archived accepted")
	}
}
func TestCompiledScopeAndReadiness(t *testing.T) {
	v := fixture()
	_, p := PortableVariants(v)
	r := Record{ID: "engine", ModelVersionID: v.ID, Runtime: "tensorrt", Status: "ready", Source: p[0].Source, Compiled: &artifact.Descriptor{ArtifactID: "compiled"}}
	if _, _, e := Resolve(v, []Record{r}, r.ID); e != nil {
		t.Fatal(e)
	}
	for _, status := range []string{"queued", "building", "failed"} {
		copy := r
		copy.Status = status
		if _, _, e := Resolve(v, []Record{copy}, r.ID); e == nil {
			t.Fatal(status)
		}
	}
	r.Source.SHA256 = "other"
	if _, _, e := Resolve(v, []Record{r}, r.ID); e == nil {
		t.Fatal("wrong source accepted")
	}
	r.ModelVersionID = "other"
	if _, _, e := Resolve(v, []Record{r}, r.ID); e == nil {
		t.Fatal("wrong model accepted")
	}
	v.Artifacts[0].DatasetVersionID = "other"
	_, p = PortableVariants(v)
	if len(p) != 1 {
		t.Fatal(p)
	}
}
func TestBuildValidation(t *testing.T) {
	for _, c := range []Create{{Runtime: "cuda", Precision: "FP16", Actor: "x"}, {Runtime: "ascend_acl", Precision: "FP16", MaxBatch: 2, Actor: "x"}, {Runtime: "tensorrt", Precision: "INT8", Actor: "x"}, {Runtime: "tensorrt", Precision: "FP32", Actor: " "}} {
		if c.Validate() == nil {
			t.Fatal(c)
		}
	}
	c := Create{Runtime: "tensorrt", Precision: "FP32", Actor: "user"}
	if e := c.Validate(); e != nil || c.MaxBatch != 1 {
		t.Fatal(c, e)
	}
}

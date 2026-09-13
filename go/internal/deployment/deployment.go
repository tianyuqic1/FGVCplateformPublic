// Package deployment defines immutable executable variants of a published model.
package deployment

import (
	"fmt"
	"sort"
	"strings"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/review"
)

type Target struct {
	Runtime string `json:"runtime"`
	Profile string `json:"target_profile"`
	Address string `json:"-"`
}
type Record struct {
	DatasetID      string               `json:"dataset_id,omitempty"`
	ID             string               `json:"id"`
	ModelVersionID string               `json:"model_version_id"`
	Runtime        string               `json:"runtime"`
	Precision      string               `json:"precision"`
	TargetProfile  string               `json:"target_profile"`
	MaxBatch       int                  `json:"max_batch"`
	Status         string               `json:"status"`
	Source         artifact.Descriptor  `json:"source"`
	Compiled       *artifact.Descriptor `json:"compiled,omitempty"`
	Validation     map[string]any       `json:"validation"`
	Error          string               `json:"error"`
	WorkerID       string               `json:"worker_id,omitempty"`
	UpdatedAt      string               `json:"updated_at,omitempty"`
	BuildToken     string               `json:"build_token,omitempty"`
}
type Create struct {
	Runtime   string `json:"runtime"`
	Precision string `json:"precision"`
	MaxBatch  int    `json:"max_batch"`
	Actor     string `json:"actor"`
}

func (c *Create) Validate() error {
	if c.Runtime != "tensorrt" && c.Runtime != "ascend_acl" {
		return fmt.Errorf("未知部署后端: %w", review.ErrInvalid)
	}
	if c.Precision != "FP32" && c.Precision != "FP16" {
		return fmt.Errorf("请选择 FP32 或 FP16: %w", review.ErrInvalid)
	}
	if c.MaxBatch == 0 {
		c.MaxBatch = 1
	}
	if c.MaxBatch < 1 || c.MaxBatch > 32 || (c.Runtime == "ascend_acl" && c.MaxBatch != 1) {
		return fmt.Errorf("批大小无效；Ascend 首版仅支持 batch=1: %w", review.ErrInvalid)
	}
	c.Actor = strings.TrimSpace(c.Actor)
	if c.Actor == "" || len(c.Actor) > 200 {
		return fmt.Errorf("操作人不能为空: %w", review.ErrInvalid)
	}
	return nil
}

// PortableVariants uses stable artifact IDs and FP32-first ordering, independent of DB ordering.
// Historical ONNX exports need not be republished or assigned new release numbers.
func PortableVariants(v modelregistry.Version) (artifact.Descriptor, []Record) {
	var bundles []artifact.Descriptor
	for _, a := range v.Artifacts {
		if a.ArtifactType == "model_bundle" {
			bundles = append(bundles, a)
		}
	}
	sort.Slice(bundles, func(i, j int) bool { return bundles[i].ArtifactID < bundles[j].ArtifactID })
	var bundle artifact.Descriptor
	if len(bundles) > 0 {
		bundle = bundles[0]
	}
	rows := []Record{}
	if v.Status != "production" || bundle.ArtifactID == "" {
		return bundle, rows
	}
	kind := "head_onnx"
	if v.HeadType == "image_classifier_v2" {
		kind = "full_onnx"
	}
	for _, a := range v.Artifacts {
		if a.ArtifactType != kind || a.Metadata["model_version_id"] != v.ID || a.Metadata["source_bundle_sha256"] != bundle.SHA256 || a.DatasetVersionID != v.DatasetVersionID || a.TrainingRunID != v.TrainingRunID {
			continue
		}
		precision := modelregistry.ArtifactPrecision(a.Metadata)
		if precision != "FP32" && precision != "FP16" {
			continue
		}
		rows = append(rows, Record{ID: "onnx:" + a.ArtifactID, ModelVersionID: v.ID, Runtime: "onnx_cpu", Precision: precision, TargetProfile: "cpu", MaxBatch: 1, Status: "ready", Source: a, Validation: map[string]any{"export_parity_passed": a.Metadata["parity_passed"]}})
	}
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].Precision != rows[j].Precision {
			return rows[i].Precision == "FP32"
		}
		return rows[i].ID < rows[j].ID
	})
	return bundle, rows
}
func Resolve(v modelregistry.Version, records []Record, id string) (artifact.Descriptor, Record, error) {
	bundle, portable := PortableVariants(v)
	if v.Status != "production" {
		return bundle, Record{}, fmt.Errorf("模型尚未发布: %w", review.ErrConflict)
	}
	if id == "" {
		if len(portable) > 0 {
			return bundle, portable[0], nil
		}
		return bundle, Record{}, fmt.Errorf("缺少已发布 ONNX: %w", review.ErrConflict)
	}
	for _, r := range append(portable, records...) {
		if r.ID != id || r.ModelVersionID != v.ID {
			continue
		}
		if r.Status != "ready" {
			return bundle, Record{}, fmt.Errorf("部署尚未就绪: %w", review.ErrConflict)
		}
		valid := false
		for _, p := range portable {
			if p.Source.ArtifactID == r.Source.ArtifactID && p.Source.SHA256 == r.Source.SHA256 {
				valid = true
			}
		}
		if !valid || (r.Runtime != "onnx_cpu" && r.Compiled == nil) {
			return bundle, Record{}, fmt.Errorf("部署源模型不匹配: %w", review.ErrConflict)
		}
		return bundle, r, nil
	}
	return bundle, Record{}, fmt.Errorf("该部署不属于当前模型: %w", review.ErrInvalid)
}

package toolcontract

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"strings"
	"unicode/utf8"
)

// Schema is the JSON Schema subset used by the registered, closed tool inputs.
// Inputs are validated locally and again at the platform boundary.
type Definition struct {
	Name           string `json:"name"`
	Description    string `json:"description"`
	ReadOnly       bool   `json:"read_only"`
	TimeoutSeconds int    `json:"timeout_seconds"`
	InputSchema    Schema `json:"input_schema"`
}

type Schema struct {
	Type                 string            `json:"type"`
	Properties           map[string]Schema `json:"properties,omitempty"`
	Required             []string          `json:"required,omitempty"`
	AdditionalProperties bool              `json:"additionalProperties"`
	Items                *Schema           `json:"items,omitempty"`
	Minimum              *float64          `json:"minimum,omitempty"`
	Maximum              *float64          `json:"maximum,omitempty"`
	ExclusiveMinimum     *float64          `json:"exclusiveMinimum,omitempty"`
	MinLength            int               `json:"minLength,omitempty"`
	MaxLength            int               `json:"maxLength,omitempty"`
	MinItems             int               `json:"minItems,omitempty"`
	MaxItems             int               `json:"maxItems,omitempty"`
	UniqueItems          bool              `json:"uniqueItems,omitempty"`
	Enum                 []any             `json:"enum,omitempty"`
}

func (s Schema) Validate(raw json.RawMessage) error {
	if len(raw) > 64<<10 {
		return fmt.Errorf("arguments exceed 64 KiB")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	var v any
	if err := d.Decode(&v); err != nil {
		return fmt.Errorf("invalid JSON arguments")
	}
	if d.Decode(new(any)) != io.EOF {
		return fmt.Errorf("expected one JSON value")
	}
	return s.validate(v, "arguments")
}

func (s Schema) validate(v any, path string) error {
	if len(s.Enum) > 0 {
		found := false
		encoded, _ := json.Marshal(v)
		for _, allowed := range s.Enum {
			candidate, _ := json.Marshal(allowed)
			if bytes.Equal(encoded, candidate) {
				found = true
				break
			}
		}
		if !found {
			return fmt.Errorf("%s has unsupported value", path)
		}
	}
	bad := func() error { return fmt.Errorf("%s must be %s", path, s.Type) }
	switch s.Type {
	case "object":
		m, ok := v.(map[string]any)
		if !ok {
			return bad()
		}
		for _, k := range s.Required {
			if _, ok := m[k]; !ok {
				return fmt.Errorf("%s.%s is required", path, k)
			}
		}
		for k, item := range m {
			field, ok := s.Properties[k]
			if !ok {
				if !s.AdditionalProperties {
					return fmt.Errorf("%s.%s is not allowed", path, k)
				}
				continue
			}
			if err := field.validate(item, path+"."+k); err != nil {
				return err
			}
		}
	case "string":
		text, ok := v.(string)
		if !ok {
			return bad()
		}
		length := utf8.RuneCountInString(text)
		if length < s.MinLength || (s.MaxLength > 0 && length > s.MaxLength) || strings.IndexByte(text, 0) >= 0 {
			return fmt.Errorf("%s has invalid length or characters", path)
		}
	case "integer", "number":
		n, ok := v.(float64)
		if !ok || math.IsNaN(n) || math.IsInf(n, 0) || (s.Type == "integer" && math.Trunc(n) != n) {
			return bad()
		}
		if (s.Minimum != nil && n < *s.Minimum) || (s.Maximum != nil && n > *s.Maximum) {
			return fmt.Errorf("%s is outside the allowed range", path)
		}
		if s.ExclusiveMinimum != nil && n <= *s.ExclusiveMinimum {
			return fmt.Errorf("%s must be greater than its lower bound", path)
		}
	case "boolean":
		if _, ok := v.(bool); !ok {
			return bad()
		}
	case "array":
		items, ok := v.([]any)
		if !ok {
			return bad()
		}
		if len(items) < s.MinItems || (s.MaxItems > 0 && len(items) > s.MaxItems) {
			return fmt.Errorf("%s has invalid item count", path)
		}
		seen := map[string]bool{}
		for i, item := range items {
			if s.Items != nil {
				if err := s.Items.validate(item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
					return err
				}
			}
			if s.UniqueItems {
				b, _ := json.Marshal(item)
				key := string(b)
				if seen[key] {
					return fmt.Errorf("%s contains duplicates", path)
				}
				seen[key] = true
			}
		}
	default:
		return fmt.Errorf("unsupported schema type %q", s.Type)
	}
	return nil
}

func str(min, max int) Schema { return Schema{Type: "string", MinLength: min, MaxLength: max} }
func num(kind string, min, max float64) Schema {
	return Schema{Type: kind, Minimum: &min, Maximum: &max}
}
func obj(props map[string]Schema, required ...string) Schema {
	return Schema{Type: "object", Properties: props, Required: required}
}

func TrainingSchema() Schema {
	zero, one := float64(0), float64(1)
	rate := Schema{Type: "number", ExclusiveMinimum: &zero, Maximum: &one}
	augs := map[string]Schema{}
	for _, name := range []string{"random_resized_crop", "horizontal_flip", "vertical_flip", "color_jitter", "random_rotation", "random_erasing"} {
		augs[name] = Schema{Type: "boolean"}
	}
	head := obj(map[string]Schema{
		"epochs": num("integer", 1, 1000), "batch_size": num("integer", 1, 128),
		"learning_rate": rate, "head_learning_rate": rate,
		"backbone_learning_rate": rate, "lora_learning_rate": rate,
		"weight_decay": num("number", 0, 1), "lora_enabled": {Type: "boolean"}, "lora_rank": {Type: "integer", Enum: []any{8, 16}}, "augmentations": obj(augs),
	})
	return obj(map[string]Schema{
		"dataset_id": str(1, 200), "dataset_version_id": str(1, 200), "backbone_key": str(1, 150), "name": str(1, 80),
		"max_attempts": num("integer", 1, 10), "feature_batch_size": num("integer", 1, 128), "image_size": num("integer", 128, 512), "head_config": head,
	}, "dataset_version_id", "backbone_key")
}

func Definitions() []Definition {
	page := func(extra map[string]Schema) Schema {
		p := map[string]Schema{"q": str(0, 100), "limit": num("integer", 1, 100), "offset": num("integer", 0, 100000)}
		for k, v := range extra {
			p[k] = v
		}
		return obj(p)
	}
	id := func(key string) Schema { return obj(map[string]Schema{key: str(1, 200)}, key) }
	ids := str(1, 200)
	return []Definition{
		{"datasets.list", "查找数据集", true, 10, page(map[string]Schema{"status": str(1, 30)})},
		{"datasets.get_version", "读取具体数据版本的统计、划分和来源", true, 10, id("dataset_version_id")},
		{"training.list_runs", "查找历史训练", true, 10, page(map[string]Schema{"status": str(1, 30), "dataset_id": str(1, 200), "backbone_id": str(1, 150)})},
		{"training.get_run", "读取训练配置、状态和错误摘要", true, 10, id("run_id")},
		{"training.analyze_test_errors", "分析某次训练最终测试集的错分：易混类别、置信度、典型错误和证据局限；只读已保存报告，不重跑推理。旧报告可能只有聚合数据或无法确认测试范围，不得把假设当事实", true, 25, obj(map[string]Schema{"run_id": str(1, 200), "max_confusion_pairs": num("integer", 1, 20), "max_examples": num("integer", 1, 20)}, "run_id")},
		{"training.get_metrics", "分页读取训练指标", true, 20, obj(map[string]Schema{"run_id": str(1, 200), "metric_name": str(1, 100), "attempt_id": str(1, 200), "after_id": num("integer", 0, 9007199254740991), "limit": num("integer", 1, 1000)}, "run_id")},
		{"models.list_versions", "查找模型版本", true, 10, page(map[string]Schema{"dataset_id": str(1, 200), "dataset_version_id": str(1, 200), "architecture": str(1, 100), "pretraining": str(1, 100), "status": str(1, 30)})},
		{"models.get_version", "读取模型指标、来源和发布状态", true, 10, id("model_version_id")},
		{"models.compare", "比较同一评估范围的模型", true, 20, obj(map[string]Schema{"model_version_ids": {Type: "array", Items: &ids, MinItems: 2, MaxItems: 5, UniqueItems: true}}, "model_version_ids")},
		{"catalog.list_backbones", "查询骨干和训练方式", true, 10, obj(map[string]Schema{})},
		{"workers.get_capacity", "读取当前用户可见的 Worker 可用性和负载", true, 10, page(map[string]Schema{"kind": str(1, 30), "status": str(1, 30), "device": str(1, 100)})},
		{"training.validate_plan", "无副作用地校验训练配置", true, 20, TrainingSchema()},
		{"training.submit_plan", "提交用户已确认的单个训练", false, 30, TrainingSchema()},
		{"training.cancel_run", "取消用户明确指定的训练", false, 20, id("run_id")},
		{"inference.run", "对指定数据版本内一个样本执行推理", false, 120, obj(map[string]Schema{
			"dataset_version_id": str(1, 200), "model_version_id": str(1, 200), "sample_id": str(1, 200), "deployment_id": str(1, 200), "top_k": num("integer", 1, 100), "evidence_k": num("integer", 0, 20),
		}, "dataset_version_id", "model_version_id", "sample_id")},
	}
}

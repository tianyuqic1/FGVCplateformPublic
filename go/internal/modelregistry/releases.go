package modelregistry

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
)

// Only model-defining configuration belongs in the signature, not optimizer,
// epoch count, metrics, display names, or deployment precision.
func ModelSignature(v Version) string {
	config := map[string]any{}
	for _, key := range []string{"model_name", "pretrained_sha256"} {
		if value, ok := v.TrainingConfig[key]; ok {
			config[key] = value
		}
	}
	mode := "frozen"
	if v.PretrainingMethod == "supervised" {
		mode = "full"
	}
	if v.TrainingConfig["lora_enabled"] == true {
		mode = "lora"
		config["lora_rank"] = v.TrainingConfig["lora_rank"]
		if config["lora_rank"] == nil {
			config["lora_rank"] = 8
		}
	}
	config["training_mode"] = mode
	data, _ := json.Marshal([]any{v.BackboneKey, v.Architecture, v.PretrainingMethod, v.PretrainingDataset, v.HeadType, v.Pooling, v.InputSize, config})
	return fmt.Sprintf("%x", sha256.Sum256(data))
}

func LatestRelease(versions []Version, dataset string) *Version {
	var latest *Version
	for _, v := range versions {
		if v.DatasetID == dataset && v.ReleaseVersion != "" && (latest == nil || v.ReleaseSequence > latest.ReleaseSequence) {
			copy := v
			latest = &copy
		}
	}
	return latest
}

// Caller must serialize allocation per stable dataset identity. Additional
// formats of an already numbered model never allocate a new release.
func NextRelease(v Version, previous *Version) (string, string, int64) {
	if v.ReleaseVersion != "" {
		return v.ReleaseVersion, "补充部署精度，保留发布版本", v.ReleaseSequence
	}
	if previous == nil {
		return "v1.0.0", "该数据集首次版本化发布", 1
	}
	var major, minor, patch int
	fmt.Sscanf(previous.ReleaseVersion, "v%d.%d.%d", &major, &minor, &patch)
	reason := "相同模型方案与训练数据，更新训练结果"
	if ModelSignature(v) != previous.ReleaseSignature {
		major++
		minor = 0
		patch = 0
		reason = "模型架构、预训练权重或训练策略变化"
	} else if v.DatasetVersionID != previous.DatasetVersionID {
		minor++
		patch = 0
		reason = "训练数据版本变化"
	} else {
		patch++
	}
	return fmt.Sprintf("v%d.%d.%d", major, minor, patch), reason, previous.ReleaseSequence + 1
}

func ArtifactPrecision(metadata map[string]any) string {
	if value, ok := metadata["precision"].(string); ok && value != "" {
		return value
	}
	return "FP32" // Historical exports had a fixed float32 contract.
}

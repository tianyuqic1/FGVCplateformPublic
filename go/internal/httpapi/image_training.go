package httpapi

import (
	"fmt"
	"math"
	"strings"
)

func normalizeImageTrainingConfig(key string, input map[string]any) (map[string]any, error) {
	config := map[string]any{}
	for k, v := range input {
		config[k] = v
	}
	enabled := false
	if value, exists := config["lora_enabled"]; exists {
		var ok bool
		enabled, ok = value.(bool)
		if !ok {
			return nil, fmt.Errorf("lora_enabled must be boolean")
		}
	}
	rank := float64(8)
	if value, exists := config["lora_rank"]; exists {
		var ok bool
		rank, ok = value.(float64)
		if !ok || (rank != 8 && rank != 16) {
			return nil, fmt.Errorf("lora_rank must be 8 or 16")
		}
	}
	mode := "frozen"
	if strings.HasPrefix(key, "imagenet_") {
		if enabled {
			return nil, fmt.Errorf("ImageNet uses full-parameter training; LoRA is not supported")
		}
		mode = "full"
	} else if enabled {
		mode = "lora"
	}
	lr := 0.0001
	if mode == "frozen" {
		lr = 0.001
	}
	defaults := map[string]float64{"epochs": 30, "batch_size": 8, "learning_rate": lr, "weight_decay": 0.0001}
	for name, fallback := range defaults {
		value, exists := config[name]
		if !exists {
			config[name] = fallback
			continue
		}
		number, ok := value.(float64)
		if !ok || math.IsNaN(number) || math.IsInf(number, 0) {
			return nil, fmt.Errorf("invalid %s", name)
		}
		valid := number > 0 && number <= 1
		switch name {
		case "epochs":
			valid = number >= 1 && number <= 1000 && math.Trunc(number) == number
		case "batch_size":
			valid = number >= 1 && number <= 128 && math.Trunc(number) == number
		case "weight_decay":
			valid = number >= 0 && number <= 1
		}
		if !valid {
			return nil, fmt.Errorf("invalid %s", name)
		}
	}
	config["head_type"], config["training_mode"] = "image_classifier_v2", mode
	config["lora_enabled"], config["lora_rank"] = enabled, rank
	return config, nil
}

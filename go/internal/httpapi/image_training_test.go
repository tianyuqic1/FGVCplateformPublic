package httpapi

import "testing"

func TestImageTrainingModes(t *testing.T) {
	for _, tc := range []struct {
		key  string
		lora bool
		rank float64
		mode string
	}{
		{"dinov3_vits16_lvd1689m", false, 8, "frozen"}, {"dinov3_vits16_lvd1689m", true, 8, "lora"},
		{"dinov3_vits16_lvd1689m", true, 16, "lora"}, {"imagenet_vits16_augreg_in21k_ft_in1k", false, 8, "full"},
		{"imagenet_resnet50_a1_in1k", false, 8, "full"},
	} {
		cfg, err := normalizeImageTrainingConfig(tc.key, map[string]any{"lora_enabled": tc.lora, "lora_rank": tc.rank})
		if err != nil || cfg["training_mode"] != tc.mode || cfg["head_type"] != "image_classifier_v2" {
			t.Fatalf("%+v: %v %v", tc, cfg, err)
		}
	}
	for _, cfg := range []map[string]any{{"lora_enabled": "true"}, {"lora_rank": float64(4)}, {"batch_size": float64(256)}, {"epochs": 1.5}, {"learning_rate": 0.0}} {
		if _, err := normalizeImageTrainingConfig("dinov3_vits16_lvd1689m", cfg); err == nil {
			t.Fatalf("accepted invalid config: %v", cfg)
		}
	}
	if _, err := normalizeImageTrainingConfig("imagenet_vits16_augreg_in21k_ft_in1k", map[string]any{"lora_enabled": true}); err == nil {
		t.Fatal("ImageNet LoRA accepted")
	}
}

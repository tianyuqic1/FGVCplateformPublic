package httpapi

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestTrainingParametersHTTPAndDetails(t *testing.T) {
	server := httptest.NewServer(NewRouter(Dependencies{}))
	defer server.Close()
	for _, key := range []string{"dinov3_vits16_lvd1689m", "imagenet_vits16_augreg_in21k_ft_in1k", "imagenet_resnet50_a1_in1k"} {
		for _, size := range []int{224, 256, 320, 384, 448, 512} {
			config := map[string]any{"head_learning_rate": 0.001, "augmentations": map[string]bool{"horizontal_flip": true, "random_erasing": true}}
			if key != "dinov3_vits16_lvd1689m" {
				config["backbone_learning_rate"] = 0.00001
			} else {
				config["lora_enabled"] = true
				config["lora_learning_rate"] = 0.0002
			}
			body, _ := json.Marshal(map[string]any{"dataset_version_id": "version-1", "backbone_key": key, "image_size": size, "head_config": config})
			response, err := http.Post(server.URL+"/api/training-runs", "application/json", bytes.NewReader(body))
			if err != nil {
				t.Fatal(err)
			}
			var payload map[string]map[string]any
			json.NewDecoder(response.Body).Decode(&payload)
			response.Body.Close()
			if response.StatusCode != 202 {
				t.Fatalf("%s/%d: %+v", key, size, payload)
			}
			run := payload["training_run"]
			detail, err := http.Get(fmt.Sprintf("%s/api/training-runs/%s", server.URL, run["id"]))
			if err != nil {
				t.Fatal(err)
			}
			json.NewDecoder(detail.Body).Decode(&payload)
			detail.Body.Close()
			saved := payload["training_run"]
			if saved["extractor_config"].(map[string]any)["image_size"] != float64(size) {
				t.Fatal(saved)
			}
			if saved["head_config"].(map[string]any)["head_learning_rate"] != 0.001 {
				t.Fatal(saved)
			}
		}
	}
	for _, size := range []int{127, 225, 513} {
		response, err := http.Post(server.URL+"/api/training-runs", "application/json", bytes.NewBufferString(fmt.Sprintf(`{"dataset_version_id":"v","backbone_key":"imagenet_vits16_augreg_in21k_ft_in1k","image_size":%d}`, size)))
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != 422 {
			t.Fatal("invalid resolution accepted", size, response.StatusCode)
		}
	}
}

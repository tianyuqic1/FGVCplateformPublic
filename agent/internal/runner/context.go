package runner

import "encoding/json"

// List APIs contain UI-oriented per-class breakdowns. Keep identities and summary
// facts in model context; retain the complete tool response in the evidence log.
func compactContext(raw json.RawMessage) json.RawMessage {
	var value any
	if json.Unmarshal(raw, &value) != nil {
		return raw
	}
	var visit func(any)
	visit = func(v any) {
		switch node := v.(type) {
		case map[string]any:
			if node["tool"] == "datasets.list" {
				if data, ok := node["data"].(map[string]any); ok {
					if datasets, ok := data["datasets"].([]any); ok {
						allowed := []string{"id", "dataset_id", "name", "description", "status", "readiness", "class_count", "sample_count", "version_number", "latest_version_id", "dataset_version_id", "created_at", "updated_at", "pending_candidate_count"}
						for i, item := range datasets {
							if full, ok := item.(map[string]any); ok {
								summary := map[string]any{}
								for _, k := range allowed {
									if val, exists := full[k]; exists {
										summary[k] = val
									}
								}
								datasets[i] = summary
							}
						}
						data["context_projection"] = "仅保留数据集身份、最新版本及就绪统计；具体类别、划分、来源使用 datasets.get_version 查询"
					}
				}
			}
			for _, child := range node {
				visit(child)
			}
		case []any:
			for _, child := range node {
				visit(child)
			}
		}
	}
	visit(value)
	out, e := json.Marshal(value)
	if e != nil {
		return raw
	}
	return out
}

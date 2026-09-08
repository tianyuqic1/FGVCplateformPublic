package llm

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

var allowedTasks = map[string]struct{}{
	"inference_explanation":   {},
	"review_assistance":       {},
	"training_diagnosis":      {},
	"feedback_curation":       {},
	"dataset_card_generation": {},
}

type Application struct {
	gateway Gateway
}

func NewApplication(gateway Gateway) *Application { return &Application{gateway: gateway} }

func (application *Application) Assist(ctx context.Context, task string, input map[string]any) (map[string]any, error) {
	if _, ok := allowedTasks[task]; !ok {
		return nil, fmt.Errorf("unsupported LLM assistance task")
	}
	contextCopy := sanitize(input).(map[string]any)
	imageDataURL := ""
	if image, ok := contextCopy["image_data_url"].(string); ok && strings.HasPrefix(image, "data:image/") && len(image) <= 10*1024*1024 {
		imageDataURL = image
		delete(contextCopy, "image_data_url")
	}
	encoded, err := json.Marshal(contextCopy)
	if err != nil {
		return nil, err
	}
	if len(encoded) > 64*1024 {
		return nil, fmt.Errorf("LLM context exceeds 64 KiB")
	}
	prompt := "You are FineVision's advisory assistant. Return one JSON object. " +
		"Never submit a review, set a final label, activate a policy, or trigger training. " +
		"Allowed response fields: summary, holistic_analysis, final_category_suggestion, " +
		"inspection_notes, suggested_actions, risk_flags, confidence.\nTask: " + task + "\nContext: " + string(encoded)
	generated, err := application.gateway.Generate(ctx, GatewayRequest{
		RequestID: randomID(), Task: task, Prompt: prompt, ImageDataURL: imageDataURL,
	})
	if err != nil {
		return nil, err
	}
	parsed := map[string]any{}
	if err := json.Unmarshal([]byte(generated.Text), &parsed); err != nil {
		parsed["summary"] = generated.Text
	}
	result := map[string]any{
		"task": task, "advisory_only": true, "provider": generated.Provider, "model": generated.Model,
		"created_at": time.Now().UTC().Format(time.RFC3339Nano),
		"summary":    stringValue(parsed["summary"]), "holistic_analysis": stringValue(parsed["holistic_analysis"]),
		"final_category_suggestion": categoryValue(parsed["final_category_suggestion"]),
		"inspection_notes":          stringList(parsed["inspection_notes"]), "suggested_actions": stringList(parsed["suggested_actions"]),
		"risk_flags": stringList(parsed["risk_flags"]), "confidence": stringValue(parsed["confidence"]),
		"usage": map[string]any{"input_tokens": generated.InputTokens, "output_tokens": generated.OutputTokens, "latency_ms": generated.LatencyMS},
	}
	if result["summary"] == "" {
		result["summary"] = stringValue(parsed["holistic_analysis"])
	}
	if result["confidence"] == "" {
		result["confidence"] = "unknown"
	}
	return result, nil
}

func sanitize(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		result := make(map[string]any, len(typed))
		for key, item := range typed {
			result[key] = sanitize(item)
		}
		return result
	case []any:
		result := make([]any, len(typed))
		for index, item := range typed {
			result[index] = sanitize(item)
		}
		return result
	case string:
		if strings.HasPrefix(typed, "/") || (len(typed) >= 3 && typed[1] == ':' && (typed[2] == '\\' || typed[2] == '/')) {
			return "[redacted-local-path]"
		}
		return typed
	default:
		return value
	}
}

func stringValue(value any) string {
	if text, ok := value.(string); ok {
		return strings.TrimSpace(text)
	}
	return ""
}

func stringList(value any) []string {
	items, ok := value.([]any)
	if !ok {
		return []string{}
	}
	result := make([]string, 0, len(items))
	for _, item := range items {
		if text := stringValue(item); text != "" {
			result = append(result, text)
		}
	}
	return result
}

func categoryValue(value any) map[string]any {
	category, ok := value.(map[string]any)
	if !ok {
		return map[string]any{"label": "unknown", "rationale": ""}
	}
	return map[string]any{"label": stringValue(category["label"]), "rationale": stringValue(category["rationale"])}
}

func randomID() string {
	value := make([]byte, 12)
	if _, err := rand.Read(value); err != nil {
		panic(err)
	}
	return "req_" + hex.EncodeToString(value)
}

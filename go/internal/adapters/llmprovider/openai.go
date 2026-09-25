package llmprovider

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/observability"
)

type OpenAICompatible struct {
	baseURL string
	model   string
	apiKey  string
	client  *http.Client
}

func NewOpenAICompatible(baseURL, model, apiKey string, client *http.Client) *OpenAICompatible {
	return &OpenAICompatible{baseURL: strings.TrimRight(baseURL, "/"), model: model, apiKey: apiKey, client: client}
}

func (provider *OpenAICompatible) Generate(ctx context.Context, request llm.GatewayRequest) (llm.GatewayResult, error) {
	content := any(request.Prompt)
	if request.ImageDataURL != "" {
		content = []map[string]any{
			{"type": "text", "text": request.Prompt},
			{"type": "image_url", "image_url": map[string]string{"url": request.ImageDataURL}},
		}
	}
	payload := map[string]any{
		"model":           provider.model,
		"max_tokens":      4096,
		"messages":        []map[string]any{{"role": "user", "content": content}},
		"response_format": map[string]string{"type": "json_object"},
	}
	if strings.Contains(provider.model, "deepseek-v4") {
		payload["thinking"] = map[string]string{"type": "disabled"}
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return llm.GatewayResult{}, err
	}
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodPost, provider.baseURL+"/chat/completions", bytes.NewReader(encoded))
	if err != nil {
		return llm.GatewayResult{}, err
	}
	httpRequest.Header.Set("Content-Type", "application/json")
	httpRequest.Header.Set("Accept", "application/json")
	httpRequest.Header.Set("X-Request-ID", request.RequestID)
	if provider.apiKey != "" {
		httpRequest.Header.Set("Authorization", "Bearer "+provider.apiKey)
	}
	started := time.Now()
	response, err := provider.client.Do(httpRequest)
	if err != nil {
		observability.RecordLLM("openai-compatible", "failed", time.Since(started), 0, 0)
		return llm.GatewayResult{}, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		outcome := "failed"
		if response.StatusCode == http.StatusTooManyRequests {
			outcome = "rate_limited"
		}
		if response.StatusCode >= 500 {
			outcome = "server_error"
		}
		observability.RecordLLM("openai-compatible", outcome, time.Since(started), 0, 0)
		return llm.GatewayResult{}, fmt.Errorf("LLM provider returned HTTP %d", response.StatusCode)
	}
	var body struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
		Usage struct {
			PromptTokens     int `json:"prompt_tokens"`
			CompletionTokens int `json:"completion_tokens"`
		} `json:"usage"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 4*1024*1024)).Decode(&body); err != nil {
		observability.RecordLLM("openai-compatible", "failed", time.Since(started), 0, 0)
		return llm.GatewayResult{}, err
	}
	if len(body.Choices) == 0 || strings.TrimSpace(body.Choices[0].Message.Content) == "" {
		observability.RecordLLM("openai-compatible", "failed", time.Since(started), body.Usage.PromptTokens, body.Usage.CompletionTokens)
		return llm.GatewayResult{}, fmt.Errorf("LLM provider returned no text")
	}
	observability.RecordLLM("openai-compatible", "success", time.Since(started), body.Usage.PromptTokens, body.Usage.CompletionTokens)
	return llm.GatewayResult{
		Provider: "openai-compatible", Model: provider.model, Text: body.Choices[0].Message.Content,
		InputTokens: body.Usage.PromptTokens, OutputTokens: body.Usage.CompletionTokens,
		LatencyMS: time.Since(started).Milliseconds(),
	}, nil
}

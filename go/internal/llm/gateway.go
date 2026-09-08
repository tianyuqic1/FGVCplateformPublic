package llm

import "context"

type GatewayRequest struct {
	RequestID    string `json:"request_id"`
	Task         string `json:"task"`
	Prompt       string `json:"prompt"`
	ImageDataURL string `json:"image_data_url,omitempty"`
}

type GatewayResult struct {
	Provider     string `json:"provider"`
	Model        string `json:"model"`
	Text         string `json:"text"`
	InputTokens  int    `json:"input_tokens"`
	OutputTokens int    `json:"output_tokens"`
	LatencyMS    int64  `json:"latency_ms"`
}

type Gateway interface {
	Generate(context.Context, GatewayRequest) (GatewayResult, error)
}

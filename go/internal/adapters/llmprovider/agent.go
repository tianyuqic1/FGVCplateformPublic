package llmprovider

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/modelcontract"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/observability"
	"io"
	"net/http"
	"strings"
	"time"
)

func (p *OpenAICompatible) Chat(ctx context.Context, in modelcontract.Request) (modelcontract.Response, error) {
	var out modelcontract.Response
	payload := map[string]any{"model": p.model, "messages": in.Messages, "max_tokens": 4096}
	if len(in.Tools) > 0 {
		payload["tools"] = in.Tools
		payload["tool_choice"] = "auto"
		payload["parallel_tool_calls"] = true
	}
	if strings.Contains(p.model, "deepseek-v4") {
		payload["thinking"] = map[string]string{"type": "disabled"}
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return out, err
	}
	req, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/chat/completions", bytes.NewReader(body))
	if err != nil {
		return out, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Request-ID", in.RequestID)
	if p.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+p.apiKey)
	}
	started := time.Now()
	resp, err := p.client.Do(req)
	if err != nil {
		return out, fmt.Errorf("provider transport failed")
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return out, fmt.Errorf("provider HTTP %d", resp.StatusCode)
	}
	var decoded struct {
		Model   string `json:"model"`
		Choices []struct {
			Message modelcontract.Message `json:"message"`
		} `json:"choices"`
		Usage struct {
			Input  int `json:"prompt_tokens"`
			Output int `json:"completion_tokens"`
		} `json:"usage"`
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, (2<<20)+1))
	if err != nil || len(raw) > 2<<20 || json.Unmarshal(raw, &decoded) != nil || len(decoded.Choices) != 1 {
		return out, fmt.Errorf("invalid provider response")
	}
	out = modelcontract.Response{Message: decoded.Choices[0].Message, Model: decoded.Model, InputTokens: decoded.Usage.Input, OutputTokens: decoded.Usage.Output}
	if out.Message.Role != "assistant" || (strings.TrimSpace(out.Message.Content) == "" && len(out.Message.ToolCalls) == 0) || len(out.Message.ToolCalls) > 24 {
		return out, fmt.Errorf("invalid assistant message")
	}
	observability.RecordLLM("openai-compatible", "success", time.Since(started), out.InputTokens, out.OutputTokens)
	return out, nil
}

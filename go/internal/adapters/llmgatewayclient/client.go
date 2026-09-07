package llmgatewayclient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
)

type Client struct {
	baseURL string
	token   string
	http    *http.Client
}

func New(baseURL, token string, client *http.Client) *Client {
	return &Client{baseURL: strings.TrimRight(baseURL, "/"), token: token, http: client}
}

func (client *Client) Generate(ctx context.Context, command llm.GatewayRequest) (llm.GatewayResult, error) {
	encoded, err := json.Marshal(command)
	if err != nil {
		return llm.GatewayResult{}, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, client.baseURL+"/internal/v1/generate", bytes.NewReader(encoded))
	if err != nil {
		return llm.GatewayResult{}, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer "+client.token)
	response, err := client.http.Do(request)
	if err != nil {
		return llm.GatewayResult{}, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		return llm.GatewayResult{}, fmt.Errorf("LLM Gateway returned %d", response.StatusCode)
	}
	var payload struct {
		Result llm.GatewayResult `json:"result"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 4*1024*1024)).Decode(&payload); err != nil {
		return llm.GatewayResult{}, err
	}
	return payload.Result, nil
}

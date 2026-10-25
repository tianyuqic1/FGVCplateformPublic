package model

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/modelcontract"
	"io"
	"net/http"
	"net/url"
)

type Client struct {
	URL, Token string
	HTTP       *http.Client
}

func (c *Client) Chat(ctx context.Context, in modelcontract.Request) (modelcontract.Response, error) {
	var out modelcontract.Response
	u, e := url.Parse(c.URL)
	if e != nil || u.Host == "" || u.User != nil || (u.Scheme != "http" && u.Scheme != "https") {
		return out, fmt.Errorf("invalid gateway URL")
	}
	raw, _ := json.Marshal(in)
	r, e := http.NewRequestWithContext(ctx, "POST", c.URL+"/internal/v2/chat", bytes.NewReader(raw))
	if e != nil {
		return out, e
	}
	r.Header.Set("Authorization", "Bearer "+c.Token)
	r.Header.Set("Content-Type", "application/json")
	resp, e := c.HTTP.Do(r)
	if e != nil {
		return out, fmt.Errorf("model transport interrupted; dispatch outcome unknown")
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return out, fmt.Errorf("model gateway HTTP %d; request will not be retried automatically", resp.StatusCode)
	}
	b, e := io.ReadAll(io.LimitReader(resp.Body, (2<<20)+1))
	if e != nil || len(b) > 2<<20 || json.Unmarshal(b, &out) != nil {
		return out, fmt.Errorf("model response invalid; dispatch outcome unknown")
	}
	return out, nil
}

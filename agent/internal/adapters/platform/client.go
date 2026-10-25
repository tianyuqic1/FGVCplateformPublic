package platform

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/tools"
)

type sessionKey struct{}

// Session is request-scoped. It is neither model input nor persisted tool data.
type Session struct {
	Cookie     string `json:"cookie"`
	CSRF       string `json:"csrf"`
	Delegation string `json:"-"`
}

func WithSession(ctx context.Context, s Session) context.Context {
	return context.WithValue(ctx, sessionKey{}, s)
}

type Client struct {
	base string
	http *http.Client
}

func New(base string, client *http.Client) (*Client, error) {
	u, err := url.Parse(base)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return nil, fmt.Errorf("platform URL must be an HTTP(S) origin without credentials or path")
	}
	if client == nil {
		client = &http.Client{Timeout: 125 * time.Second}
	}
	copy := *client
	copy.Jar = nil // A shared cookie jar must never override request-scoped identity.
	if copy.Timeout <= 0 {
		copy.Timeout = 125 * time.Second
	}
	copy.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	return &Client{base: strings.TrimRight(base, "/"), http: &copy}, nil
}

func (c *Client) Invoke(ctx context.Context, name string, args json.RawMessage, control tools.Control) (tools.Result, error) {
	body := map[string]any{"arguments": args}
	if control.OperationID != "" {
		body["operation_id"] = control.OperationID
	}
	if control.Approval != "" {
		body["approval"] = control.Approval
	}
	var result tools.Result
	err := c.request(ctx, "/api/agent-tools/invoke/"+url.PathEscape(name), body, &result, !isRead(name))
	return result, err
}

// Approve is for a trusted, explicit user-confirmation flow. It is deliberately
// not registered as an LLM tool. The platform binds approval to the current user,
// login session, operation and exact canonical arguments.
type ApprovedAction struct {
	Approval    string          `json:"approval"`
	Arguments   json.RawMessage `json:"arguments"`
	OperationID string          `json:"operation_id"`
	ExpiresAt   time.Time       `json:"expires_at"`
}

func (c *Client) Approve(ctx context.Context, name string, args json.RawMessage, operationID string) (ApprovedAction, error) {
	var result ApprovedAction
	err := c.request(ctx, "/api/agent-tools/approvals/"+url.PathEscape(name), map[string]any{"arguments": args, "operation_id": operationID}, &result, false)
	return result, err
}

func (c *Client) Operation(ctx context.Context, id string) (json.RawMessage, error) {
	var value json.RawMessage
	err := c.request(ctx, "/api/agent-tools/operations/"+url.PathEscape(id), nil, &value, false)
	return value, err
}

func isRead(name string) bool {
	for _, d := range tools.Definitions() {
		if d.Name == name {
			return d.ReadOnly
		}
	}
	return false
}

func (c *Client) request(ctx context.Context, path string, body any, out any, write bool) error {
	s, ok := ctx.Value(sessionKey{}).(Session)
	if !ok || (s.Delegation == "" && (s.Cookie == "" || s.CSRF == "")) {
		return &tools.Error{Code: "AUTHENTICATION_REQUIRED", Message: "request-scoped platform session and CSRF are required", Status: 401}
	}
	var encoded []byte
	var err error
	method := http.MethodGet
	if body != nil {
		method = http.MethodPost
		encoded, err = json.Marshal(body)
		if err != nil {
			return err
		}
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, bytes.NewReader(encoded))
	if err != nil {
		return err
	}
	if s.Delegation != "" {
		req.Header.Set("X-Agent-Delegation", s.Delegation)
	}
	req.Header.Set("Cookie", s.Cookie)
	req.Header.Set("X-CSRF-Token", s.CSRF)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return &tools.Error{Code: "PLATFORM_UNAVAILABLE", Message: "platform transport interrupted; inspect the operation before retrying a write", Unknown: write}
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, (512<<10)+1))
	if err != nil || len(raw) > 512<<10 {
		return &tools.Error{Code: "INVALID_PLATFORM_RESPONSE", Message: "platform response interrupted or exceeds 512 KiB", Unknown: write}
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		var envelope struct {
			Error struct {
				Code    string `json:"code"`
				Message string `json:"message"`
				Unknown bool   `json:"unknown"`
			} `json:"error"`
		}
		_ = json.Unmarshal(raw, &envelope)
		if envelope.Error.Code == "" {
			envelope.Error.Code = "PLATFORM_REJECTED"
			envelope.Error.Message = http.StatusText(resp.StatusCode)
		}
		return &tools.Error{Code: envelope.Error.Code, Message: envelope.Error.Message, Status: resp.StatusCode, Unknown: envelope.Error.Unknown || (write && resp.StatusCode >= 500)}
	}
	if err := json.Unmarshal(raw, out); err != nil {
		return &tools.Error{Code: "INVALID_PLATFORM_RESPONSE", Message: "platform returned invalid JSON", Unknown: write}
	}
	return nil
}

// Catalog rechecks the live platform session and current role before each model round.
func (c *Client) Catalog(ctx context.Context) (json.RawMessage, error) {
	var out json.RawMessage
	e := c.request(ctx, "/api/agent-tools/catalog", nil, &out, false)
	return out, e
}

// Package agenttools owns the platform-side approval and operation boundary.
// It does not access the independent Agent database.
package agenttools

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"
)

var ErrConflict = errors.New("operation key already used for different arguments")
var ErrNotFound = errors.New("operation not found")

type Operation struct {
	ID         string          `json:"operation_id"`
	Actor      string          `json:"-"`
	Tool       string          `json:"tool"`
	Digest     string          `json:"arguments_sha256"`
	Status     string          `json:"status"`
	HTTPStatus int             `json:"http_status,omitempty"`
	Response   json.RawMessage `json:"response,omitempty"`
	UpdatedAt  time.Time       `json:"updated_at"`
	Outcome    string          `json:"outcome"`
}

func Outcome(status int) string {
	if status == 0 {
		return "pending_or_unknown"
	}
	if status >= 500 {
		return "unknown"
	}
	if status >= 400 {
		return "rejected"
	}
	return "succeeded"
}

type Store interface {
	Begin(context.Context, Operation) (Operation, bool, error)
	Finish(context.Context, string, string, int, json.RawMessage) error
	Get(context.Context, string, string) (Operation, error)
}

// BusinessResolver recovers platform business results after response persistence
// failed. It must only look up existing results; never re-dispatch computation.
type BusinessResolver interface {
	Recover(context.Context, Operation) (json.RawMessage, bool, error)
}
type operationContextKey struct{}

func WithOperation(ctx context.Context, key string) context.Context {
	return context.WithValue(ctx, operationContextKey{}, key)
}
func OperationKey(ctx context.Context) string {
	key, _ := ctx.Value(operationContextKey{}).(string)
	return key
}

func Digest(raw json.RawMessage) (string, error) {
	var v any
	if err := json.Unmarshal(raw, &v); err != nil {
		return "", err
	}
	b, err := json.Marshal(v)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:]), nil
}

type Receipt struct {
	Actor     string `json:"actor"`
	Session   string `json:"session"`
	Tool      string `json:"tool"`
	Operation string `json:"operation"`
	Digest    string `json:"digest"`
	Expires   int64  `json:"expires"`
}

func Sign(key []byte, r Receipt) (string, error) {
	if len(key) < 32 {
		return "", fmt.Errorf("approval signing key needs at least 32 bytes")
	}
	raw, err := json.Marshal(r)
	if err != nil {
		return "", err
	}
	encoded := base64.RawURLEncoding.EncodeToString(raw)
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(encoded))
	return encoded + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil)), nil
}
func Verify(key []byte, token string, expected Receipt, now time.Time) error {
	if len(key) < 32 || len(token) > 4096 {
		return fmt.Errorf("approval unavailable or invalid")
	}
	parts := strings.Split(token, ".")
	if len(parts) != 2 {
		return fmt.Errorf("invalid approval")
	}
	sig, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return fmt.Errorf("invalid approval")
	}
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(parts[0]))
	if !hmac.Equal(sig, mac.Sum(nil)) {
		return fmt.Errorf("invalid approval")
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return fmt.Errorf("invalid approval")
	}
	var r Receipt
	if json.Unmarshal(raw, &r) != nil || r.Actor != expected.Actor || r.Session != expected.Session || r.Tool != expected.Tool || r.Operation != expected.Operation || r.Digest != expected.Digest || r.Expires <= now.Unix() || r.Expires > now.Add(16*time.Minute).Unix() {
		return fmt.Errorf("approval expired or does not match current session and action")
	}
	return nil
}

// MemoryStore is for tests and explicitly ephemeral development only.
type MemoryStore struct {
	mu   sync.Mutex
	rows map[string]Operation
}

func NewMemoryStore() *MemoryStore { return &MemoryStore{rows: map[string]Operation{}} }
func (s *MemoryStore) Begin(_ context.Context, op Operation) (Operation, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	key := op.Actor + ":" + op.ID
	if old, ok := s.rows[key]; ok {
		if old.Digest != op.Digest || old.Tool != op.Tool {
			return Operation{}, false, ErrConflict
		}
		return clone(old), false, nil
	}
	op.Status = "running"
	op.UpdatedAt = time.Now().UTC()
	s.rows[key] = clone(op)
	return clone(op), true, nil
}
func (s *MemoryStore) Finish(_ context.Context, actor, id string, status int, body json.RawMessage) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	key := actor + ":" + id
	op, ok := s.rows[key]
	if !ok {
		return ErrNotFound
	}
	if op.Status != "running" && !(op.Status == "completed" && op.HTTPStatus >= 500) {
		return ErrConflict
	}
	op.HTTPStatus = status
	op.Response = append(json.RawMessage(nil), body...)
	op.Status = "completed"
	op.UpdatedAt = time.Now().UTC()
	s.rows[key] = op
	return nil
}
func (s *MemoryStore) Get(_ context.Context, actor, id string) (Operation, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	op, ok := s.rows[actor+":"+id]
	if !ok {
		return Operation{}, ErrNotFound
	}
	return clone(op), nil
}
func clone(op Operation) Operation {
	op.Outcome = Outcome(op.HTTPStatus)
	op.Response = append(json.RawMessage(nil), op.Response...)
	return op
}

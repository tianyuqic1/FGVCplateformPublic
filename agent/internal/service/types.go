package service

import (
	"encoding/json"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/modelcontract"
	"time"
)

type Proposal struct {
	Call        modelcontract.Call `json:"call"`
	Arguments   json.RawMessage    `json:"arguments"`
	OperationID string             `json:"operation_id"`
	Approval    string             `json:"-"`
	Confirmed   bool               `json:"confirmed"`
}

// State is private persisted execution state; never returned verbatim over HTTP.
type State struct {
	CompletedOperations []string                `json:"completed_operations,omitempty"`
	Messages            []modelcontract.Message `json:"messages"`
	Round               int                     `json:"round"`
	ToolCount           int                     `json:"tool_count"`
	InputTokens         int                     `json:"input_tokens"`
	OutputTokens        int                     `json:"output_tokens"`
	ModelPending        bool                    `json:"model_pending"`
	Calls               []modelcontract.Call    `json:"calls,omitempty"`
	Proposal            *Proposal               `json:"proposal,omitempty"`
	Receipt             string                  `json:"receipt,omitempty"`
	WritePending        bool                    `json:"write_pending"`
	Output              string                  `json:"output"`
}
type Run struct {
	ID             string    `json:"id"`
	ConversationID string    `json:"conversation_id"`
	Owner          string    `json:"-"`
	Key            string    `json:"-"`
	Input          string    `json:"input"`
	Status         string    `json:"status"`
	Error          string    `json:"error,omitempty"`
	State          State     `json:"-"`
	Grant          string    `json:"-"`
	Epoch          int64     `json:"-"`
	Cancel         bool      `json:"cancel_requested"`
	CreatedAt      time.Time `json:"created_at"`
	UpdatedAt      time.Time `json:"updated_at"`
	Output         string    `json:"output"`
	Proposal       *Proposal `json:"proposal,omitempty"`
	Rounds         int       `json:"rounds"`
	Tools          int       `json:"tools"`
	InputTokens    int       `json:"input_tokens"`
	OutputTokens   int       `json:"output_tokens"`
}

func (r *Run) Public() {
	r.Output = r.State.Output
	r.Proposal = r.State.Proposal
	r.Rounds = r.State.Round
	r.Tools = r.State.ToolCount
	r.InputTokens = r.State.InputTokens
	r.OutputTokens = r.State.OutputTokens
}

type Conversation struct {
	ID        string    `json:"id"`
	Title     string    `json:"title"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}
type Event struct {
	ID        int64           `json:"id"`
	Kind      string          `json:"kind"`
	Data      json.RawMessage `json:"data"`
	CreatedAt time.Time       `json:"created_at"`
}
type Fault struct {
	Status  int
	Message string
}

func (e *Fault) Error() string { return e.Message }

type Watch struct {
	ID             string          `json:"id"`
	ConversationID string          `json:"conversation_id"`
	Owner          string          `json:"-"`
	TrainingRunID  string          `json:"training_run_id"`
	Status         string          `json:"status"`
	ObservedStatus string          `json:"observed_status"`
	Result         json.RawMessage `json:"result"`
	Error          string          `json:"error,omitempty"`
	Grant          string          `json:"-"`
	Epoch          int64           `json:"-"`
	CreatedAt      time.Time       `json:"created_at"`
	UpdatedAt      time.Time       `json:"updated_at"`
	ExpiresAt      time.Time       `json:"expires_at"`
}

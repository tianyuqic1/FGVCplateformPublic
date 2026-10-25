package tools

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/toolcontract"
)

type Definition = toolcontract.Definition
type Schema = toolcontract.Schema

func Definitions() []Definition { return toolcontract.Definitions() }

// Control is trusted execution metadata, never included in model arguments.
type Control struct {
	OperationID string
	Approval    string
}
type Evidence struct {
	Type       string    `json:"type"`
	ID         string    `json:"id"`
	ObservedAt time.Time `json:"observed_at"`
}
type Result struct {
	Tool        string          `json:"tool"`
	Data        json.RawMessage `json:"data"`
	ObservedAt  time.Time       `json:"observed_at"`
	Evidence    []Evidence      `json:"evidence"`
	OperationID string          `json:"operation_id,omitempty"`
}
type Transport interface {
	Invoke(context.Context, string, json.RawMessage, Control) (Result, error)
}
type Error struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Status  int    `json:"status,omitempty"`
	Unknown bool   `json:"unknown,omitempty"`
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }

type Registry struct {
	transport    Transport
	defs         map[string]Definition
	slots        chan struct{}
	readParallel int
}

func New(transport Transport, maxConcurrent, readParallel int) (*Registry, error) {
	if transport == nil || maxConcurrent < 1 || readParallel < 1 {
		return nil, fmt.Errorf("transport and positive concurrency limits are required")
	}
	r := &Registry{transport: transport, defs: map[string]Definition{}, slots: make(chan struct{}, maxConcurrent), readParallel: readParallel}
	for _, d := range Definitions() {
		r.defs[d.Name] = d
	}
	return r, nil
}
func (r *Registry) Definitions() []Definition { return Definitions() }
func (r *Registry) Validate(name string, args json.RawMessage) (Definition, error) {
	d, ok := r.defs[name]
	if !ok {
		return d, &Error{Code: "UNKNOWN_TOOL", Message: "tool is not registered"}
	}
	if err := d.InputSchema.Validate(args); err != nil {
		return d, &Error{Code: "INVALID_ARGUMENTS", Message: err.Error()}
	}
	return d, nil
}
func (r *Registry) Execute(ctx context.Context, name string, args json.RawMessage, control Control) (Result, error) {
	d, err := r.Validate(name, args)
	if err != nil {
		return Result{}, err
	}
	if !d.ReadOnly && (control.OperationID == "" || control.Approval == "") {
		return Result{}, &Error{Code: "CONFIRMATION_REQUIRED", Message: "write tools need an operation ID and platform-issued approval"}
	}
	ctx, cancel := context.WithTimeout(ctx, time.Duration(d.TimeoutSeconds)*time.Second)
	defer cancel()
	select {
	case r.slots <- struct{}{}:
		defer func() { <-r.slots }()
	case <-ctx.Done():
		return Result{}, ctx.Err()
	}
	return r.transport.Invoke(ctx, name, args, control)
}

type Call struct {
	ID        string
	Tool      string
	Arguments json.RawMessage
	Control   Control
}
type Outcome struct {
	CallID string  `json:"call_id"`
	Result *Result `json:"result,omitempty"`
	Error  *Error  `json:"error,omitempty"`
}

// ExecuteBatch validates the entire proposal before executing any call. Only
// independent read calls may run together. Results retain model call order.
func (r *Registry) ExecuteBatch(ctx context.Context, calls []Call) ([]Outcome, error) {
	if len(calls) < 1 || len(calls) > 24 {
		return nil, fmt.Errorf("a batch requires 1–24 calls")
	}
	seen := map[string]bool{}
	for _, call := range calls {
		if call.ID == "" || seen[call.ID] {
			return nil, fmt.Errorf("call IDs must be nonempty and unique")
		}
		seen[call.ID] = true
		d, err := r.Validate(call.Tool, call.Arguments)
		if err != nil {
			return nil, err
		}
		if !d.ReadOnly && len(calls) != 1 {
			return nil, &Error{Code: "MIXED_WRITE_BATCH", Message: "a write must be the sole call in its batch"}
		}
		if !d.ReadOnly && (call.Control.Approval == "" || call.Control.OperationID == "") {
			return nil, &Error{Code: "CONFIRMATION_REQUIRED", Message: "write approval missing"}
		}
	}
	results := make([]Outcome, len(calls))
	jobs := make(chan int)
	var wg sync.WaitGroup
	for n := 0; n < min(r.readParallel, len(calls)); n++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range jobs {
				c := calls[i]
				value, err := r.Execute(ctx, c.Tool, c.Arguments, c.Control)
				results[i].CallID = c.ID
				if err == nil {
					results[i].Result = &value
				} else {
					var e *Error
					if !errors.As(err, &e) {
						e = &Error{Code: "TOOL_FAILED", Message: err.Error()}
					}
					results[i].Error = e
				}
			}
		}()
	}
	for i := range calls {
		jobs <- i
	}
	close(jobs)
	wg.Wait()
	return results, nil
}

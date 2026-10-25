package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"sync/atomic"
	"testing"
	"time"
)

type recordingTransport struct {
	active atomic.Int32
	peak   atomic.Int32
	calls  atomic.Int32
}

func (t *recordingTransport) Invoke(ctx context.Context, name string, args json.RawMessage, _ Control) (Result, error) {
	t.calls.Add(1)
	n := t.active.Add(1)
	defer t.active.Add(-1)
	for old := t.peak.Load(); n > old; old = t.peak.Load() {
		if t.peak.CompareAndSwap(old, n) {
			break
		}
	}
	select {
	case <-time.After(3 * time.Millisecond):
	case <-ctx.Done():
		return Result{}, ctx.Err()
	}
	return Result{Tool: name, Data: append(json.RawMessage(nil), args...)}, nil
}
func TestBatchBoundsConcurrencyAndRetainsCallOrder(t *testing.T) {
	transport := &recordingTransport{}
	r, _ := New(transport, 8, 4)
	calls := []Call{}
	for i := 0; i < 20; i++ {
		calls = append(calls, Call{ID: fmt.Sprint(i), Tool: "training.get_run", Arguments: json.RawMessage(fmt.Sprintf(`{"run_id":"run-%d"}`, i))})
	}
	out, err := r.ExecuteBatch(context.Background(), calls)
	if err != nil {
		t.Fatal(err)
	}
	if transport.peak.Load() > 4 || transport.peak.Load() < 2 {
		t.Fatalf("parallelism = %d", transport.peak.Load())
	}
	for i, item := range out {
		if item.CallID != calls[i].ID || item.Result == nil || string(item.Result.Data) != string(calls[i].Arguments) {
			t.Fatalf("misordered result %d: %+v", i, item)
		}
	}
}
func TestInvalidBatchHasNoPartialSideEffects(t *testing.T) {
	for _, calls := range [][]Call{
		{{ID: "a", Tool: "datasets.list", Arguments: json.RawMessage(`{}`)}, {ID: "b", Tool: "training.cancel_run", Arguments: json.RawMessage(`{"run_id":"run"}`), Control: Control{"op", "approval"}}},
		{{ID: "a", Tool: "datasets.list", Arguments: json.RawMessage(`{}`)}, {ID: "b", Tool: "datasets.list", Arguments: json.RawMessage(`{"limit":1001}`)}},
		{{ID: "same", Tool: "datasets.list", Arguments: json.RawMessage(`{}`)}, {ID: "same", Tool: "datasets.list", Arguments: json.RawMessage(`{}`)}},
	} {
		transport := &recordingTransport{}
		r, _ := New(transport, 8, 4)
		if _, err := r.ExecuteBatch(context.Background(), calls); err == nil {
			t.Fatal("invalid batch accepted")
		}
		if transport.calls.Load() != 0 {
			t.Fatal("partial execution")
		}
	}
}
func TestAllToolsHaveClosedSchemasAndWriteGates(t *testing.T) {
	transport := &recordingTransport{}
	r, _ := New(transport, 8, 4)
	defs := r.Definitions()
	if len(defs) != 15 {
		t.Fatalf("tool count %d", len(defs))
	}
	for _, d := range defs {
		if d.InputSchema.AdditionalProperties {
			t.Fatal(d.Name)
		}
		if err := d.InputSchema.Validate(json.RawMessage(`{"approved":true,"actor":"admin"}`)); err == nil {
			t.Fatalf("%s accepts privileged input", d.Name)
		}
	}
	if _, err := r.Execute(context.Background(), "training.cancel_run", json.RawMessage(`{"run_id":"run"}`), Control{}); err == nil {
		t.Fatal("write without approval accepted")
	}
	if transport.calls.Load() != 0 {
		t.Fatal("unapproved write reached transport")
	}
}
func TestSchemaRejectsNestedInvalidTypesAndDuplicateModels(t *testing.T) {
	r, _ := New(&recordingTransport{}, 8, 4)
	for _, test := range []struct{ name, args string }{
		{"models.compare", `{"model_version_ids":["a","a"]}`},
		{"training.validate_plan", `{"dataset_version_id":"v","backbone_key":"b","head_config":{"epochs":1.5}}`},
		{"training.validate_plan", `{"dataset_version_id":"v","backbone_key":"b","head_config":{"augmentations":{"horizontal_flip":"true"}}}`},
		{"inference.run", `{"dataset_version_id":"v","model_version_id":"m","sample_id":"s","url":"http://attacker"}`},
		{"datasets.list", `{} {}`},
		{"datasets.list", `null`},
	} {
		if _, err := r.Validate(test.name, json.RawMessage(test.args)); err == nil {
			t.Fatalf("accepted %s", test.args)
		}
	}
}

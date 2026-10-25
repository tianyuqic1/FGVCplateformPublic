package httpapi

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// This crosses the module and HTTP boundary using the actual Agent CLI/client,
// rather than calling platform functions with hand-constructed contexts.
func TestAgentCLIInvokesAndConfirmsAgainstPlatform(t *testing.T) {
	if testing.Short() {
		t.Skip("cross-module CLI integration")
	}
	f := newToolFixture(t, nil)
	dir := t.TempDir()
	session := filepath.Join(dir, "session.json")
	raw, _ := json.Marshal(map[string]string{"cookie": "fv_session=" + toolCookie("owner"), "csrf": "csrf"})
	if err := os.WriteFile(session, raw, 0600); err != nil {
		t.Fatal(err)
	}
	run := func(mode string, input any, extra ...string) []byte {
		t.Helper()
		args := []string{"run", "./cmd/agent-tools", mode, "--platform", f.server.URL, "--session-file", session}
		args = append(args, extra...)
		cmd := exec.Command("go", args...)
		cmd.Dir = filepath.Join("..", "..", "..", "agent")
		encoded, _ := json.Marshal(input)
		cmd.Stdin = bytes.NewReader(encoded)
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("CLI %s: %v %s", mode, err, out)
		}
		return out
	}
	out := run("invoke", map[string]any{"tool": "datasets.list", "arguments": map[string]any{}})
	if !bytes.Contains(out, []byte(`"tool":"datasets.list"`)) {
		t.Fatal(string(out))
	}
	operationID := "57869f26-2778-45bd-9951-e4bc3f3a8b58"
	approved := run("approve", map[string]any{"tool": "training.submit_plan", "arguments": map[string]any{"dataset_version_id": "version-1", "backbone_key": "dinov3_vits16_lvd1689m"}}, "--confirm", "--operation-id", operationID)
	approval := filepath.Join(dir, "approval.json")
	if err := os.WriteFile(approval, approved, 0600); err != nil {
		t.Fatal(err)
	}
	var receipt struct {
		Arguments json.RawMessage `json:"arguments"`
	}
	_ = json.Unmarshal(approved, &receipt)
	out = run("invoke", map[string]any{"tool": "training.submit_plan", "arguments": receipt.Arguments}, "--approval-file", approval)
	if !bytes.Contains(out, []byte(`"training_run"`)) {
		t.Fatal(string(out))
	}
	if again := run("invoke", map[string]any{"tool": "training.submit_plan", "arguments": receipt.Arguments}, "--approval-file", approval); !bytes.Equal(out, again) {
		t.Fatalf("CLI repeat changed results: %s %s", out, again)
	}
}

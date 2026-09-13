package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func input() Input {
	return Input{Operation: "classify", Images: []string{"data:image/png;base64,YQ=="}, Classes: []Class{{ID: "1", Name: "one"}}}
}
func TestEndpointBoundary(t *testing.T) {
	for _, raw := range []string{"http://example.com/v1", "https://user:secret@example.com", "https://example.com?key=x", "file:///tmp/truth"} {
		if endpoint(raw, true) == nil {
			t.Fatal(raw)
		}
	}
	if endpoint("https://example.com/v1", false) == nil {
		t.Fatal("remote requires opt in")
	}
	if endpoint("http://127.0.0.1/v1", false) != nil {
		t.Fatal("local test endpoint rejected")
	}
}
func TestSingleHTTPAttemptAndPayload(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Header.Get("Authorization") != "Bearer secret" {
			t.Error("missing auth")
		}
		b, _ := io.ReadAll(r.Body)
		var body map[string]any
		if json.Unmarshal(b, &body) != nil {
			t.Error("json")
		}
		if strings.Contains(string(b), "truth.json") {
			t.Error("oracle leaked")
		}
		w.Header().Set("Retry-After", "2")
		w.WriteHeader(429)
	}))
	defer server.Close()
	out := step(context.Background(), input(), server.URL, "test", "secret", server.Client())
	if calls != 1 || out.Kind != "retryable" || out.RetryAfter != 2 {
		t.Fatalf("%+v calls %d", out, calls)
	}
}
func TestResponses(t *testing.T) {
	for _, tc := range []struct {
		status     int
		body, kind string
	}{
		{401, `secret in server error`, "fatal"},
		{200, `{"choices":[{"finish_reason":"stop","message":{"content":"{\"class_ids\":[\"1\"]}"}}]}`, "ok"},
		{200, `{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}`, "failed"},
		{200, `broken`, "failed"},
		{503, `down`, "retryable"},
	} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(tc.status); io.WriteString(w, tc.body) }))
		out := step(context.Background(), input(), server.URL, "test", "", server.Client())
		server.Close()
		if out.Kind != tc.kind || strings.Contains(out.Error, "secret") {
			t.Fatalf("%+v", out)
		}
	}
}
func TestTimeoutOutcomeUnknown(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { time.Sleep(30 * time.Millisecond) }))
	defer server.Close()
	client := server.Client()
	client.Timeout = time.Millisecond
	if step(context.Background(), input(), server.URL, "test", "", client).Kind != "unknown" {
		t.Fatal("ambiguous call must not auto retry")
	}
}
func TestPromptRejectsRemoteImagesAndToolInstructions(t *testing.T) {
	in := input()
	in.Images = []string{"https://example.com/a.png"}
	if _, e := prompt(in); e == nil {
		t.Fatal("remote image allowed")
	}
	in = input()
	in.Operation = "select_tool"
	in.Tools = []string{"none", "sr_x2"}
	p, e := prompt(in)
	if e != nil || !strings.Contains(p, "at most ONE") || !strings.Contains(p, "original remains authoritative") {
		t.Fatal(p, e)
	}
}

func TestOptimizedEvidencePolicyPreservesNonRetrievedAlternatives(t *testing.T) {
	in := input()
	in.EvidencePolicy = "balanced_v44"
	p, err := prompt(in)
	if err != nil || !strings.Contains(p, "FULL catalog independently") || !strings.Contains(p, "plausible non-retrieved alternatives") {
		t.Fatal(p, err)
	}
	in.EvidencePolicy = "untrusted-policy"
	if _, err := prompt(in); err == nil {
		t.Fatal("unknown policy accepted")
	}
}

package main

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestStreamingCompletionAndTruncation(t *testing.T) {
	for _, tc := range []struct{ name, body, kind string }{
		{"complete", "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"{}\"},\"finish_reason\":null}]}\n\ndata: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\ndata: {\"choices\":[],\"usage\":{\"total_tokens\":5}}\n\ndata: [DONE]\n\n", "ok"},
		{"missing_done", "data: {\"choices\":[{\"delta\":{\"content\":\"{}\"},\"finish_reason\":\"stop\"}]}\n\n", "unknown"},
		{"missing_stop", "data: [DONE]\n\n", "failed"},
		{"malformed", "data: not-json\n\n", "failed"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "text/event-stream")
				io.WriteString(w, tc.body)
			}))
			defer s.Close()
			out := step(context.Background(), input(), s.URL, "mock", "", s.Client())
			if out.Kind != tc.kind || !out.Dispatched {
				t.Fatalf("%+v", out)
			}
			if out.Kind == "ok" && (out.Raw != "{}" || !strings.Contains(string(out.Usage), "5")) {
				t.Fatalf("lost stream content/usage: %+v", out)
			}
		})
	}
}

func TestHeartbeatsDoNotExtendFirstContent(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		f := w.(http.Flusher)
		for {
			select {
			case <-r.Context().Done():
				return
			case <-time.After(5 * time.Millisecond):
				io.WriteString(w, ": keep-alive\n\n")
				f.Flush()
			}
		}
	}))
	defer s.Close()
	out := stepWithOptions(context.Background(), input(), s.URL, "mock", "", s.Client(), Options{Total: time.Second, First: 40 * time.Millisecond, Idle: time.Second, Stream: true})
	if out.Kind != "unknown" || out.FailurePhase != "first_content" {
		t.Fatalf("%+v", out)
	}
}

func TestIdleContentAndTotalTimeout(t *testing.T) {
	for _, total := range []bool{false, true} {
		s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "text/event-stream")
			f := w.(http.Flusher)
			io.WriteString(w, "data: {\"choices\":[{\"delta\":{\"content\":\"x\"}}]}\n\n")
			f.Flush()
			for {
				select {
				case <-r.Context().Done():
					return
				case <-time.After(5 * time.Millisecond):
					if total {
						io.WriteString(w, "data: {\"choices\":[{\"delta\":{\"content\":\"x\"}}]}\n\n")
					} else {
						io.WriteString(w, ": keep-alive\n\n")
					}
					f.Flush()
				}
			}
		}))
		opts := Options{Total: time.Second, First: time.Second, Idle: 40 * time.Millisecond, Stream: true}
		phase := "idle_content"
		if total {
			opts.Total = 60 * time.Millisecond
			opts.Idle = time.Second
			phase = "total_or_transport"
		}
		out := stepWithOptions(context.Background(), input(), s.URL, "mock", "", s.Client(), opts)
		s.Close()
		if out.Kind != "unknown" || out.FailurePhase != phase {
			t.Fatalf("%+v", out)
		}
	}
}

func TestConnectFailureIsRetryableAndNoRedirectRetry(t *testing.T) {
	l, e := net.Listen("tcp", "127.0.0.1:0")
	if e != nil {
		t.Fatal(e)
	}
	address := l.Addr().String()
	l.Close()
	c := newHTTPClient(100*time.Millisecond, time.Second, 1)
	defer c.CloseIdleConnections()
	out := step(context.Background(), input(), "http://"+address, "mock", "", c)
	if out.Kind != "retryable" || out.FailurePhase != "connect" {
		t.Fatalf("%+v", out)
	}
	var hits atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		w.Header().Set("Location", "/elsewhere")
		w.WriteHeader(307)
	}))
	defer s.Close()
	out = step(context.Background(), input(), s.URL, "mock", "", c)
	if hits.Load() != 1 || out.Kind != "fatal" {
		t.Fatalf("redirect followed: %+v %d", out, hits.Load())
	}
}

func TestHTTPConnectionReuse(t *testing.T) {
	var connections atomic.Int32
	s := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		fmt.Fprint(w, `{"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]}`)
	}))
	s.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			connections.Add(1)
		}
	}
	s.Start()
	defer s.Close()
	c := newHTTPClient(time.Second, time.Second, 2)
	defer c.CloseIdleConnections()
	for i := 0; i < 3; i++ {
		if step(context.Background(), input(), s.URL, "mock", "", c).Kind != "ok" {
			t.Fatal("call failed")
		}
	}
	if connections.Load() != 1 {
		t.Fatalf("connections=%d", connections.Load())
	}
}

func TestAdaptiveAdmissionAndCircuit(t *testing.T) {
	g := newGate(2, 4, 30*time.Second)
	for i := 0; i < 2; i++ {
		if ok, _ := g.admit(); !ok {
			t.Fatal("premature refusal")
		}
	}
	if ok, out := g.admit(); ok || out.Dispatched || out.Kind != "deferred" {
		t.Fatal(out)
	}
	g.done(Output{Kind: "retryable", RetryAfter: 600})
	g.done(Output{Kind: "ok"})
	if ok, out := g.admit(); ok || out.RetryAfter < 599 || out.Limit != 1 {
		t.Fatal(out)
	}
	g.until = time.Now().Add(-time.Second)
	if ok, _ := g.admit(); !ok {
		t.Fatal("probe refused")
	}
	if ok, _ := g.admit(); ok {
		t.Fatal("multiple probes")
	}
	g.done(Output{Kind: "ok"})
	for i := 0; i < 7; i++ {
		g.admit()
		g.done(Output{Kind: "ok"})
	}
	if g.limit != 2 {
		t.Fatal("no gradual recovery")
	}
	g.admit()
	g.done(Output{Kind: "fatal"})
	if ok, out := g.admit(); ok || out.Kind != "fatal" {
		t.Fatal(out)
	}
}

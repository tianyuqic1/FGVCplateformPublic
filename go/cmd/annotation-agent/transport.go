package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"
)

type Options struct {
	Total, First, Idle time.Duration
	Stream             bool
}

func newHTTPClient(connect, total time.Duration, maxConnections int) *http.Client {
	tr := http.DefaultTransport.(*http.Transport).Clone()
	tr.DialContext = (&net.Dialer{Timeout: connect, KeepAlive: 30 * time.Second}).DialContext
	tr.TLSHandshakeTimeout = connect
	tr.MaxIdleConns, tr.MaxIdleConnsPerHost, tr.MaxConnsPerHost = maxConnections, maxConnections, maxConnections
	return &http.Client{Transport: tr, Timeout: total, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirects disabled") }}
}

// Heartbeats never extend first-content or idle-content deadlines.
type progressWatch struct {
	mu      sync.Mutex
	timer   *time.Timer
	idle    time.Duration
	cancel  context.CancelFunc
	state   string
	expired string
	closed  bool
}

func newProgressWatch(cancel context.CancelFunc, first, idle time.Duration) *progressWatch {
	w := &progressWatch{idle: idle, cancel: cancel, state: "first_content"}
	w.timer = time.AfterFunc(first, func() {
		w.mu.Lock()
		defer w.mu.Unlock()
		if !w.closed {
			w.expired = w.state
			w.closed = true
			w.cancel()
		}
	})
	return w
}
func (w *progressWatch) touch() {
	w.mu.Lock()
	defer w.mu.Unlock()
	if !w.closed {
		w.state = "idle_content"
		w.timer.Reset(w.idle)
	}
}
func (w *progressWatch) close() { w.mu.Lock(); defer w.mu.Unlock(); w.closed = true; w.timer.Stop() }
func (w *progressWatch) phase() string {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.expired != "" {
		return w.expired
	}
	return "total_or_transport"
}

func readJSONBody(r io.Reader, w *progressWatch) ([]byte, error) {
	var out bytes.Buffer
	buf := make([]byte, 8192)
	limited := io.LimitReader(r, 2*1024*1024+1)
	for {
		n, e := limited.Read(buf)
		if n > 0 {
			out.Write(buf[:n])
			if len(bytes.TrimSpace(buf[:n])) > 0 {
				w.touch()
			}
		}
		if e == io.EOF {
			return out.Bytes(), nil
		}
		if e != nil {
			return nil, e
		}
	}
}

func readSSE(r io.Reader, w *progressWatch, out Output) Output {
	scanner := bufio.NewScanner(io.LimitReader(r, 2*1024*1024+1))
	scanner.Buffer(make([]byte, 8192), 2*1024*1024)
	var data []string
	var content strings.Builder
	finish := ""
	consume := func() (bool, bool) {
		if len(data) == 0 {
			return false, true
		}
		raw := strings.Join(data, "\n")
		data = nil
		if raw == "[DONE]" {
			return true, true
		}
		var env struct {
			Model   string          `json:"model"`
			Usage   json.RawMessage `json:"usage"`
			Error   json.RawMessage `json:"error"`
			Choices []struct {
				Index  int     `json:"index"`
				Finish *string `json:"finish_reason"`
				Delta  struct {
					Content string `json:"content"`
				} `json:"delta"`
			} `json:"choices"`
		}
		if json.Unmarshal([]byte(raw), &env) != nil || len(env.Choices) > 1 || len(env.Error) > 0 {
			return false, false
		}
		if env.Model != "" {
			out.Model = env.Model
		}
		if len(env.Usage) > 0 && string(env.Usage) != "null" {
			out.Usage = env.Usage
		}
		if len(env.Choices) == 1 {
			c := env.Choices[0]
			if c.Index != 0 {
				return false, false
			}
			if c.Delta.Content != "" {
				if finish != "" {
					return false, false
				}
				content.WriteString(c.Delta.Content)
				w.touch()
			}
			if c.Finish != nil {
				finish = *c.Finish
			}
		}
		return false, true
	}
	for scanner.Scan() {
		line := strings.TrimSuffix(scanner.Text(), "\r")
		if strings.HasPrefix(line, ":") {
			continue
		}
		if strings.HasPrefix(line, "data:") {
			data = append(data, strings.TrimPrefix(strings.TrimPrefix(line, "data:"), " "))
			continue
		}
		if line != "" {
			continue
		}
		done, valid := consume()
		if !valid {
			out.Kind, out.Error = "failed", "invalid streaming envelope"
			return out
		}
		if done {
			out.Raw = content.String()
			out.Kind = "ok"
			if finish != "stop" {
				out.Kind, out.Error = "failed", "incomplete model output"
			}
			return out
		}
	}
	// Even valid partial JSON is not a completed generation without [DONE].
	out.Kind, out.Error, out.FailurePhase = "unknown", "stream interrupted before completion; inspect before retry", w.phase()
	return out
}

// Admission is local and never consumes a paid retry budget. Provider retries
// stay exclusively in the durable coordinator, not in this transport.
type adaptiveGate struct {
	mu                                          sync.Mutex
	active, limit, maximum, failures, successes int
	until                                       time.Time
	cooldown                                    time.Duration
	fatal                                       bool
	halfOpen                                    bool
}

func newGate(initial, maximum int, cooldown time.Duration) *adaptiveGate {
	return &adaptiveGate{limit: initial, maximum: maximum, cooldown: cooldown}
}
func (g *adaptiveGate) admit() (bool, Output) {
	g.mu.Lock()
	defer g.mu.Unlock()
	out := Output{Kind: "deferred", RetryAfter: 0.1, Limit: g.limit, Error: "local concurrency queue"}
	if g.fatal {
		out.Kind, out.Error = "fatal", "provider configuration circuit blocked"
		return false, out
	}
	if time.Now().Before(g.until) {
		out.RetryAfter = time.Until(g.until).Seconds()
		out.Error = "provider circuit cooling down"
		return false, out
	}
	if g.active >= g.limit || (g.halfOpen && g.active > 0) {
		return false, out
	}
	g.active++
	return true, Output{}
}
func (g *adaptiveGate) done(out Output) int {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.active--
	if out.Kind == "fatal" {
		g.fatal = true
	}
	if out.Kind == "retryable" || out.Kind == "unknown" {
		g.limit = max(1, g.limit/2)
		g.failures++
		g.successes = 0
		delay := time.Duration(out.RetryAfter * float64(time.Second))
		if g.failures >= 3 || g.halfOpen {
			delay = max(delay, g.cooldown)
			g.halfOpen = true
		}
		if delay > 0 {
			g.until = maxTime(g.until, time.Now().Add(delay))
			g.halfOpen = true
		}
	} else if out.Kind == "ok" {
		// An old in-flight success must not clear a newer cooldown.
		if !time.Now().Before(g.until) {
			g.failures = 0
			g.halfOpen = false
			g.successes++
			if g.successes >= 8 {
				g.limit = min(g.maximum, g.limit+1)
				g.successes = 0
			}
		}
	}
	return g.limit
}
func maxTime(a, b time.Time) time.Time {
	if a.After(b) {
		return a
	}
	return b
}

type rpcInput struct {
	ID        string `json:"request_id"`
	Input     Input  `json:"input"`
	TimeoutMS int64  `json:"timeout_ms,omitempty"`
}
type rpcOutput struct {
	ID     string `json:"request_id"`
	Output Output `json:"output"`
}

func serveRPC(in io.Reader, out io.Writer, gate *adaptiveGate, invoke func(context.Context, Input) Output) {
	var mu sync.Mutex
	var tasks sync.WaitGroup
	encode := func(v any) { mu.Lock(); defer mu.Unlock(); json.NewEncoder(out).Encode(v) }
	encode(map[string]bool{"ready": true})
	scanner := bufio.NewScanner(in)
	scanner.Buffer(make([]byte, 8192), 24*1024*1024)
	for scanner.Scan() {
		var req rpcInput
		d := json.NewDecoder(bytes.NewReader(scanner.Bytes()))
		d.DisallowUnknownFields()
		if d.Decode(&req) != nil || d.Decode(new(any)) != io.EOF || req.ID == "" || len(req.ID) > 128 || req.TimeoutMS < 0 || req.TimeoutMS > 3600000 {
			encode(rpcOutput{ID: req.ID, Output: Output{Kind: "fatal", Error: "invalid RPC schema"}})
			continue
		}
		if ok, result := gate.admit(); !ok {
			encode(rpcOutput{ID: req.ID, Output: result})
			continue
		}
		tasks.Add(1)
		go func(r rpcInput) {
			defer tasks.Done()
			ctx := context.Background()
			if r.TimeoutMS > 0 {
				var cancel context.CancelFunc
				ctx, cancel = context.WithTimeout(ctx, time.Duration(r.TimeoutMS)*time.Millisecond)
				defer cancel()
			}
			result := invoke(ctx, r.Input)
			result.Limit = gate.done(result)
			encode(rpcOutput{ID: r.ID, Output: result})
		}(req)
	}
	tasks.Wait()
}

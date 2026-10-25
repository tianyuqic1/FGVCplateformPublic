package runner

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/delegation"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/modelcontract"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/adapters/platform"
	agenthttp "github.com/tianyuqic1/FGVCplateformPublic/agent/internal/httpapi"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/service"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/storage"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/tools"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func database(t *testing.T) *storage.Store {
	t.Helper()
	url := os.Getenv("FINEVISION_AGENT_TEST_DATABASE_URL")
	if url == "" {
		t.Skip("set FINEVISION_AGENT_TEST_DATABASE_URL to a disposable PostgreSQL database")
	}
	ctx := context.Background()
	admin, e := pgxpool.New(ctx, url)
	if e != nil {
		t.Fatal(e)
	}
	schema := "t_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, e = admin.Exec(ctx, `CREATE SCHEMA `+schema); e != nil {
		t.Fatal(e)
	}
	cfg, e := pgxpool.ParseConfig(url)
	if e != nil {
		t.Fatal(e)
	}
	cfg.ConnConfig.RuntimeParams["search_path"] = schema
	pool, e := pgxpool.NewWithConfig(ctx, cfg)
	if e != nil {
		t.Fatal(e)
	}
	s := &storage.Store{Pool: pool}
	if e = s.Migrate(ctx); e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { pool.Close(); _, _ = admin.Exec(ctx, `DROP SCHEMA `+schema+` CASCADE`); admin.Close() })
	return s
}

type modelFunc func(context.Context, modelcontract.Request) (modelcontract.Response, error)

func (f modelFunc) Chat(c context.Context, r modelcontract.Request) (modelcontract.Response, error) {
	return f(c, r)
}

var testKey = []byte("test-agent-signing-key-must-have-32-bytes")

func assertion(owner, purpose string) string {
	token, e := delegation.Sign(testKey, delegation.Claims{Actor: owner, Role: "business", Session: strings.Repeat("a", 64), Purpose: purpose, Expires: time.Now().Add(time.Minute).Unix()})
	if e != nil {
		panic(e)
	}
	return token
}
func submit(t *testing.T, s *storage.Store, owner, text string) service.Run {
	t.Helper()
	c, e := s.CreateConversation(context.Background(), owner, "")
	if e != nil {
		t.Fatal(e)
	}
	r, e := s.Submit(context.Background(), owner, c.ID, uuid.NewString(), text, assertion(owner, "tools"))
	if e != nil {
		t.Fatal(e)
	}
	return r
}
func waitStatus(t *testing.T, s *storage.Store, r service.Run, status string) service.Run {
	t.Helper()
	limit := time.Now().Add(8 * time.Second)
	for time.Now().Before(limit) {
		out, e := s.Get(context.Background(), r.Owner, r.ID)
		if e != nil {
			t.Fatal(e)
		}
		if out.Status == status {
			return out
		}
		if out.Status == "failed" || out.Status == "unknown" {
			t.Fatalf("unexpected status %s: %s", out.Status, out.Error)
		}
		time.Sleep(15 * time.Millisecond)
	}
	t.Fatalf("timed out waiting %s", status)
	return r
}
func setup(t *testing.T, s *storage.Store, m Model) (*Runner, *httptest.Server, *atomic.Int32) {
	writes := &atomic.Int32{}
	var mu sync.Mutex
	operations := map[string]json.RawMessage{}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, e := delegation.Verify(testKey, r.Header.Get("X-Agent-Delegation"), time.Now())
		if e != nil {
			w.WriteHeader(401)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if strings.HasSuffix(r.URL.Path, "catalog") {
			fmt.Fprint(w, `{"tools":[],"role":"business"}`)
			return
		}
		var in struct {
			Arguments json.RawMessage `json:"arguments"`
			Operation string          `json:"operation_id"`
			Approval  string          `json:"approval"`
		}
		_ = json.NewDecoder(r.Body).Decode(&in)
		if strings.Contains(r.URL.Path, "/approvals/") {
			if c.Purpose != "interactive" {
				w.WriteHeader(401)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"arguments": in.Arguments, "approval": "test-receipt", "operation_id": in.Operation})
			return
		}
		if strings.Contains(r.URL.Path, "/operations/") {
			id := r.URL.Path[strings.LastIndex(r.URL.Path, "/")+1:]
			mu.Lock()
			defer mu.Unlock()
			raw, ok := operations[id]
			if !ok {
				w.WriteHeader(404)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"outcome": "succeeded", "response": raw})
			return
		}
		name := r.URL.Path[strings.LastIndex(r.URL.Path, "/")+1:]
		var data any = map[string]any{"items": []any{}, "source": "live-test"}
		if name == "training.validate_plan" {
			data = map[string]any{"valid": true, "normalized_arguments": json.RawMessage(in.Arguments)}
		}
		raw, _ := json.Marshal(tools.Result{Tool: name, Data: func() json.RawMessage { b, _ := json.Marshal(data); return b }(), ObservedAt: time.Now()})
		if name == "training.cancel_run" {
			mu.Lock()
			defer mu.Unlock()
			if old, ok := operations[in.Operation]; ok {
				raw = old
			} else {
				writes.Add(1)
				operations[in.Operation] = raw
			}
		}
		_, _ = w.Write(raw)
	}))
	t.Cleanup(upstream.Close)
	client, e := platform.New(upstream.URL, nil)
	if e != nil {
		t.Fatal(e)
	}
	registry, e := tools.New(client, 16, 4)
	if e != nil {
		t.Fatal(e)
	}
	runner := &Runner{Store: s, Platform: client, Registry: registry, Model: m, Key: testKey}
	server := httptest.NewServer((&agenthttp.Server{Store: s, Platform: client, Key: testKey}).Handler())
	t.Cleanup(server.Close)
	return runner, server, writes
}
func call(t *testing.T, server *httptest.Server, owner, method, path string, body any) (int, []byte) {
	t.Helper()
	var b bytes.Buffer
	if body != nil {
		_ = json.NewEncoder(&b).Encode(body)
	}
	r, e := http.NewRequest(method, server.URL+path, &b)
	if e != nil {
		t.Fatal(e)
	}
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("X-Agent-Identity", assertion(owner, "interactive"))
	r.Header.Set("X-Agent-Grant", assertion(owner, "tools"))
	resp, e := http.DefaultClient.Do(r)
	if e != nil {
		t.Fatal(e)
	}
	defer resp.Body.Close()
	var raw bytes.Buffer
	_, _ = raw.ReadFrom(resp.Body)
	return resp.StatusCode, raw.Bytes()
}
func TestConversationToolLoopConfirmationAndIsolation(t *testing.T) {
	s := database(t)
	var calls atomic.Int32
	model := modelFunc(func(_ context.Context, in modelcontract.Request) (modelcontract.Response, error) {
		calls.Add(1)
		last := in.Messages[len(in.Messages)-1]
		m := modelcontract.Message{Role: "assistant", Content: "已查询平台，任务已取消。"}
		if last.Role == "user" {
			m.Content = ""
			m.ToolCalls = []modelcontract.Call{{ID: "cancel", Type: "function", Function: modelcontract.Function{Name: "training__cancel_run", Arguments: `{"run_id":"11111111-1111-4111-8111-111111111111"}`}}}
		}
		return modelcontract.Response{Message: m}, nil
	})
	runner, server, writes := setup(t, s, model)
	ctx, cancel := context.WithCancel(context.Background())
	wg := runner.Start(ctx, 4)
	defer func() { cancel(); wg.Wait() }()
	owner := uuid.NewString()
	r := submit(t, s, owner, "请取消这个任务")
	r = waitStatus(t, s, r, "waiting_confirmation")
	if writes.Load() != 0 {
		t.Fatal("write executed without confirmation")
	}
	if calls.Load() != 1 {
		t.Fatal("unexpected model count")
	}
	code, raw := call(t, server, uuid.NewString(), "GET", "/v1/runs/"+r.ID, nil)
	if code != 404 {
		t.Fatalf("cross-user read %d %s", code, raw)
	}
	code, raw = call(t, server, owner, "POST", "/v1/runs/"+r.ID+"/confirm", map[string]string{"operation_id": uuid.NewString()})
	if code != 409 {
		t.Fatalf("stale confirmation %d %s", code, raw)
	}
	var wgConfirm sync.WaitGroup
	for i := 0; i < 12; i++ {
		wgConfirm.Add(1)
		go func() {
			defer wgConfirm.Done()
			code, raw := call(t, server, owner, "POST", "/v1/runs/"+r.ID+"/confirm", map[string]string{"operation_id": r.State.Proposal.OperationID})
			if code != 200 {
				t.Errorf("confirm %d %s", code, raw)
			}
		}()
	}
	wgConfirm.Wait()
	r = waitStatus(t, s, r, "succeeded")
	if writes.Load() != 1 || calls.Load() != 2 {
		t.Fatalf("writes %d models %d", writes.Load(), calls.Load())
	}
	code, raw = call(t, server, owner, "GET", "/v1/runs/"+r.ID, nil)
	if code != 200 || bytes.Contains(raw, []byte("test-receipt")) || bytes.Contains(raw, []byte("grant_token")) {
		t.Fatalf("secret leaked: %d %s", code, raw)
	}
	events, e := s.Events(context.Background(), owner, r.ID, 0)
	if e != nil || len(events) < 8 {
		t.Fatalf("events %d %v", len(events), e)
	}
	tail, e := s.Events(context.Background(), owner, r.ID, events[len(events)-2].ID)
	if e != nil || len(tail) != 1 {
		t.Fatalf("cursor replay %v %d", e, len(tail))
	}
}
func TestReadLoopAndCancellation(t *testing.T) {
	s := database(t)
	m := modelFunc(func(ctx context.Context, in modelcontract.Request) (modelcontract.Response, error) {
		if strings.Contains(in.Messages[len(in.Messages)-1].Content, "block") {
			<-ctx.Done()
			return modelcontract.Response{}, ctx.Err()
		}
		m := modelcontract.Message{Role: "assistant", Content: "平台查询完成"}
		if in.Messages[len(in.Messages)-1].Role == "user" {
			m.Content = ""
			for _, name := range []string{"datasets__list", "workers__get_capacity"} {
				m.ToolCalls = append(m.ToolCalls, modelcontract.Call{ID: name, Type: "function", Function: modelcontract.Function{Name: name, Arguments: `{}`}})
			}
		}
		return modelcontract.Response{Message: m, InputTokens: 10, OutputTokens: 20}, nil
	})
	runner, _, _ := setup(t, s, m)
	ctx, cancel := context.WithCancel(context.Background())
	wg := runner.Start(ctx, 4)
	defer func() { cancel(); wg.Wait() }()
	owner := uuid.NewString()
	r := submit(t, s, owner, "查询数据和资源")
	r = waitStatus(t, s, r, "succeeded")
	if r.State.ToolCount != 2 || r.State.Round != 2 {
		t.Fatalf("bad counters %+v", r.State)
	}
	r = submit(t, s, owner, "block")
	waitStatus(t, s, r, "running")
	if e := s.Cancel(context.Background(), owner, r.ID); e != nil {
		t.Fatal(e)
	}
	waitStatus(t, s, r, "cancelled")
}
func TestQueueIdempotencyAdmissionAndLeaseFencing(t *testing.T) {
	s := database(t)
	ctx := context.Background()
	owner := uuid.NewString()
	c, e := s.CreateConversation(ctx, owner, "")
	if e != nil {
		t.Fatal(e)
	}
	key := uuid.NewString()
	var wg sync.WaitGroup
	ids := sync.Map{}
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r, e := s.Submit(ctx, owner, c.ID, key, "同一个问题", assertion(owner, "tools"))
			if e != nil {
				t.Error(e)
				return
			}
			ids.Store(r.ID, true)
		}()
	}
	wg.Wait()
	count := 0
	ids.Range(func(_, _ any) bool { count++; return true })
	if count != 1 {
		t.Fatalf("duplicate runs: %d", count)
	}
	if _, e = s.Submit(ctx, owner, c.ID, uuid.NewString(), "不同问题", assertion(owner, "tools")); e == nil {
		t.Fatal("same conversation accepted concurrent run")
	}
	for i := 0; i < 7; i++ {
		submit(t, s, owner, "other conversation")
	}
	c2, _ := s.CreateConversation(ctx, owner, "")
	if _, e = s.Submit(ctx, owner, c2.ID, uuid.NewString(), "over quota", ""); e == nil {
		t.Fatal("user quota ignored")
	}
	for i := 0; i < 4; i++ {
		other := uuid.NewString()
		for j := 0; j < 2; j++ {
			submit(t, s, other, "fair admission")
		}
	}
	claimed := []service.Run{}
	for i := 0; i < 8; i++ {
		r, e := s.Claim(ctx)
		if e != nil || r.ID == "" {
			t.Fatalf("claim %v %+v", e, r)
		}
		claimed = append(claimed, r)
	}
	if r, e := s.Claim(ctx); e != nil || r.ID != "" {
		t.Fatalf("global cap ignored %+v %v", r, e)
	}
	byUser := map[string]int{}
	for _, r := range claimed {
		byUser[r.Owner]++
		if byUser[r.Owner] > 2 {
			t.Fatal("per-user cap ignored")
		}
	}
	stale := claimed[0]
	stale.State.ModelPending = true
	if e = s.Save(ctx, &stale, "running", "model_dispatched", map[string]string{}); e != nil {
		t.Fatal(e)
	}
	_, _ = s.Pool.Exec(ctx, `UPDATE runs SET lease_until=now()-interval '1 second' WHERE id=$1`, stale.ID)
	if _, e = s.Claim(ctx); e != nil {
		t.Fatal(e)
	}
	out, e := s.Get(ctx, stale.Owner, stale.ID)
	if e != nil || out.Status != "unknown" {
		t.Fatalf("ambiguous model was requeued %s %v", out.Status, e)
	}
	if e = s.Save(ctx, &stale, "succeeded", "answer", map[string]string{}); e == nil {
		t.Fatal("stale worker wrote checkpoint")
	}
}
func TestWriteRecoveryDoesNotRedispatch(t *testing.T) {
	s := database(t)
	var modelCalls atomic.Int32
	m := modelFunc(func(context.Context, modelcontract.Request) (modelcontract.Response, error) {
		modelCalls.Add(1)
		return modelcontract.Response{Message: modelcontract.Message{Role: "assistant", Content: "恢复完成"}}, nil
	})
	runner, _, writes := setup(t, s, m)
	owner := uuid.NewString()
	queued := submit(t, s, owner, "recover")
	r, e := s.Claim(context.Background())
	if e != nil || r.ID != queued.ID {
		t.Fatal(e)
	}
	c := modelcontract.Call{ID: "oldcall", Type: "function", Function: modelcontract.Function{Name: "training.cancel_run", Arguments: `{"run_id":"11111111-1111-4111-8111-111111111111"}`}}
	op := uuid.NewString()
	r.State.Proposal = &service.Proposal{Call: c, Arguments: json.RawMessage(c.Function.Arguments), OperationID: op, Confirmed: true}
	r.State.Receipt = "test-receipt"
	r.State.WritePending = true
	r.State.Messages = append(r.State.Messages, modelcontract.Message{Role: "assistant", ToolCalls: []modelcontract.Call{c}})
	_, e = runner.Registry.Execute(platform.WithSession(context.Background(), platform.Session{Delegation: assertion(owner, "tools")}), c.Function.Name, json.RawMessage(c.Function.Arguments), tools.Control{OperationID: op, Approval: "test-receipt"})
	if e != nil {
		t.Fatal(e)
	}
	if e = s.Save(context.Background(), &r, "running", "write_dispatched", map[string]string{}); e != nil {
		t.Fatal(e)
	}
	_, _ = s.Pool.Exec(context.Background(), `UPDATE runs SET lease_until=now()-interval '1 second' WHERE id=$1`, r.ID)
	ctx, cancel := context.WithCancel(context.Background())
	wg := runner.Start(ctx, 2)
	defer func() { cancel(); wg.Wait() }()
	out := waitStatus(t, s, r, "succeeded")
	if writes.Load() != 1 || modelCalls.Load() != 1 {
		t.Fatalf("redispatch detected writes %d models %d", writes.Load(), modelCalls.Load())
	}
	if out.State.WritePending {
		t.Fatal("pending marker not cleared")
	}
}

func TestWatchOwnershipLeaseAndCompletion(t *testing.T) {
	s := database(t)
	ctx := context.Background()
	owner := uuid.NewString()
	conv, e := s.CreateConversation(ctx, owner, "")
	if e != nil {
		t.Fatal(e)
	}
	runID := uuid.NewString()
	if _, e = s.CreateWatch(ctx, uuid.NewString(), conv.ID, runID, ""); e == nil {
		t.Fatal("cross-user watch accepted")
	}
	first, e := s.CreateWatch(ctx, owner, conv.ID, runID, assertion(owner, "tools"))
	if e != nil {
		t.Fatal(e)
	}
	same, e := s.CreateWatch(ctx, owner, conv.ID, runID, assertion(owner, "tools"))
	if e != nil || same.ID != first.ID {
		t.Fatalf("watch duplicate %v", e)
	}
	claimed, e := s.ClaimWatch(ctx)
	if e != nil || claimed.ID != first.ID {
		t.Fatalf("claim watch %v %+v", e, claimed)
	}
	if again, e := s.ClaimWatch(ctx); e != nil || again.ID != "" {
		t.Fatalf("concurrent watch claim %+v %v", again, e)
	}
	if e = s.ChangeWatch(ctx, owner, first.ID, "cancelled", ""); e != nil {
		t.Fatal(e)
	}
	if e = s.SaveWatch(ctx, claimed, "completed", "succeeded", "", json.RawMessage(`{}`)); e == nil {
		t.Fatal("stale poll wrote after cancellation")
	}
	second, e := s.CreateWatch(ctx, owner, conv.ID, runID, assertion(owner, "tools"))
	if e != nil {
		t.Fatal(e)
	}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(tools.Result{Tool: "training.get_run", Data: json.RawMessage(`{"training_run":{"status":"succeeded"}}`), ObservedAt: time.Now()})
	}))
	defer upstream.Close()
	client, _ := platform.New(upstream.URL, nil)
	registry, _ := tools.New(client, 4, 4)
	runner := &Runner{Store: s, Registry: registry, Key: testKey}
	watchCtx, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	go func() { defer close(done); runner.WatchLoop(watchCtx) }()
	defer func() { cancel(); <-done }()
	limit := time.Now().Add(3 * time.Second)
	for time.Now().Before(limit) {
		items, e := s.Watches(ctx, owner, conv.ID)
		if e != nil {
			t.Fatal(e)
		}
		for _, w := range items {
			if w.ID == second.ID && w.Status == "completed" && w.ObservedStatus == "succeeded" {
				return
			}
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("watch did not complete")
}

func TestTwentyUsersFortyRunsRespectLimitsAcrossTwoRunners(t *testing.T) {
	s := database(t)
	var mu sync.Mutex
	active := 0
	peak := 0
	perUser := map[string]int{}
	var modelCalls atomic.Int32
	m := modelFunc(func(ctx context.Context, in modelcontract.Request) (modelcontract.Response, error) {
		owner := strings.TrimPrefix(in.Messages[len(in.Messages)-1].Content, "owner=")
		mu.Lock()
		active++
		perUser[owner]++
		if active > peak {
			peak = active
		}
		if active > 8 || perUser[owner] > 2 {
			t.Errorf("admission violated active=%d user=%d", active, perUser[owner])
		}
		mu.Unlock()
		modelCalls.Add(1)
		select {
		case <-time.After(35 * time.Millisecond):
		case <-ctx.Done():
		}
		mu.Lock()
		active--
		perUser[owner]--
		mu.Unlock()
		return modelcontract.Response{Message: modelcontract.Message{Role: "assistant", Content: "完成"}}, nil
	})
	runner, _, _ := setup(t, s, m)
	for i := 0; i < 20; i++ {
		owner := uuid.NewString()
		for j := 0; j < 2; j++ {
			submit(t, s, owner, "owner="+owner)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	first := runner.Start(ctx, 8)
	second := runner.Start(ctx, 8)
	defer func() { cancel(); first.Wait(); second.Wait() }()
	limit := time.Now().Add(12 * time.Second)
	for time.Now().Before(limit) {
		var count int
		if e := s.Pool.QueryRow(context.Background(), `SELECT count(*) FROM runs WHERE status='succeeded'`).Scan(&count); e != nil {
			t.Fatal(e)
		}
		if count == 40 {
			mu.Lock()
			observed := peak
			mu.Unlock()
			if observed < 2 || observed > 8 || modelCalls.Load() != 40 {
				t.Fatalf("peak %d calls %d", observed, modelCalls.Load())
			}
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("load did not drain")
}

func TestCancelledLeaseRecoversWithoutLeavingAnUnclaimableQueueEntry(t *testing.T) {
	s := database(t)
	owner := uuid.NewString()
	r := submit(t, s, owner, "cancel before restart")
	claimed, e := s.Claim(context.Background())
	if e != nil || claimed.ID != r.ID {
		t.Fatal(e)
	}
	if e = s.Cancel(context.Background(), owner, r.ID); e != nil {
		t.Fatal(e)
	}
	_, e = s.Pool.Exec(context.Background(), `UPDATE runs SET lease_until=now()-interval '1 second' WHERE id=$1`, r.ID)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.Claim(context.Background()); e != nil {
		t.Fatal(e)
	}
	out, e := s.Get(context.Background(), owner, r.ID)
	if e != nil || out.Status != "cancelled" {
		t.Fatalf("stuck cancelled run: %s %v", out.Status, e)
	}
}

func TestSSEReconnectReplaysOnlyEventsAfterCursor(t *testing.T) {
	s := database(t)
	m := modelFunc(func(context.Context, modelcontract.Request) (modelcontract.Response, error) {
		return modelcontract.Response{}, fmt.Errorf("must not run")
	})
	_, server, _ := setup(t, s, m)
	owner := uuid.NewString()
	r := submit(t, s, owner, "event reconnect")
	events, e := s.Events(context.Background(), owner, r.ID, 0)
	if e != nil || len(events) != 1 {
		t.Fatal(e)
	}
	if e = s.Cancel(context.Background(), owner, r.ID); e != nil {
		t.Fatal(e)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	req, e := http.NewRequestWithContext(ctx, "GET", server.URL+"/v1/runs/"+r.ID+"/events", nil)
	if e != nil {
		t.Fatal(e)
	}
	req.Header.Set("X-Agent-Identity", assertion(owner, "interactive"))
	req.Header.Set("X-Agent-Grant", assertion(owner, "tools"))
	req.Header.Set("Last-Event-ID", fmt.Sprint(events[0].ID))
	resp, e := http.DefaultClient.Do(req)
	if e != nil {
		t.Fatal(e)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatal(resp.StatusCode)
	}
	scanner := bufio.NewScanner(resp.Body)
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "data: ") {
			var ev service.Event
			if e = json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &ev); e != nil {
				t.Fatal(e)
			}
			if ev.Kind != "cancellation" || ev.ID <= events[0].ID {
				t.Fatalf("incorrect replay %+v", ev)
			}
			cancel()
			return
		}
	}
	t.Fatalf("no replay event: %v", scanner.Err())
}

func TestRoleDowngradePausesBeforeModelAndCanResumeAfterRestoration(t *testing.T) {
	s := database(t)
	var calls atomic.Int32
	var restored atomic.Bool
	m := modelFunc(func(context.Context, modelcontract.Request) (modelcontract.Response, error) {
		calls.Add(1)
		return modelcontract.Response{Message: modelcontract.Message{Role: "assistant", Content: "权限恢复后完成"}}, nil
	})
	runner, _, _ := setup(t, s, m)
	authorization := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		role := "annotator"
		if restored.Load() {
			role = "business"
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"role": role, "tools": []any{}})
	}))
	defer authorization.Close()
	runner.Platform, _ = platform.New(authorization.URL, nil)
	owner := uuid.NewString()
	r := submit(t, s, owner, "permission check")
	ctx, cancel := context.WithCancel(context.Background())
	wg := runner.Start(ctx, 2)
	defer func() { cancel(); wg.Wait() }()
	waitStatus(t, s, r, "waiting_auth")
	if calls.Load() != 0 {
		t.Fatal("revoked role dispatched model")
	}
	restored.Store(true)
	if e := s.Resume(context.Background(), owner, r.ID, assertion(owner, "tools")); e != nil {
		t.Fatal(e)
	}
	waitStatus(t, s, r, "succeeded")
	if calls.Load() != 1 {
		t.Fatal("resume dispatched more than once")
	}
}

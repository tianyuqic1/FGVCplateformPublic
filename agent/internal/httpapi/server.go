package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/delegation"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/adapters/platform"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/service"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/storage"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/tools"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

type Server struct {
	Store    *storage.Store
	Platform *platform.Client
	Key      []byte
}
type identityKey struct{}
type identity struct {
	Claims           delegation.Claims
	Assertion, Grant string
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		ctx, c := context.WithTimeout(r.Context(), 2*time.Second)
		defer c()
		if s.Store.Pool.Ping(ctx) != nil {
			respond(w, 503, map[string]string{"status": "unavailable"})
			return
		}
		respond(w, 200, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("POST /v1/watches", s.createWatch)
	mux.HandleFunc("GET /v1/watches", s.listWatches)
	mux.HandleFunc("POST /v1/watches/{id}/{action}", s.changeWatch)
	mux.HandleFunc("GET /v1/conversations", s.list)
	mux.HandleFunc("POST /v1/conversations", s.create)
	mux.HandleFunc("GET /v1/conversations/{id}", s.conversation)
	mux.HandleFunc("POST /v1/runs", s.submit)
	mux.HandleFunc("GET /v1/runs/{id}", s.get)
	mux.HandleFunc("POST /v1/runs/{id}/cancel", s.cancel)
	mux.HandleFunc("POST /v1/runs/{id}/confirm", s.confirm)
	mux.HandleFunc("POST /v1/runs/{id}/resume", s.resume)
	mux.HandleFunc("GET /v1/runs/{id}/events", s.events)
	mux.HandleFunc("GET /v1/runs/{id}/operation", s.operation)
	mux.HandleFunc("GET /v1/tools", func(w http.ResponseWriter, r *http.Request) {
		respond(w, 200, map[string]any{"items": tools.Definitions()})
	})
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		if r.URL.Path == "/health" {
			mux.ServeHTTP(w, r)
			return
		}
		c, e := delegation.Verify(s.Key, r.Header.Get("X-Agent-Identity"), time.Now())
		if e != nil || c.Purpose != "interactive" {
			respond(w, 401, map[string]string{"detail": "需要经过平台验证的身份"})
			return
		}
		grant, e := delegation.Verify(s.Key, r.Header.Get("X-Agent-Grant"), time.Now())
		if e != nil || grant.Purpose != "tools" || grant.Actor != c.Actor || grant.Session != c.Session || (c.Role != "admin" && c.Role != "business") {
			respond(w, 403, map[string]string{"detail": "Agent 委托无效"})
			return
		}
		if _, e = uuid.Parse(c.Actor); e != nil {
			respond(w, 401, map[string]string{"detail": "身份无效"})
			return
		}
		for _, part := range strings.Split(r.URL.Path, "/") {
			if len(part) == 36 {
				if _, e := uuid.Parse(part); e != nil {
					respond(w, 422, map[string]string{"detail": "资源编号无效"})
					return
				}
			}
		}
		mux.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), identityKey{}, identity{c, r.Header.Get("X-Agent-Identity"), r.Header.Get("X-Agent-Grant")})))
	})
}
func who(r *http.Request) identity { return r.Context().Value(identityKey{}).(identity) }
func respond(w http.ResponseWriter, status int, out any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(out)
}
func report(w http.ResponseWriter, e error) {
	var f *service.Fault
	if errors.As(e, &f) {
		respond(w, f.Status, map[string]string{"detail": f.Message})
		return
	}
	var t *tools.Error
	if errors.As(e, &t) {
		status := t.Status
		if status < 400 {
			status = 503
		}
		respond(w, status, map[string]any{"error": t})
		return
	}
	respond(w, 503, map[string]string{"detail": "Agent 存储暂不可用"})
}
func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	if !strings.HasPrefix(r.Header.Get("Content-Type"), "application/json") {
		respond(w, 415, map[string]string{"detail": "需要 JSON 请求体"})
		return false
	}
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 24<<10))
	d.DisallowUnknownFields()
	if d.Decode(v) != nil || d.Decode(new(any)) != io.EOF {
		respond(w, 422, map[string]string{"detail": "请求格式无效"})
		return false
	}
	return true
}
func (s *Server) list(w http.ResponseWriter, r *http.Request) {
	out, e := s.Store.Conversations(r.Context(), who(r).Claims.Actor)
	if e != nil {
		report(w, e)
		return
	}
	respond(w, 200, map[string]any{"items": out})
}
func (s *Server) create(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Title string `json:"title"`
	}
	if !decode(w, r, &in) {
		return
	}
	out, e := s.Store.CreateConversation(r.Context(), who(r).Claims.Actor, in.Title)
	if e != nil {
		report(w, e)
		return
	}
	respond(w, 201, out)
}
func (s *Server) conversation(w http.ResponseWriter, r *http.Request) {
	if !validID(w, r.PathValue("id")) {
		return
	}
	out, e := s.Store.Runs(r.Context(), who(r).Claims.Actor, r.PathValue("id"))
	if e != nil {
		report(w, e)
		return
	}
	respond(w, 200, map[string]any{"items": out})
}
func validID(w http.ResponseWriter, id string) bool {
	if _, e := uuid.Parse(id); e != nil {
		respond(w, 422, map[string]string{"detail": "资源编号无效"})
		return false
	}
	return true
}
func (s *Server) submit(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Conversation string `json:"conversation_id"`
		Key          string `json:"request_key"`
		Input        string `json:"input"`
	}
	if !decode(w, r, &in) || !validID(w, in.Conversation) {
		return
	}
	u := who(r)
	out, e := s.Store.Submit(r.Context(), u.Claims.Actor, in.Conversation, in.Key, in.Input, u.Grant)
	if e != nil {
		report(w, e)
		return
	}
	respond(w, 202, out)
}
func (s *Server) get(w http.ResponseWriter, r *http.Request) {
	if !validID(w, r.PathValue("id")) {
		return
	}
	out, e := s.Store.Get(r.Context(), who(r).Claims.Actor, r.PathValue("id"))
	if e != nil {
		report(w, e)
		return
	}
	respond(w, 200, out)
}
func (s *Server) cancel(w http.ResponseWriter, r *http.Request) {
	if !validID(w, r.PathValue("id")) {
		return
	}
	if e := s.Store.Cancel(r.Context(), who(r).Claims.Actor, r.PathValue("id")); e != nil {
		report(w, e)
		return
	}
	respond(w, 200, map[string]string{"status": "cancellation_requested"})
}
func (s *Server) confirm(w http.ResponseWriter, r *http.Request) {
	if !validID(w, r.PathValue("id")) {
		return
	}
	var in struct {
		Operation string `json:"operation_id"`
	}
	if !decode(w, r, &in) {
		return
	}
	u := who(r)
	run, e := s.Store.Get(r.Context(), u.Claims.Actor, r.PathValue("id"))
	if e != nil {
		report(w, e)
		return
	}
	for _, completed := range run.State.CompletedOperations {
		if completed == in.Operation {
			respond(w, 200, map[string]string{"status": run.Status})
			return
		}
	}
	p := run.State.Proposal
	if p == nil || p.OperationID != in.Operation {
		respond(w, 409, map[string]string{"detail": "方案已变化，请刷新后确认"})
		return
	}
	if p.Confirmed {
		respond(w, 200, map[string]string{"status": run.Status})
		return
	}
	if run.Status != "waiting_confirmation" {
		respond(w, 409, map[string]string{"detail": "任务不再等待确认"})
		return
	}
	ctx := platform.WithSession(r.Context(), platform.Session{Delegation: u.Assertion})
	approved, e := s.Platform.Approve(ctx, p.Call.Function.Name, p.Arguments, p.OperationID)
	if e != nil {
		report(w, e)
		return
	}
	if e = s.Store.Confirm(r.Context(), u.Claims.Actor, run.ID, p.OperationID, approved.Approval, u.Grant, approved.Arguments); e != nil {
		report(w, e)
		return
	}
	respond(w, 200, map[string]string{"status": "queued"})
}
func (s *Server) resume(w http.ResponseWriter, r *http.Request) {
	if !validID(w, r.PathValue("id")) {
		return
	}
	u := who(r)
	if e := s.Store.Resume(r.Context(), u.Claims.Actor, r.PathValue("id"), u.Grant); e != nil {
		report(w, e)
		return
	}
	respond(w, 200, map[string]string{"status": "queued"})
}
func (s *Server) operation(w http.ResponseWriter, r *http.Request) {
	if !validID(w, r.PathValue("id")) {
		return
	}
	u := who(r)
	run, e := s.Store.Get(r.Context(), u.Claims.Actor, r.PathValue("id"))
	if e != nil {
		report(w, e)
		return
	}
	if run.State.Proposal == nil {
		respond(w, 404, map[string]string{"detail": "没有待核对的平台操作"})
		return
	}
	ctx := platform.WithSession(r.Context(), platform.Session{Delegation: u.Grant})
	out, e := s.Platform.Operation(ctx, run.State.Proposal.OperationID)
	if e != nil {
		report(w, e)
		return
	}
	respond(w, 200, out)
}
func (s *Server) events(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !validID(w, id) {
		return
	}
	u := who(r)
	if _, e := s.Store.Get(r.Context(), u.Claims.Actor, id); e != nil {
		report(w, e)
		return
	}
	cursor := r.Header.Get("Last-Event-ID")
	if cursor == "" {
		cursor = r.URL.Query().Get("after")
	}
	after := int64(0)
	if cursor != "" {
		var e error
		after, e = strconv.ParseInt(cursor, 10, 64)
		if e != nil || after < 0 {
			respond(w, 422, map[string]string{"detail": "事件游标无效"})
			return
		}
	}
	if r.URL.Query().Get("format") == "json" {
		out, e := s.Store.Events(r.Context(), u.Claims.Actor, id, after)
		if e != nil {
			report(w, e)
			return
		}
		respond(w, 200, map[string]any{"items": out})
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(200)
	flush := http.NewResponseController(w)
	_ = flush.Flush()
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	deadline := time.NewTimer(50 * time.Second)
	defer deadline.Stop()
	for {
		items, e := s.Store.Events(r.Context(), u.Claims.Actor, id, after)
		if e != nil {
			return
		}
		for _, ev := range items {
			b, _ := json.Marshal(ev)
			if _, e = fmt.Fprintf(w, "id: %d\nevent: update\ndata: %s\n\n", ev.ID, b); e != nil {
				return
			}
			after = ev.ID
		}
		if len(items) == 0 {
			if _, e = io.WriteString(w, ": heartbeat\n\n"); e != nil {
				return
			}
		}
		if flush.Flush() != nil {
			return
		}
		select {
		case <-r.Context().Done():
			return
		case <-deadline.C:
			return
		case <-ticker.C:
		}
	}
}

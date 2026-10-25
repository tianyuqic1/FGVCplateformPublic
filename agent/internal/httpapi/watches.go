package httpapi

import (
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/adapters/platform"
	"net/http"
)

func (s *Server) createWatch(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Conversation string `json:"conversation_id"`
		Run          string `json:"run_id"`
	}
	if !decode(w, r, &in) || !validID(w, in.Conversation) || !validID(w, in.Run) {
		return
	}
	u := who(r)
	// Check live authorization before registering any background work.
	if _, e := s.Platform.Catalog(platform.WithSession(r.Context(), platform.Session{Delegation: u.Grant})); e != nil {
		report(w, e)
		return
	}
	out, e := s.Store.CreateWatch(r.Context(), u.Claims.Actor, in.Conversation, in.Run, u.Grant)
	if e != nil {
		report(w, e)
		return
	}
	respond(w, 201, out)
}
func (s *Server) listWatches(w http.ResponseWriter, r *http.Request) {
	conv := r.URL.Query().Get("conversation_id")
	if !validID(w, conv) {
		return
	}
	out, e := s.Store.Watches(r.Context(), who(r).Claims.Actor, conv)
	if e != nil {
		report(w, e)
		return
	}
	respond(w, 200, map[string]any{"items": out})
}
func (s *Server) changeWatch(w http.ResponseWriter, r *http.Request) {
	if !validID(w, r.PathValue("id")) {
		return
	}
	u := who(r)
	status := "cancelled"
	grant := ""
	if r.PathValue("action") == "resume" {
		status = "active"
		grant = u.Grant
	} else if r.PathValue("action") != "cancel" {
		http.NotFound(w, r)
		return
	}
	if e := s.Store.ChangeWatch(r.Context(), u.Claims.Actor, r.PathValue("id"), status, grant); e != nil {
		report(w, e)
		return
	}
	respond(w, 200, map[string]string{"status": status})
}

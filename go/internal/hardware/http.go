package hardware

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sort"
	"time"

	"github.com/go-chi/chi/v5"
)

type Handler struct {
	Store Store
	Token string
	Now   func() time.Time
}

func (h Handler) Register(r chi.Router) {
	if h.Now == nil {
		h.Now = time.Now
	}
	r.Post("/api/internal/hardware/samples", h.ingest)
	r.Get("/api/hardware/nodes", h.nodes)
	r.Get("/api/hardware/nodes/{nodeID}", h.detail)
}
func reply(w http.ResponseWriter, code int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(payload)
}
func fail(w http.ResponseWriter, code int, message string) {
	reply(w, code, map[string]any{"error": message})
}
func (h Handler) ready(w http.ResponseWriter) bool {
	if h.Store == nil {
		fail(w, 503, "硬件监控尚未配置")
		return false
	}
	return true
}
func (h Handler) ingest(w http.ResponseWriter, r *http.Request) {
	if h.Token == "" || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+h.Token)) != 1 {
		fail(w, 401, "invalid collector token")
		return
	}
	if !h.ready(w) {
		return
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 128<<10))
	decoder.DisallowUnknownFields()
	var sample Snapshot
	if err := decoder.Decode(&sample); err != nil {
		fail(w, 400, "invalid sample JSON")
		return
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		fail(w, 400, "expected one sample")
		return
	}
	now := h.Now().UTC()
	if err := sample.Validate(now); err != nil {
		fail(w, 400, err.Error())
		return
	}
	sample.ReceivedAt = now
	if sample.GPUs == nil {
		sample.GPUs = []GPU{}
	}
	if sample.Disks == nil {
		sample.Disks = []Disk{}
	}
	if sample.Errors == nil {
		sample.Errors = []string{}
	}
	err := h.Store.Ingest(r.Context(), sample)
	if errors.Is(err, ErrOldSample) {
		fail(w, 409, "sample is not newer than the last sample")
		return
	}
	if err != nil {
		fail(w, 503, "hardware storage unavailable")
		return
	}
	reply(w, 202, map[string]any{"accepted": true})
}
func (h Handler) nodes(w http.ResponseWriter, r *http.Request) {
	if !h.ready(w) {
		return
	}
	samples, err := h.Store.Nodes(r.Context())
	if err != nil {
		fail(w, 503, "hardware storage unavailable")
		return
	}
	now := h.Now().UTC()
	nodes := []any{}
	for _, s := range samples {
		nodes = append(nodes, map[string]any{"node_id": s.NodeID, "name": s.Name, "status": s.Status(now), "received_at": s.ReceivedAt})
	}
	reply(w, 200, map[string]any{"nodes": nodes, "server_time": now})
}
func (h Handler) detail(w http.ResponseWriter, r *http.Request) {
	if !h.ready(w) {
		return
	}
	id := chi.URLParam(r, "nodeID")
	if !ValidNode(id) {
		fail(w, 400, "invalid node id")
		return
	}
	window := r.URL.Query().Get("window")
	duration, step := 15*time.Minute, 5
	switch window {
	case "", "15m":
	case "1h":
		duration, step = time.Hour, 15
	case "24h":
		duration, step = 24*time.Hour, 300
	default:
		fail(w, 400, "window must be 15m, 1h, or 24h")
		return
	}
	latest, err := h.Store.Latest(r.Context(), id)
	if errors.Is(err, ErrNotFound) {
		fail(w, 404, "node not found")
		return
	}
	if err != nil {
		fail(w, 503, "hardware storage unavailable")
		return
	}
	now := h.Now().UTC()
	history, err := h.Store.History(r.Context(), id, now.Add(-duration), step)
	if err != nil {
		fail(w, 503, "hardware history unavailable")
		return
	}
	recent, err := h.Store.History(r.Context(), id, now.Add(-90*time.Second), 1)
	if err != nil {
		fail(w, 503, "hardware alerts unavailable")
		return
	}
	tasks, err := h.Store.Tasks(r.Context(), id)
	if err != nil {
		fail(w, 503, "hardware task assignments unavailable")
		return
	}
	alerts := Alerts(recent, now)
	sort.Slice(alerts, func(i, j int) bool { return alerts[i].Code < alerts[j].Code })
	reply(w, 200, map[string]any{"snapshot": latest, "status": latest.Status(now), "history": history, "tasks": tasks, "alerts": alerts, "server_time": now, "step_seconds": step})
}

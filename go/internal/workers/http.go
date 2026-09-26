package workers

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/auth"
)

type Handler struct {
	Store Store
	Token string
	Now   func() time.Time
}

func reply(w http.ResponseWriter, code int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(body)
}
func fail(w http.ResponseWriter, code int, detail string) {
	reply(w, code, map[string]string{"detail": detail})
}
func (h Handler) Register(r chi.Router) {
	if h.Now == nil {
		h.Now = time.Now
	}
	r.Post("/api/internal/workers/register", func(w http.ResponseWriter, r *http.Request) { h.report(w, r, true) })
	r.Post("/api/internal/workers/heartbeat", func(w http.ResponseWriter, r *http.Request) { h.report(w, r, false) })
	r.Get("/api/workers", h.list)
	r.Get("/api/workers/{id}", h.list)
}
func (h Handler) report(w http.ResponseWriter, r *http.Request, register bool) {
	if h.Token == "" || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+h.Token)) != 1 {
		fail(w, 401, "Worker 凭据无效")
		return
	}
	var v Snapshot
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 32<<10))
	d.DisallowUnknownFields()
	if d.Decode(&v) != nil || d.Decode(new(any)) != io.EOF || !v.Validate() || (register && v.Sequence != 0) {
		fail(w, 400, "Worker 上报内容无效")
		return
	}
	if h.Store == nil {
		fail(w, 503, "Worker 状态服务未配置")
		return
	}
	err := h.Store.Report(r.Context(), v, register)
	if errors.Is(err, ErrConflict) {
		fail(w, 409, "实例会话已被替代或上报序号过期")
		return
	}
	if err != nil {
		fail(w, 503, "Worker 状态暂时无法保存")
		return
	}
	reply(w, 202, map[string]bool{"accepted": true})
}
func (h Handler) list(w http.ResponseWriter, r *http.Request) {
	if h.Store == nil {
		fail(w, 503, "Worker 状态服务未配置")
		return
	}
	q := r.URL.Query()
	kind, status, device := q.Get("kind"), q.Get("status"), q.Get("device")
	if (kind != "" && !kinds[kind]) || (status != "" && status != "online" && status != "delayed" && status != "offline") {
		fail(w, 400, "筛选条件无效")
		return
	}
	limit, offset := 6, 0
	if q.Has("limit") {
		n, e := strconv.Atoi(q.Get("limit"))
		if e != nil || n < 1 || n > 100 {
			fail(w, 400, "limit 必须为 1–100")
			return
		}
		limit = n
	}
	if q.Has("offset") {
		n, e := strconv.Atoi(q.Get("offset"))
		if e != nil || n < 0 {
			fail(w, 400, "offset 必须为非负整数")
			return
		}
		offset = n
	}
	all, err := h.Store.List(r.Context())
	if err != nil {
		fail(w, 503, "Worker 状态暂时不可用")
		return
	}
	now := h.Now().UTC()
	items := []Instance{}
	summary := map[string]int{"total": 0, "available": 0, "busy": 0, "abnormal": 0}
	search := strings.ToLower(strings.TrimSpace(q.Get("q")))
	user, _ := auth.UserFromContext(r.Context())
	owned := map[string]bool{}
	if user.Role == auth.Business {
		ids := []string{}
		for _, i := range all {
			for _, task := range i.Tasks {
				if task.Kind == "training" {
					ids = append(ids, task.ID)
				}
			}
		}
		if len(ids) > 0 {
			if scope, ok := h.Store.(interface {
				OwnedTraining(context.Context, string, []string) (map[string]bool, error)
			}); ok {
				owned, err = scope.OwnedTraining(r.Context(), user.ID.String(), ids)
				if err != nil {
					fail(w, 503, "任务归属暂时无法核实，请稍后重试")
					return
				}
			}
		}
	}
	for _, i := range all {
		i.Derive(now)
		i = visibleInstance(i, user.Role, owned)
		if id := chi.URLParam(r, "id"); id != "" {
			if i.ID == id {
				reply(w, 200, map[string]any{"worker": i, "server_time": now})
				return
			}
			continue
		}
		if kind != "" && kind != i.Kind || status != "" && status != i.Connection || device != "" && !strings.Contains(strings.ToLower(i.Device), strings.ToLower(device)) || search != "" && !strings.Contains(strings.ToLower(i.Name+" "+i.ID+" "+i.NodeID), search) {
			continue
		}
		summary["total"]++
		if i.CanAccept {
			summary["available"]++
		}
		if i.Connection == "online" && i.ActiveCount > 0 {
			summary["busy"]++
		}
		if i.Connection != "online" || i.Readiness == "dependency_error" || i.Readiness == "stopped" {
			summary["abnormal"]++
		}
		items = append(items, i)
	}
	if chi.URLParam(r, "id") != "" {
		fail(w, 404, "Worker 实例不存在")
		return
	}
	rank := func(i Instance) int {
		if i.Connection != "online" || i.Readiness == "dependency_error" {
			return 0
		}
		return 1
	}
	sort.SliceStable(items, func(a, b int) bool {
		if rank(items[a]) != rank(items[b]) {
			return rank(items[a]) < rank(items[b])
		}
		if items[a].Kind != items[b].Kind {
			return items[a].Kind < items[b].Kind
		}
		return items[a].ID < items[b].ID
	})
	total := len(items)
	if offset >= total {
		offset = 0
		if total > 0 {
			offset = (total - 1) / limit * limit
		}
	}
	end := offset + limit
	if end > total {
		end = total
	}
	view := "business"
	if user.Role == auth.Admin {
		view = "admin"
	}
	reply(w, 200, map[string]any{"items": items[offset:end], "summary": summary, "pagination": map[string]int{"total": total, "limit": limit, "offset": offset}, "server_time": now, "view": view})
}

func visibleInstance(i Instance, role auth.Role, owned map[string]bool) Instance {
	if role == auth.Admin {
		i.TaskVisibility = "all"
		return i
	}
	i.TaskVisibility = "own_training_only"
	i.SessionID = ""
	i.Sequence = 0
	tasks := []Task{}
	if role == auth.Business && i.Kind == "training" {
		for _, t := range i.Tasks {
			if t.Kind == "training" && owned[t.ID] {
				t.Stage = "训练任务执行中"
				tasks = append(tasks, t)
			}
		}
	}
	i.Tasks = tasks
	// Non-admin readers receive safe operational explanations, never raw diagnostics.
	switch i.Readiness {
	case "ready":
		i.Reason = "服务已就绪，具体任务能否执行仍取决于模型与依赖状态。"
	case "initializing":
		i.Reason = "服务正在初始化，请稍后查看。"
	case "dependency_error":
		i.Reason = "服务依赖暂时不可用，请联系平台管理员。"
	case "stopped":
		i.Reason = "服务已停止，请联系平台管理员。"
	default:
		i.Reason = "服务就绪状态尚未确认。"
	}
	return i
}

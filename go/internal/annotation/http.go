package annotation

import (
	"bytes"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

type Handler struct {
	Repo      *Repository
	Store     artifact.Store
	Token     string
	Publisher *Publisher
}

func response(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if status != 204 {
		_ = json.NewEncoder(w).Encode(value)
	}
}
func failure(w http.ResponseWriter, err error) {
	status, message := 503, "标注服务暂不可用"
	switch {
	case errors.Is(err, ErrInvalid):
		status, message = 422, "参数不合法，请检查类别、文件或状态"
	case errors.Is(err, ErrConflict):
		status, message = 409, err.Error()
	case errors.Is(err, pgx.ErrNoRows):
		status, message = 404, "标注项目或图片不存在"
	}
	response(w, status, map[string]string{"detail": message})
}
func decode(w http.ResponseWriter, r *http.Request, value any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
	d := json.NewDecoder(r.Body)
	d.DisallowUnknownFields()
	if d.Decode(value) != nil || d.Decode(new(any)) != io.EOF {
		failure(w, ErrInvalid)
		return false
	}
	return true
}
func validID(w http.ResponseWriter, id string) bool {
	if _, err := uuid.Parse(id); err != nil {
		failure(w, ErrInvalid)
		return false
	}
	return true
}
func (h *Handler) Register(r chi.Router) {
	r.Route("/api/annotation", func(r chi.Router) {
		r.Use(func(next http.Handler) http.Handler {
			return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if h == nil || h.Repo == nil {
					failure(w, errors.New("not configured"))
					return
				}
				next.ServeHTTP(w, r)
			})
		})
		r.Get("/status", func(w http.ResponseWriter, r *http.Request) {
			var raw []byte
			err := h.Repo.Pool.QueryRow(r.Context(), `SELECT jsonb_build_object('online',heartbeat_at>now()-interval '45 seconds','details',details) FROM annotation_worker_status ORDER BY heartbeat_at DESC LIMIT 1`).Scan(&raw)
			if errors.Is(err, pgx.ErrNoRows) {
				response(w, 200, map[string]any{"online": false})
				return
			}
			if err != nil {
				failure(w, err)
				return
			}
			response(w, 200, json.RawMessage(raw))
		})
		h.registerPublications(r)
		h.registerCatalog(r)
		r.Get("/projects", func(w http.ResponseWriter, r *http.Request) {
			p, err := h.Repo.Projects(r.Context())
			if err != nil {
				failure(w, err)
				return
			}
			response(w, 200, map[string]any{"items": p})
		})
		r.Post("/projects", func(w http.ResponseWriter, r *http.Request) {
			p, ok := decodeProject(w, r)
			if !ok {
				return
			}
			p, err := h.Repo.Create(r.Context(), p)
			if err != nil {
				failure(w, err)
				return
			}
			response(w, 201, p)
		})
		r.Route("/projects/{project}", func(r chi.Router) {
			r.Use(func(next http.Handler) http.Handler {
				return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if !validID(w, chi.URLParam(r, "project")) {
						return
					}
					next.ServeHTTP(w, r)
				})
			})
			r.Get("/tasks", func(w http.ResponseWriter, r *http.Request) {
				page := 1
				if raw := r.URL.Query().Get("page"); raw != "" {
					v, e := strconv.Atoi(raw)
					if e != nil || v < 1 || v > 100000 {
						failure(w, ErrInvalid)
						return
					}
					page = v
				}
				status := r.URL.Query().Get("status")
				if status != "" && !strings.Contains("|pending|queued|running|suggested|failed|unknown|confirmed|", "|"+status+"|") {
					failure(w, ErrInvalid)
					return
				}
				tasks, total, err := h.Repo.Tasks(r.Context(), chi.URLParam(r, "project"), status, page)
				if err != nil {
					failure(w, err)
					return
				}
				response(w, 200, map[string]any{"items": tasks, "total": total, "page": page, "page_size": 12})
			})
			r.Post("/images", h.upload)
			r.Post("/queue", func(w http.ResponseWriter, r *http.Request) {
				var in struct {
					IDs         []string `json:"ids"`
					AllowRemote bool     `json:"allow_remote"`
				}
				if !decode(w, r, &in) {
					return
				}
				if !in.AllowRemote {
					failure(w, ErrInvalid)
					return
				}
				n, err := h.Repo.Queue(r.Context(), chi.URLParam(r, "project"), in.IDs)
				if err != nil {
					failure(w, err)
					return
				}
				response(w, 200, map[string]any{"queued": n})
			})
			r.Get("/export", h.export)
		})
		r.Get("/tasks/{task}/image", h.image)
		r.Post("/tasks/{task}/confirm", func(w http.ResponseWriter, r *http.Request) {
			if !validID(w, chi.URLParam(r, "task")) {
				return
			}
			var in struct {
				Label string `json:"label"`
				Actor string `json:"actor"`
			}
			if !decode(w, r, &in) {
				return
			}
			if err := h.Repo.Confirm(r.Context(), chi.URLParam(r, "task"), in.Label, in.Actor); err != nil {
				failure(w, err)
				return
			}
			response(w, 200, map[string]bool{"confirmed": true})
		})
		r.Route("/internal", func(r chi.Router) {
			r.Use(func(next http.Handler) http.Handler {
				return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if h.Token == "" || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+h.Token)) != 1 {
						response(w, 401, map[string]string{"detail": "Worker 凭据无效"})
						return
					}
					next.ServeHTTP(w, r)
				})
			})
			r.Post("/heartbeat", func(w http.ResponseWriter, r *http.Request) {
				var info map[string]any
				if !decode(w, r, &info) {
					return
				}
				raw, _ := json.Marshal(info)
				_, err := h.Repo.Pool.Exec(r.Context(), `INSERT INTO annotation_worker_status(id,details) VALUES('low-resource',$1) ON CONFLICT(id) DO UPDATE SET heartbeat_at=now(),details=EXCLUDED.details`, raw)
				if err != nil {
					failure(w, err)
					return
				}
				response(w, 204, nil)
			})
			r.Post("/claim", func(w http.ResponseWriter, r *http.Request) {
				t, err := h.Repo.Claim(r.Context())
				h.jobResponse(w, r, t, err)
			})
			r.Get("/memory", func(w http.ResponseWriter, r *http.Request) {
				t, err := h.Repo.MemoryNext(r.Context())
				h.jobResponse(w, r, t, err)
			})
			r.Post("/tasks/{task}/result", func(w http.ResponseWriter, r *http.Request) {
				if !validID(w, chi.URLParam(r, "task")) {
					return
				}
				var in struct {
					Token  string          `json:"token"`
					Status string          `json:"status"`
					Result json.RawMessage `json:"result"`
					Error  string          `json:"error"`
				}
				if !decode(w, r, &in) {
					return
				}
				if !validID(w, in.Token) {
					return
				}
				if len(in.Error) > 500 {
					failure(w, ErrInvalid)
					return
				}
				if err := h.Repo.Finish(r.Context(), chi.URLParam(r, "task"), in.Token, in.Status, in.Result, in.Error); err != nil {
					failure(w, err)
					return
				}
				response(w, 204, nil)
			})
			r.Post("/tasks/{task}/indexed", func(w http.ResponseWriter, r *http.Request) {
				if !validID(w, chi.URLParam(r, "task")) {
					return
				}
				var in struct {
					OK    bool   `json:"ok"`
					Error string `json:"error"`
				}
				if !decode(w, r, &in) {
					return
				}
				if len(in.Error) > 500 {
					failure(w, ErrInvalid)
					return
				}
				if err := h.Repo.MemoryAck(r.Context(), chi.URLParam(r, "task"), in.OK, in.Error); err != nil {
					failure(w, err)
					return
				}
				response(w, 204, nil)
			})
		})
	})
}
func (h *Handler) jobResponse(w http.ResponseWriter, r *http.Request, t Task, err error) {
	if errors.Is(err, pgx.ErrNoRows) {
		response(w, 204, nil)
		return
	}
	if err != nil {
		failure(w, err)
		return
	}
	p, err := h.Repo.Project(r.Context(), t.ProjectID)
	if err != nil {
		failure(w, err)
		return
	}
	response(w, 200, map[string]any{"task": t, "project": p})
}
func validateImage(data []byte) (string, error) {
	if len(data) == 0 || len(data) > 20<<20 {
		return "", ErrInvalid
	}
	kind := http.DetectContentType(data)
	if kind != "image/png" && kind != "image/jpeg" {
		return "", ErrInvalid
	}
	cfg, _, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil || cfg.Width < 1 || cfg.Height < 1 || int64(cfg.Width)*int64(cfg.Height) > 25000000 {
		return "", ErrInvalid
	}
	if _, _, err = image.Decode(bytes.NewReader(data)); err != nil {
		return "", ErrInvalid
	}
	return kind, nil
}
func (h *Handler) upload(w http.ResponseWriter, r *http.Request) {
	pid := chi.URLParam(r, "project")
	if _, err := h.Repo.Project(r.Context(), pid); err != nil {
		failure(w, err)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 21<<20)
	if err := r.ParseMultipartForm(1 << 20); err != nil {
		failure(w, ErrInvalid)
		return
	}
	defer r.MultipartForm.RemoveAll()
	f, header, err := r.FormFile("image")
	if err != nil {
		failure(w, ErrInvalid)
		return
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, (20<<20)+1))
	if err != nil {
		failure(w, ErrInvalid)
		return
	}
	kind, err := validateImage(data)
	if err != nil {
		failure(w, err)
		return
	}
	name := filepath.Base(strings.ReplaceAll(header.Filename, "\\", "/"))
	if len(name) > 240 {
		name = "image"
	}
	tmp, err := os.CreateTemp("", "annotation-upload-")
	if err != nil {
		failure(w, err)
		return
	}
	defer os.Remove(tmp.Name())
	_, err = tmp.Write(data)
	closeErr := tmp.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		failure(w, err)
		return
	}
	a, err := h.Store.PutFile(r.Context(), tmp.Name(), artifact.PutRequest{ArtifactID: uuid.NewString(), ArtifactType: "annotation_image", ContentType: kind, Producer: "annotation-upload", Metadata: map[string]any{"project_id": pid}})
	if err != nil {
		failure(w, err)
		return
	}
	t, err := h.Repo.Add(r.Context(), pid, name, a)
	if err != nil {
		failure(w, err)
		return
	}
	t.Token = ""
	response(w, 201, t)
}
func (h *Handler) image(w http.ResponseWriter, r *http.Request) {
	if !validID(w, chi.URLParam(r, "task")) {
		return
	}
	t, err := h.Repo.Task(r.Context(), chi.URLParam(r, "task"))
	if err != nil {
		failure(w, err)
		return
	}
	tmp, err := os.MkdirTemp("", "annotation-preview-")
	if err != nil {
		failure(w, err)
		return
	}
	defer os.RemoveAll(tmp)
	path, err := h.Store.MaterializeVerified(r.Context(), t.Image, tmp)
	if err != nil {
		failure(w, err)
		return
	}
	w.Header().Set("Content-Type", t.Image.ContentType)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Cache-Control", "private, max-age=3600")
	http.ServeFile(w, r, path)
}
func (h *Handler) export(w http.ResponseWriter, r *http.Request) {
	pid := chi.URLParam(r, "project")
	p, err := h.Repo.Project(r.Context(), pid)
	if err != nil {
		failure(w, err)
		return
	}
	rows, err := h.Repo.Pool.Query(r.Context(), `SELECT id::text,filename,sha,label,actor,updated_at::text FROM annotation_tasks WHERE project_id=$1 AND status='confirmed' ORDER BY seq`, pid)
	if err != nil {
		failure(w, err)
		return
	}
	defer rows.Close()
	items := []map[string]string{}
	for rows.Next() {
		var id, name, sha, label, actor, at string
		if err = rows.Scan(&id, &name, &sha, &label, &actor, &at); err != nil {
			failure(w, err)
			return
		}
		items = append(items, map[string]string{"id": id, "filename": name, "sha256": sha, "label": label, "actor": actor, "confirmed_at": at, "image_url": "/api/annotation/tasks/" + id + "/image"})
	}
	if err = rows.Err(); err != nil {
		failure(w, err)
		return
	}
	w.Header().Set("Content-Disposition", `attachment; filename="annotations.json"`)
	response(w, 200, map[string]any{"schema_version": 1, "project": p, "samples": items})
}

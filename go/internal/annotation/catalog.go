package annotation

import (
	"context"
	"fmt"
	"net/http"

	"github.com/go-chi/chi/v5"
)

type Catalog struct {
	SchemaVersion int     `json:"schema_version"`
	Classes       []Class `json:"classes"`
}

func (c Catalog) Validate() error {
	if c.SchemaVersion != 1 {
		return fmt.Errorf("%w: schema_version 必须为 1", ErrInvalid)
	}
	return (Project{Name: "catalog", Classes: c.Classes, Method: "A", Domain: "general"}).Validate()
}

type QueueSummary struct {
	Pending    int   `json:"pending"`
	Queued     int   `json:"queued"`
	Running    int   `json:"running"`
	ThroughSeq int64 `json:"through_seq"`
}

func (s *Repository) QueueSummary(ctx context.Context, id string) (out QueueSummary, err error) {
	err = s.Pool.QueryRow(ctx, `SELECT count(*) FILTER(WHERE status='pending'),count(*) FILTER(WHERE status='queued'),count(*) FILTER(WHERE status='running'),coalesce(max(seq),0) FROM annotation_tasks WHERE project_id=$1`, id).Scan(&out.Pending, &out.Queued, &out.Running, &out.ThroughSeq)
	return
}
func (s *Repository) QueueAll(ctx context.Context, id string, through int64) (int64, error) {
	if through < 1 {
		return 0, ErrInvalid
	}
	tag, err := s.Pool.Exec(ctx, `UPDATE annotation_tasks SET status='queued',updated_at=now() WHERE project_id=$1 AND seq<=$2 AND status='pending'`, id, through)
	return tag.RowsAffected(), err
}
func (h *Handler) registerCatalog(r chi.Router) {
	r.Post("/catalog/validate", func(w http.ResponseWriter, r *http.Request) {
		var c Catalog
		if !decode(w, r, &c) {
			return
		}
		if e := c.Validate(); e != nil {
			failure(w, e)
			return
		}
		response(w, 200, c)
	})
	r.Get("/projects/{project}/catalog", func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "project")
		if !validID(w, id) {
			return
		}
		p, e := h.Repo.Project(r.Context(), id)
		if e != nil {
			failure(w, e)
			return
		}
		w.Header().Set("Content-Disposition", `attachment; filename="classes.json"`)
		response(w, 200, Catalog{SchemaVersion: 1, Classes: p.Classes})
	})
	r.Get("/projects/{project}/queue-summary", func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "project")
		if !validID(w, id) {
			return
		}
		out, e := h.Repo.QueueSummary(r.Context(), id)
		if e != nil {
			failure(w, e)
			return
		}
		response(w, 200, out)
	})
	r.Post("/projects/{project}/queue-all", func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "project")
		if !validID(w, id) {
			return
		}
		var in struct {
			ThroughSeq  int64 `json:"through_seq"`
			AllowRemote bool  `json:"allow_remote"`
		}
		if !decode(w, r, &in) {
			return
		}
		if !in.AllowRemote {
			failure(w, ErrInvalid)
			return
		}
		n, e := h.Repo.QueueAll(r.Context(), id, in.ThroughSeq)
		if e != nil {
			failure(w, e)
			return
		}
		response(w, 200, map[string]any{"queued": n})
	})
}

// Retain the existing JSON classes array for old API clients and workers; new
// clients submit the versioned JSON catalog. No second source of truth/file path.
func decodeProject(w http.ResponseWriter, r *http.Request) (Project, bool) {
	var in struct {
		Name    string   `json:"name"`
		Method  string   `json:"method"`
		Domain  string   `json:"domain"`
		Classes []Class  `json:"classes"`
		Catalog *Catalog `json:"catalog"`
	}
	if !decode(w, r, &in) {
		return Project{}, false
	}
	if in.Catalog != nil {
		if len(in.Classes) > 0 {
			failure(w, ErrInvalid)
			return Project{}, false
		}
		if e := in.Catalog.Validate(); e != nil {
			failure(w, e)
			return Project{}, false
		}
		in.Classes = in.Catalog.Classes
	}
	return Project{Name: in.Name, Method: in.Method, Domain: in.Domain, Classes: in.Classes}, true
}

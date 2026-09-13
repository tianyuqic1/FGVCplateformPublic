package annotation

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
)

type ReleaseRequest struct {
	ID        string            `json:"id"`
	Source    string            `json:"source"`
	ProjectID string            `json:"project_id"`
	DatasetID string            `json:"dataset_id"`
	BaseID    string            `json:"base_id"`
	Name      string            `json:"name"`
	IDs       []string          `json:"ids"`
	TrainOnly bool              `json:"train_only"`
	Split     dataset.SplitPlan `json:"split"`
	Mapping   map[string]string `json:"mapping"`
	Notes     string            `json:"notes"`
}
type releasePlan struct {
	Request  ReleaseRequest      `json:"request"`
	Archive  artifact.Descriptor `json:"archive"`
	Changes  map[string]any      `json:"changes"`
	Feedback []dataset.Feedback  `json:"feedback,omitempty"`
}
type Release struct {
	ID          string          `json:"id"`
	Status      string          `json:"status"`
	Plan        releasePlan     `json:"plan"`
	Preview     json.RawMessage `json:"preview"`
	Result      json.RawMessage `json:"result"`
	Error       string          `json:"error"`
	CreatedAt   time.Time       `json:"created_at"`
	Fingerprint string          `json:"-"`
}
type Publisher struct {
	Repo      *Repository
	Datasets  *dataset.Service
	previewMu sync.Mutex
}

const releaseSelect = `SELECT id::text,status,plan,preview,result,error,created_at,fingerprint FROM annotation_publications`

func scanRelease(row pgx.Row) (j Release, err error) {
	var raw []byte
	err = row.Scan(&j.ID, &j.Status, &raw, &j.Preview, &j.Result, &j.Error, &j.CreatedAt, &j.Fingerprint)
	if err == nil {
		err = json.Unmarshal(raw, &j.Plan)
	}
	return
}
func (p *Publisher) Get(ctx context.Context, id string) (Release, error) {
	return scanRelease(p.Repo.Pool.QueryRow(ctx, releaseSelect+` WHERE id=$1`, id))
}
func releaseFailure(w http.ResponseWriter, err error) {
	if errors.Is(err, dataset.ErrInvalid) || errors.Is(err, dataset.ErrInvalidArchive) {
		response(w, 422, map[string]string{"detail": err.Error()})
		return
	}
	if errors.Is(err, dataset.ErrConflict) {
		response(w, 409, map[string]string{"detail": err.Error()})
		return
	}
	failure(w, err)
}
func (in *ReleaseRequest) Validate() error {
	if len(in.Mapping) > 0 {
		return fmt.Errorf("%w: 不再支持类别映射，请保留已确认类别名称", dataset.ErrInvalid)
	}
	if _, e := uuid.Parse(in.ID); e != nil {
		return ErrInvalid
	}
	if len(in.IDs) < 1 || len(in.IDs) > 1000 || len(in.Notes) > 2000 {
		return ErrInvalid
	}
	if in.Source != "annotation" && in.Source != "feedback" {
		return ErrInvalid
	}
	if in.Source == "annotation" {
		if _, e := uuid.Parse(in.ProjectID); e != nil {
			return ErrInvalid
		}
	}
	if (in.DatasetID == "") != (in.BaseID == "") {
		return ErrInvalid
	}
	if in.Source == "feedback" && (in.DatasetID == "" || !in.TrainOnly) {
		return fmt.Errorf("%w: 回流必须选择来源数据集并只加入训练集", dataset.ErrInvalid)
	}
	if in.DatasetID == "" {
		if strings.TrimSpace(in.Name) == "" || len([]rune(in.Name)) > 120 {
			return ErrInvalid
		}
	}
	if e := in.Split.Validate(); e != nil {
		return e
	}
	sort.Strings(in.IDs)
	for i, id := range in.IDs {
		if _, e := uuid.Parse(id); e != nil {
			return ErrInvalid
		}
		if i > 0 && in.IDs[i-1] == id {
			return ErrInvalid
		}
	}
	return nil
}

// Preview freezes authoritative human labels and a verified archive, not values
// supplied by the browser. It never calls an LLM or changes an existing version.
func (p *Publisher) Preview(ctx context.Context, in ReleaseRequest) (Release, error) {
	if e := in.Validate(); e != nil {
		return Release{}, e
	}
	encoded, _ := json.Marshal(in)
	fingerprint := fmt.Sprintf("%x", sha256.Sum256(encoded))
	old, e := p.Get(ctx, in.ID)
	if e == nil {
		if old.Fingerprint != fingerprint {
			return old, ErrConflict
		}
		return old, nil
	}
	if !errors.Is(e, pgx.ErrNoRows) {
		return old, e
	}
	if !p.previewMu.TryLock() {
		return Release{}, fmt.Errorf("%w: 另一个发布清单正在检查，请稍后重试", ErrConflict)
	}
	defer p.previewMu.Unlock()
	ctx, cancel := context.WithTimeout(ctx, 20*time.Minute)
	defer cancel()
	var base *dataset.Snapshot
	if in.DatasetID != "" {
		b, e := p.Datasets.Repository.Base(ctx, in.DatasetID, in.BaseID)
		if e != nil {
			return Release{}, e
		}
		base = &b
	}
	changes := map[string]any{}
	plan := releasePlan{Request: in}
	if in.Source == "annotation" {
		project, e := p.Repo.Project(ctx, in.ProjectID)
		if e != nil {
			return Release{}, e
		}
		names := map[string]string{}
		for _, c := range project.Classes {
			names[c.ID] = c.Name
		}
		images := []dataset.LabeledImage{}
		for _, id := range in.IDs {
			task, e := p.Repo.Task(ctx, id)
			if e != nil {
				return Release{}, e
			}
			if task.Status != "confirmed" || task.ProjectID != in.ProjectID {
				return Release{}, ErrInvalid
			}
			label := names[task.Label]
			images = append(images, dataset.LabeledImage{ID: id, Label: label, Filename: task.Filename, Image: task.Image})
		}
		archive, summary, e := p.Datasets.BuildLabeledArchive(ctx, base, images, in.Split, in.TrainOnly)
		if e != nil {
			return Release{}, e
		}
		defer os.Remove(archive)
		plan.Archive, e = p.Datasets.StoreArchive(ctx, archive, in.ID)
		if e != nil {
			return Release{}, e
		}
		changes = summary
	} else {
		candidates, e := p.Datasets.Repository.Candidates(ctx, in.DatasetID)
		if e != nil {
			return Release{}, e
		}
		eligible := map[string]dataset.Feedback{}
		for _, f := range candidates {
			eligible[f.ID] = f
		}
		sourceVersions := map[string]int{}
		for _, id := range in.IDs {
			f, ok := eligible[id]
			if !ok {
				return Release{}, ErrInvalid
			}
			sourceVersions[f.SourceVersionID]++
			// InputRef is deliberately omitted from JSON; compare immutable source
			// identity and label at registration, with the bytes frozen below.
			plan.Feedback = append(plan.Feedback, f)
		}
		archive, summary, e := p.Datasets.BuildFeedbackArchive(ctx, *base, plan.Feedback)
		if e != nil {
			return Release{}, e
		}
		defer os.Remove(archive)
		plan.Archive, e = p.Datasets.StoreArchive(ctx, archive, in.ID)
		if e != nil {
			return Release{}, e
		}
		changes = summary
		changes["source_versions"] = sourceVersions
		changes["train_only"] = true
		changes["labels_immutable"] = true
	}
	changes["notes"] = in.Notes
	plan.Changes = changes
	preview := map[string]any{"changes": changes, "new_dataset": base == nil, "selected_count": len(in.IDs)}
	if base != nil {
		preview["base_name"] = base.Name
		preview["base_number"] = base.Number
		preview["base_id"] = base.VersionID
	}
	raw, _ := json.Marshal(plan)
	summary, _ := json.Marshal(preview)
	_, e = p.Repo.Pool.Exec(ctx, `INSERT INTO annotation_publications(id,status,fingerprint,plan,preview) VALUES($1,'preview',$2,$3,$4) ON CONFLICT(id) DO NOTHING`, in.ID, fingerprint, raw, summary)
	if e != nil {
		return Release{}, e
	}
	result, e := p.Get(ctx, in.ID)
	if e == nil && result.Fingerprint != fingerprint {
		return result, ErrConflict
	}
	return result, e
}

func (p *Publisher) Transition(ctx context.Context, id, action string) error {
	return pgx.BeginFunc(ctx, p.Repo.Pool, func(tx pgx.Tx) error {
		j, e := scanRelease(tx.QueryRow(ctx, releaseSelect+` WHERE id=$1 FOR UPDATE`, id))
		if e != nil {
			return e
		}
		if action == "cancel" {
			if j.Status != "preview" && j.Status != "failed" && j.Status != "cancelled" {
				return ErrConflict
			}
			if _, e = tx.Exec(ctx, `DELETE FROM annotation_publication_members WHERE publication_id=$1`, id); e != nil {
				return e
			}
			_, e = tx.Exec(ctx, `UPDATE annotation_publications SET status='cancelled',updated_at=now() WHERE id=$1`, id)
			return e
		}
		if j.Status == "queued" || j.Status == "building" || j.Status == "registering" || j.Status == "published" {
			return nil
		}
		if j.Status != "preview" && j.Status != "failed" {
			return ErrConflict
		}
		if len(j.Plan.Request.Mapping) > 0 {
			return fmt.Errorf("%w: 旧批次包含类别映射，请取消后重新检查清单", ErrConflict)
		}
		for _, sourceID := range j.Plan.Request.IDs {
			_, e = tx.Exec(ctx, `INSERT INTO annotation_publication_members(source,source_id,publication_id) VALUES($1,$2,$3) ON CONFLICT(source,source_id) DO NOTHING`, j.Plan.Request.Source, sourceID, id)
			if e != nil {
				return e
			}
			var owner string
			if e = tx.QueryRow(ctx, `SELECT publication_id::text FROM annotation_publication_members WHERE source=$1 AND source_id=$2`, j.Plan.Request.Source, sourceID).Scan(&owner); e != nil {
				return e
			}
			if owner != id {
				return fmt.Errorf("%w: 所选图片已在其他发布批次中", ErrConflict)
			}
		}
		_, e = tx.Exec(ctx, `UPDATE annotation_publications SET status='queued',error='',updated_at=now() WHERE id=$1`, id)
		return e
	})
}

// A session advisory lock serializes the coordinator across replicas. Crashes
// release the lock; the next worker resumes the persisted batch using its same
// dataset request ID, including crashes after registration but before ACK.
func (p *Publisher) ProcessNext(ctx context.Context) error {
	conn, e := p.Repo.Pool.Acquire(ctx)
	if e != nil {
		return e
	}
	defer conn.Release()
	var acquired bool
	if e = conn.QueryRow(ctx, `SELECT pg_try_advisory_lock(71420920)`).Scan(&acquired); e != nil || !acquired {
		return e
	}
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if _, err := conn.Exec(cleanup, `SELECT pg_advisory_unlock(71420920)`); err != nil {
			conn.Conn().Close(cleanup)
		}
	}()
	j, e := scanRelease(conn.QueryRow(ctx, releaseSelect+` WHERE status IN ('queued','building','registering') ORDER BY created_at LIMIT 1`))
	if errors.Is(e, pgx.ErrNoRows) {
		return nil
	}
	if e != nil {
		return e
	}
	work, cancel := context.WithTimeout(ctx, 20*time.Minute)
	defer cancel()
	if _, e = conn.Exec(work, `UPDATE annotation_publications SET status='building',updated_at=now() WHERE id=$1`, j.ID); e != nil {
		return e
	}
	result, err := p.execute(work, j)
	if ctx.Err() != nil {
		return ctx.Err()
	}
	status, message := "published", ""
	if err != nil {
		status = "failed"
		slog.ErrorContext(ctx, "dataset publication failed", "publication_id", j.ID, "error", err)
		message = "发布失败，原标签和已注册版本未改动。请检查计算/对象存储服务后重试。"
		if errors.Is(err, dataset.ErrInvalid) || errors.Is(err, dataset.ErrInvalidArchive) || errors.Is(err, dataset.ErrConflict) {
			message = err.Error()
		}
	}
	raw, _ := json.Marshal(result)
	if result == nil {
		raw = []byte(`{}`)
	}
	_, e = conn.Exec(ctx, `UPDATE annotation_publications SET status=$2,result=$3,error=$4,updated_at=now() WHERE id=$1`, j.ID, status, raw, message)
	return e
}
func (p *Publisher) execute(ctx context.Context, j Release) (map[string]any, error) {
	in := j.Plan.Request
	if len(in.Mapping) > 0 {
		return nil, fmt.Errorf("%w: 旧批次包含类别映射，请取消后重新检查清单", dataset.ErrInvalid)
	}
	cache, e := os.MkdirTemp("", "annotation-publish-*")
	if e != nil {
		return nil, e
	}
	defer os.RemoveAll(cache)
	archive, e := p.Datasets.Store.MaterializeVerified(ctx, j.Plan.Archive, cache)
	if e != nil {
		return nil, e
	}
	if _, e = p.Repo.Pool.Exec(ctx, `UPDATE annotation_publications SET status='registering',updated_at=now() WHERE id=$1`, j.ID); e != nil {
		return nil, e
	}
	if in.Source == "feedback" {
		return p.Datasets.PublishBuiltFeedback(ctx, in.DatasetID, in.BaseID, j.ID, archive, j.Plan.Changes, j.Plan.Feedback)
	}
	return p.Datasets.PublishBuilt(ctx, in.DatasetID, in.BaseID, in.Name, j.ID, archive, j.Plan.Changes)
}
func (p *Publisher) Run(ctx context.Context) {
	for ctx.Err() == nil {
		_ = p.ProcessNext(ctx)
		select {
		case <-ctx.Done():
			return
		case <-time.After(2 * time.Second):
		}
	}
}

func (h *Handler) registerPublications(r chi.Router) {
	r.Route("/publications", func(r chi.Router) {
		r.Use(func(next http.Handler) http.Handler {
			return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if h.Publisher == nil {
					failure(w, errors.New("publisher unavailable"))
					return
				}
				next.ServeHTTP(w, r)
			})
		})
		r.Get("/targets", h.publicationTargets)
		r.Get("/candidates", h.publicationCandidates)
		r.Post("/preview", func(w http.ResponseWriter, r *http.Request) {
			var in ReleaseRequest
			if !decode(w, r, &in) {
				return
			}
			j, e := h.Publisher.Preview(r.Context(), in)
			if e != nil {
				releaseFailure(w, e)
				return
			}
			response(w, 200, j)
		})
		r.Get("/", func(w http.ResponseWriter, r *http.Request) {
			page, e := releasePage(r)
			if e != nil {
				failure(w, e)
				return
			}
			rows, e := h.Repo.Pool.Query(r.Context(), releaseSelect+` ORDER BY created_at DESC LIMIT 12 OFFSET $1`, (page-1)*12)
			if e != nil {
				failure(w, e)
				return
			}
			defer rows.Close()
			items := []Release{}
			for rows.Next() {
				j, e := scanRelease(rows)
				if e != nil {
					failure(w, e)
					return
				}
				items = append(items, j)
			}
			if e = rows.Err(); e != nil {
				failure(w, e)
				return
			}
			var total int
			if e = h.Repo.Pool.QueryRow(r.Context(), `SELECT count(*) FROM annotation_publications`).Scan(&total); e != nil {
				failure(w, e)
				return
			}
			response(w, 200, map[string]any{"items": items, "total": total, "page": page})
		})
		r.Post("/{id}/{action}", func(w http.ResponseWriter, r *http.Request) {
			id, action := chi.URLParam(r, "id"), chi.URLParam(r, "action")
			if !validID(w, id) {
				return
			}
			if action != "publish" && action != "retry" && action != "cancel" {
				failure(w, ErrInvalid)
				return
			}
			if e := h.Publisher.Transition(r.Context(), id, action); e != nil {
				releaseFailure(w, e)
				return
			}
			j, e := h.Publisher.Get(r.Context(), id)
			if e != nil {
				failure(w, e)
				return
			}
			response(w, 200, j)
		})
	})
}
func releasePage(r *http.Request) (int, error) {
	raw := r.URL.Query().Get("page")
	if raw == "" {
		return 1, nil
	}
	n, e := strconv.Atoi(raw)
	if e != nil || n < 1 || n > 100000 {
		return 0, ErrInvalid
	}
	return n, nil
}
func (h *Handler) publicationTargets(w http.ResponseWriter, r *http.Request) {
	rows, e := h.Repo.Pool.Query(r.Context(), `SELECT jsonb_build_object('dataset_id',d.id,'name',d.name,'version_id',v.id,'number',v.version_number,'classes',a.artifact_metadata->'manifest'->'classes','split_counts',v.split_summary) FROM datasets d JOIN dataset_versions v ON v.dataset_id=d.id JOIN artifacts a ON a.id=v.manifest_artifact_id ORDER BY d.updated_at DESC,v.version_number DESC LIMIT 2000`)
	if e != nil {
		failure(w, e)
		return
	}
	defer rows.Close()
	items := []json.RawMessage{}
	for rows.Next() {
		var raw []byte
		if e = rows.Scan(&raw); e != nil {
			failure(w, e)
			return
		}
		items = append(items, json.RawMessage(raw))
	}
	if e = rows.Err(); e != nil {
		failure(w, e)
		return
	}
	response(w, 200, map[string]any{"items": items})
}
func (h *Handler) publicationCandidates(w http.ResponseWriter, r *http.Request) {
	page, e := releasePage(r)
	if e != nil {
		failure(w, e)
		return
	}
	source := r.URL.Query().Get("source")
	scope := r.URL.Query().Get("scope")
	var items []map[string]any
	if source == "annotation" {
		if !validID(w, scope) {
			return
		}
		var total int
		where := ` FROM annotation_tasks t WHERE t.project_id=$1 AND t.status='confirmed' AND NOT EXISTS(SELECT 1 FROM annotation_publication_members m WHERE m.source='annotation' AND m.source_id=t.id)`
		if e = h.Repo.Pool.QueryRow(r.Context(), `SELECT count(*)`+where, scope).Scan(&total); e != nil {
			failure(w, e)
			return
		}
		rows, e := h.Repo.Pool.Query(r.Context(), `SELECT t.id::text,t.filename,t.label,t.sha`+where+` ORDER BY t.seq LIMIT 12 OFFSET $2`, scope, (page-1)*12)
		if e != nil {
			failure(w, e)
			return
		}
		defer rows.Close()
		for rows.Next() {
			var id, name, label, sha string
			if e = rows.Scan(&id, &name, &label, &sha); e != nil {
				failure(w, e)
				return
			}
			items = append(items, map[string]any{"id": id, "filename": name, "label": label, "sha": sha})
		}
		if e = rows.Err(); e != nil {
			failure(w, e)
			return
		}
		if items == nil {
			items = []map[string]any{}
		}
		response(w, 200, map[string]any{"items": items, "total": total, "page": page, "page_size": 12})
		return
	} else if source == "feedback" {
		candidates, e := h.Publisher.Datasets.Repository.Candidates(r.Context(), scope)
		if e != nil {
			releaseFailure(w, e)
			return
		}
		rows, e := h.Repo.Pool.Query(r.Context(), `SELECT source_id::text FROM annotation_publication_members WHERE source='feedback'`)
		if e != nil {
			failure(w, e)
			return
		}
		reserved := map[string]bool{}
		for rows.Next() {
			var id string
			if e = rows.Scan(&id); e != nil {
				rows.Close()
				failure(w, e)
				return
			}
			reserved[id] = true
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			failure(w, e)
			return
		}
		for _, f := range candidates {
			if !reserved[f.ID] {
				items = append(items, map[string]any{"id": f.ID, "label": f.Label, "source_version_id": f.SourceVersionID, "model_version_id": f.ModelVersionID, "review_id": f.ReviewID})
			}
		}
	} else {
		failure(w, ErrInvalid)
		return
	}
	total := len(items)
	start := (page - 1) * 12
	if start > total {
		start = total
	}
	end := start + 12
	if end > total {
		end = total
	}
	out := items[start:end]
	if out == nil {
		out = []map[string]any{}
	}
	response(w, 200, map[string]any{"items": out, "total": total, "page": page, "page_size": 12})
}

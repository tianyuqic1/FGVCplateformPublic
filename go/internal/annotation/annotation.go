// Package annotation owns the human-confirmed classification workspace. Model
// calls are performed by an isolated compute worker, never in an HTTP request.
package annotation

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

var ErrInvalid = errors.New("invalid annotation request")
var ErrConflict = errors.New("annotation state conflict")

type Class struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}
type Project struct {
	ID        string  `json:"id"`
	Name      string  `json:"name"`
	Classes   []Class `json:"classes"`
	Method    string  `json:"method"`
	Domain    string  `json:"domain"`
	Total     int     `json:"total"`
	Confirmed int     `json:"confirmed"`
	Indexed   int     `json:"indexed"`
}

func (p Project) Validate() error {
	if strings.TrimSpace(p.Name) == "" || len(p.Name) > 180 || len(p.Classes) < 2 || len(p.Classes) > 1000 {
		return ErrInvalid
	}
	if !strings.Contains("ABCD", p.Method) || len(p.Method) != 1 {
		return ErrInvalid
	}
	if p.Domain != "general" && p.Domain != "birds" && p.Domain != "cars" {
		return ErrInvalid
	}
	seen := map[string]bool{}
	names := map[string]bool{}
	for _, c := range p.Classes {
		if strings.TrimSpace(c.ID) == "" || len(c.ID) > 180 || strings.TrimSpace(c.Name) == "" || len(c.Name) > 180 || seen[c.ID] {
			return ErrInvalid
		}
		if c.ID != strings.TrimSpace(c.ID) || c.Name != strings.TrimSpace(c.Name) || names[c.Name] {
			return ErrInvalid
		}
		seen[c.ID] = true
		names[c.Name] = true
	}
	return nil
}

type Task struct {
	ID         string              `json:"id"`
	Seq        int64               `json:"seq"`
	ProjectID  string              `json:"project_id"`
	Filename   string              `json:"filename"`
	Image      artifact.Descriptor `json:"image"`
	SHA        string              `json:"sha"`
	Status     string              `json:"status"`
	Result     json.RawMessage     `json:"result"`
	Label      string              `json:"label"`
	Actor      string              `json:"actor"`
	Error      string              `json:"error"`
	Token      string              `json:"token,omitempty"`
	Indexed    bool                `json:"indexed"`
	IndexError string              `json:"index_error"`
}
type Repository struct{ Pool *pgxpool.Pool }

const projectSelect = `SELECT p.id::text,p.name,p.classes,p.method,p.domain,
 (SELECT count(*) FROM annotation_tasks t WHERE t.project_id=p.id),
 (SELECT count(*) FROM annotation_tasks t WHERE t.project_id=p.id AND t.status='confirmed'),
 (SELECT count(*) FROM annotation_tasks t JOIN annotation_memory_outbox o ON o.task_id=t.id WHERE t.project_id=p.id AND o.delivered_at IS NOT NULL)
 FROM annotation_projects p`

func scanProject(row pgx.Row) (p Project, err error) {
	var raw []byte
	err = row.Scan(&p.ID, &p.Name, &raw, &p.Method, &p.Domain, &p.Total, &p.Confirmed, &p.Indexed)
	if err == nil {
		err = json.Unmarshal(raw, &p.Classes)
	}
	return
}
func (s *Repository) Project(ctx context.Context, id string) (Project, error) {
	return scanProject(s.Pool.QueryRow(ctx, projectSelect+` WHERE p.id=$1`, id))
}
func (s *Repository) Projects(ctx context.Context) ([]Project, error) {
	rows, err := s.Pool.Query(ctx, projectSelect+` ORDER BY p.created_at DESC LIMIT 500`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Project{}
	for rows.Next() {
		p, e := scanProject(rows)
		if e != nil {
			return nil, e
		}
		out = append(out, p)
	}
	return out, rows.Err()
}
func (s *Repository) Create(ctx context.Context, p Project) (Project, error) {
	if err := p.Validate(); err != nil {
		return p, err
	}
	p.ID = uuid.NewString()
	raw, _ := json.Marshal(p.Classes)
	_, err := s.Pool.Exec(ctx, `INSERT INTO annotation_projects(id,name,classes,method,domain) VALUES($1,$2,$3,$4,$5)`, p.ID, p.Name, raw, p.Method, p.Domain)
	return p, err
}

const taskSelect = `SELECT t.id::text,t.seq,t.project_id::text,t.filename,t.image,t.sha,t.status,t.result,t.label,t.actor,t.error,COALESCE(t.claim_token::text,''),COALESCE(o.delivered_at IS NOT NULL,false),COALESCE(o.error,'') FROM annotation_tasks t LEFT JOIN annotation_memory_outbox o ON o.task_id=t.id`

func scanTask(row pgx.Row) (t Task, err error) {
	var raw []byte
	err = row.Scan(&t.ID, &t.Seq, &t.ProjectID, &t.Filename, &raw, &t.SHA, &t.Status, &t.Result, &t.Label, &t.Actor, &t.Error, &t.Token, &t.Indexed, &t.IndexError)
	if err == nil {
		err = json.Unmarshal(raw, &t.Image)
	}
	return
}
func (s *Repository) Task(ctx context.Context, id string) (Task, error) {
	return scanTask(s.Pool.QueryRow(ctx, taskSelect+` WHERE t.id=$1`, id))
}
func (s *Repository) Tasks(ctx context.Context, pid, status string, page int) ([]Task, int, error) {
	var total int
	err := s.Pool.QueryRow(ctx, `SELECT count(*) FROM annotation_tasks WHERE project_id=$1 AND ($2='' OR status=$2)`, pid, status).Scan(&total)
	if err != nil {
		return nil, 0, err
	}
	rows, err := s.Pool.Query(ctx, taskSelect+` WHERE t.project_id=$1 AND ($2='' OR t.status=$2) ORDER BY t.seq LIMIT 12 OFFSET $3`, pid, status, (page-1)*12)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()
	out := []Task{}
	for rows.Next() {
		t, e := scanTask(rows)
		if e != nil {
			return nil, 0, e
		}
		t.Token = ""
		out = append(out, t)
	}
	return out, total, rows.Err()
}
func (s *Repository) Add(ctx context.Context, pid, name string, a artifact.Descriptor) (Task, error) {
	raw, _ := json.Marshal(a)
	var id string
	err := s.Pool.QueryRow(ctx, `INSERT INTO annotation_tasks(id,project_id,filename,image,sha) VALUES($1,$2,$3,$4,$5)
 ON CONFLICT(project_id,sha) DO UPDATE SET sha=EXCLUDED.sha RETURNING id::text`, uuid.NewString(), pid, name, raw, a.SHA256).Scan(&id)
	if err != nil {
		return Task{}, err
	}
	return s.Task(ctx, id)
}
func (s *Repository) Queue(ctx context.Context, pid string, ids []string) (int64, error) {
	if len(ids) < 1 || len(ids) > 100 {
		return 0, ErrInvalid
	}
	for _, id := range ids {
		if _, err := uuid.Parse(id); err != nil {
			return 0, ErrInvalid
		}
	}
	// Only fresh jobs can be queued. Unknown/failed paid calls are never silently replayed.
	tag, err := s.Pool.Exec(ctx, `UPDATE annotation_tasks SET status='queued',updated_at=now() WHERE project_id=$1 AND id=ANY($2::uuid[]) AND status='pending'`, pid, ids)
	return tag.RowsAffected(), err
}
func (s *Repository) Confirm(ctx context.Context, id, label, actor string) error {
	if strings.TrimSpace(actor) == "" || len(actor) > 120 {
		return ErrInvalid
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	var status, old string
	var classes []byte
	err = tx.QueryRow(ctx, `SELECT t.status,t.label,p.classes FROM annotation_tasks t JOIN annotation_projects p ON p.id=t.project_id WHERE t.id=$1 FOR UPDATE OF t`, id).Scan(&status, &old, &classes)
	if err != nil {
		return err
	}
	var cs []Class
	if err = json.Unmarshal(classes, &cs); err != nil {
		return err
	}
	valid := false
	for _, c := range cs {
		if c.ID == label {
			valid = true
		}
	}
	if !valid {
		return ErrInvalid
	}
	if status == "running" || status == "queued" {
		return fmt.Errorf("AI 任务进行中，请等待完成: %w", ErrConflict)
	}
	if status == "confirmed" {
		if old == label {
			return tx.Commit(ctx)
		}
		return fmt.Errorf("已确认标签不可覆盖，请导出后建立修订项目: %w", ErrConflict)
	}
	_, err = tx.Exec(ctx, `UPDATE annotation_tasks SET status='confirmed',label=$2,actor=$3,updated_at=now() WHERE id=$1`, id, label, actor)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO annotation_memory_outbox(task_id) VALUES($1) ON CONFLICT DO NOTHING`, id)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func (s *Repository) Claim(ctx context.Context) (Task, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return Task{}, err
	}
	defer tx.Rollback(ctx)
	// Expiry is quarantined rather than requeued: provider may already have billed.
	_, err = tx.Exec(ctx, `UPDATE annotation_tasks SET status='unknown',error='Worker 租约过期；结果未知，不自动重复调用',updated_at=now() WHERE status='running' AND lease_until<now()`)
	if err != nil {
		return Task{}, err
	}
	var id string
	err = tx.QueryRow(ctx, `SELECT id::text FROM annotation_tasks WHERE status='queued' ORDER BY seq FOR UPDATE SKIP LOCKED LIMIT 1`).Scan(&id)
	if errors.Is(err, pgx.ErrNoRows) {
		if e := tx.Commit(ctx); e != nil {
			return Task{}, e
		}
		return Task{}, err
	}
	if err != nil {
		return Task{}, err
	}
	_, err = tx.Exec(ctx, `UPDATE annotation_tasks SET status='running',claim_token=$2,lease_until=now()+interval '20 minutes',updated_at=now() WHERE id=$1`, id, uuid.NewString())
	if err != nil {
		return Task{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return Task{}, err
	}
	return s.Task(ctx, id)
}
func (s *Repository) Finish(ctx context.Context, id, token, status string, result json.RawMessage, message string) error {
	if status != "suggested" && status != "failed" && status != "unknown" {
		return ErrInvalid
	}
	var data struct {
		ClassIDs []string `json:"class_ids"`
	}
	if len(result) > 128*1024 || json.Unmarshal(result, &data) != nil {
		return ErrInvalid
	}
	t, err := s.Task(ctx, id)
	if err != nil {
		return err
	}
	p, err := s.Project(ctx, t.ProjectID)
	if err != nil {
		return err
	}
	allowed := map[string]bool{}
	for _, c := range p.Classes {
		allowed[c.ID] = true
	}
	seen := map[string]bool{}
	for _, c := range data.ClassIDs {
		if !allowed[c] || seen[c] {
			return ErrInvalid
		}
		seen[c] = true
	}
	if len(data.ClassIDs) > 10 || (status == "suggested" && len(data.ClassIDs) != min(10, len(p.Classes))) {
		return ErrInvalid
	}
	tag, err := s.Pool.Exec(ctx, `UPDATE annotation_tasks SET status=$3,result=$4,error=$5,lease_until=NULL,updated_at=now() WHERE id=$1 AND claim_token=$2 AND status='running' AND lease_until>now()`, id, token, status, result, message)
	if err == nil && tag.RowsAffected() == 0 {
		var same bool
		e := s.Pool.QueryRow(ctx, `SELECT claim_token=$2::uuid AND status=$3 AND result=$4::jsonb AND error=$5 FROM annotation_tasks WHERE id=$1`, id, token, status, result, message).Scan(&same)
		if e == nil && same {
			return nil
		}
		return ErrConflict
	}
	return err
}
func (s *Repository) MemoryNext(ctx context.Context) (Task, error) {
	return scanTask(s.Pool.QueryRow(ctx, taskSelect+` WHERE t.status='confirmed' AND o.delivered_at IS NULL AND o.next_at<=now() ORDER BY t.seq LIMIT 1`))
}
func (s *Repository) MemoryAck(ctx context.Context, id string, ok bool, message string) error {
	_, err := s.Pool.Exec(ctx, `UPDATE annotation_memory_outbox SET delivered_at=CASE WHEN $2 THEN now() ELSE NULL END,next_at=now()+interval '60 seconds',attempts=attempts+1,error=$3 WHERE task_id=$1 AND delivered_at IS NULL`, id, ok, message)
	return err
}

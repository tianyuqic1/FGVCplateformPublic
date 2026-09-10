package dataset

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

var ErrQueueFull = errors.New("dataset import queue is full")
var ErrNoImportJob = errors.New("no available dataset import")
var ErrImportLeaseLost = errors.New("dataset import lease lost")

// A durable job refers to object storage, never the HTTP handler's temporary file.
type ImportJob struct {
	ID        string              `json:"id"`
	Name      string              `json:"name"`
	RequestID string              `json:"request_id"`
	Status    string              `json:"status"`
	CreatedAt time.Time           `json:"created_at"`
	Result    map[string]any      `json:"result,omitempty"`
	Error     string              `json:"error,omitempty"`
	Archive   artifact.Descriptor `json:"-"`
	Attempt   int                 `json:"-"`
}

type ImportJobRepository interface {
	Enqueue(context.Context, ImportJob) error
	List(context.Context) ([]ImportJob, error)
	Claim(context.Context) (ImportJob, error)
	Renew(context.Context, ImportJob) error
	Finish(context.Context, ImportJob, map[string]any, string) error
}

type ImportQueue struct {
	Repository ImportJobRepository
	Service    *Service
}

func (q *ImportQueue) Enqueue(ctx context.Context, name, requestID, archivePath string) (ImportJob, error) {
	name = strings.TrimSpace(name)
	if err := validateImport(name, requestID); err != nil {
		return ImportJob{}, err
	}
	if archivePath == "" {
		return ImportJob{}, fmt.Errorf("%w: 请选择图片", ErrInvalid)
	}
	job := ImportJob{ID: uuid.NewString(), Name: name, RequestID: requestID, Status: "queued", CreatedAt: time.Now().UTC()}
	archive, err := q.Service.StoreArchive(ctx, archivePath, job.ID)
	if err != nil {
		return ImportJob{}, err
	}
	job.Archive = archive
	if err = q.Repository.Enqueue(ctx, job); err != nil {
		return ImportJob{}, err
	}
	return job, nil
}

// Run has one consumer per process. The repository also enforces a global
// concurrency bound, including when multiple control planes are running.
func (q *ImportQueue) Run(ctx context.Context) {
	for ctx.Err() == nil {
		err := q.ProcessNext(ctx)
		if err == nil {
			continue
		}
		if !errors.Is(err, ErrNoImportJob) && ctx.Err() == nil {
			slog.ErrorContext(ctx, "dataset queue worker", "error", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(2 * time.Second):
		}
	}
}

func (q *ImportQueue) ProcessNext(ctx context.Context) error {
	job, err := q.Repository.Claim(ctx)
	if err != nil {
		return err
	}
	workCtx, cancel := context.WithTimeout(ctx, 20*time.Minute)
	defer cancel()
	heartbeatDone := make(chan struct{})
	var heartbeatErr error
	go func() {
		defer close(heartbeatDone)
		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-workCtx.Done():
				return
			case <-ticker.C:
				if err := q.Repository.Renew(workCtx, job); err != nil {
					heartbeatErr = err
					cancel()
					return
				}
			}
		}
	}()
	result, importErr := q.Service.ImportArchive(workCtx, job.Name, job.RequestID, job.ID, job.Archive)
	cancel()
	<-heartbeatDone
	// Shutdown or a lost lease leaves the durable job for recovery. Completion
	// uses a fresh bounded context; it never inherits the original HTTP request.
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if heartbeatErr != nil {
		return heartbeatErr
	}
	message := ""
	if importErr != nil {
		slog.ErrorContext(ctx, "background dataset import failed", "job_id", job.ID, "error", importErr)
		message = "数据集扫描或存储失败，请检查计算服务后重新上传。"
		if errors.Is(importErr, ErrInvalidArchive) {
			message = "图片损坏或 ImageFolder 目录结构不合法。"
		}
		if errors.Is(importErr, ErrConflict) {
			message = "该导入请求已处理或与其他请求冲突，请刷新后重试。"
		}
		if errors.Is(importErr, context.DeadlineExceeded) {
			message = "后台导入超过 20 分钟，请检查计算服务后重新上传。"
		}
	}
	finishCtx, finishCancel := context.WithTimeout(ctx, 10*time.Second)
	defer finishCancel()
	return q.Repository.Finish(finishCtx, job, result, message)
}

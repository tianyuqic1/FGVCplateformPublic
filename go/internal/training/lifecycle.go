package training

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

type Status string

const (
	StatusQueued    Status = "queued"
	StatusPaused    Status = "paused"
	StatusRunning   Status = "running"
	StatusSucceeded Status = "succeeded"
	StatusFailed    Status = "failed"
	StatusCancelled Status = "cancelled"
)

type AttemptStatus string

const (
	AttemptRunning   AttemptStatus = "running"
	AttemptSucceeded AttemptStatus = "succeeded"
	AttemptFailed    AttemptStatus = "failed"
	AttemptFenced    AttemptStatus = "fenced"
	AttemptCancelled AttemptStatus = "cancelled"
	AttemptExpired   AttemptStatus = "expired"
)

type Code string

const (
	CodeNotFound                Code = "NOT_FOUND"
	CodeInvalidState            Code = "INVALID_STATE_TRANSITION"
	CodeObsoleteDispatch        Code = "OBSOLETE_DISPATCH"
	CodeFenced                  Code = "FENCED"
	CodeValidationFailed        Code = "VALIDATION_FAILED"
	CodeArtifactIntegrityFailed Code = "ARTIFACT_INTEGRITY_FAILED"
	CodeIdempotencyConflict     Code = "IDEMPOTENCY_CONFLICT"
	CodeAttemptsExhausted       Code = "ATTEMPTS_EXHAUSTED"
	CodeTemporarilyUnavailable  Code = "TEMPORARILY_UNAVAILABLE"
)

type DomainError struct {
	Code    Code
	Message string
}

func (err *DomainError) Error() string { return err.Message }

func ErrorCode(err error) Code {
	var domainErr *DomainError
	if errors.As(err, &domainErr) {
		return domainErr.Code
	}
	return ""
}

type Job struct {
	ID                 string
	Status             Status
	Payload            map[string]any
	MaxAttempts        int
	AttemptCount       int
	DispatchGeneration int64
	ExecutionEpoch     int64
	ActiveAttemptID    string
	AvailableAt        time.Time
	LastHeartbeatAt    time.Time
	CreatedAt          time.Time
	UpdatedAt          time.Time
	Result             CompleteResult
	ErrorCode          string
	ErrorMessage       string
}

type Run struct {
	ID               string
	JobID            string
	DatasetID        string
	DatasetVersionID string
	BackboneID       string
	Status           Status
	Progress         map[string]any
	CreatedAt        time.Time
	UpdatedAt        time.Time
}

type Attempt struct {
	ID              string
	Number          int
	ExecutionEpoch  int64
	WorkerID        string
	Status          AttemptStatus
	LeaseExpiresAt  time.Time
	LastHeartbeatAt time.Time
	StartedAt       time.Time
	FinishedAt      time.Time
	CompletionKey   string
	ResultDigest    string
	Result          CompleteResult
	ErrorCode       string
	ErrorMessage    string
}

type AuditEvent struct {
	EventType string
	Message   string
	Payload   map[string]any
	CreatedAt time.Time
}

type MetricPoint struct {
	ID             int64
	TrainingRunID  string
	AttemptID      string
	ExecutionEpoch int64
	Name           string
	Step           int64
	Value          float64
	RecordedAt     time.Time
	Context        map[string]any
}

type OutboxEvent struct {
	MessageID          string
	EventType          string
	SchemaVersion      int
	JobID              string
	DispatchGeneration int64
	OccurredAt         time.Time
}

type Aggregate struct {
	Job       Job
	Run       Run
	Attempts  map[string]*Attempt
	Events    []AuditEvent
	Outbox    []OutboxEvent
	Artifacts []artifact.Descriptor
	Metrics   []MetricPoint
}

type Repository interface {
	Create(context.Context, *Aggregate) error
	Get(context.Context, string) (*Aggregate, error)
	Update(context.Context, string, func(*Aggregate) error) error
	Expired(context.Context, time.Time, int) ([]string, error)
}

type CreateCommand struct {
	DatasetID        string
	DatasetVersionID string
	BackboneID       string
	Payload          map[string]any
	MaxAttempts      int
}

type CreatedJob struct {
	JobID         string `json:"job_id"`
	TrainingRunID string `json:"training_run_id"`
	Status        Status `json:"status"`
}

type ClaimCommand struct {
	JobID              string
	DispatchGeneration int64
	WorkerID           string
}

type ClaimDisposition string

const (
	Claimed  ClaimDisposition = "claimed"
	Obsolete ClaimDisposition = "obsolete"
	Terminal ClaimDisposition = "terminal"
)

type ClaimResult struct {
	Disposition    ClaimDisposition `json:"disposition"`
	JobID          string           `json:"job_id"`
	TrainingRunID  string           `json:"training_run_id,omitempty"`
	AttemptID      string           `json:"attempt_id,omitempty"`
	ExecutionEpoch int64            `json:"execution_epoch,omitempty"`
	LeaseExpiresAt time.Time        `json:"lease_expires_at,omitempty"`
	Payload        map[string]any   `json:"payload,omitempty"`
}

type HeartbeatCommand struct {
	JobID          string
	AttemptID      string
	ExecutionEpoch int64
}

type HeartbeatResult struct {
	Directive      string    `json:"directive"`
	LeaseExpiresAt time.Time `json:"lease_expires_at"`
}

type ProgressCommand struct {
	JobID          string
	AttemptID      string
	ExecutionEpoch int64
	Progress       map[string]any
	MetricPoints   []MetricPoint
}

type CompleteCommand struct {
	JobID          string
	AttemptID      string
	ExecutionEpoch int64
	CompletionKey  string
	ResultDigest   string
	Artifacts      []artifact.Descriptor
	Metrics        map[string]any
}

type CompleteResult struct {
	JobID          string         `json:"job_id"`
	TrainingRunID  string         `json:"training_run_id"`
	ModelVersionID string         `json:"model_version_id"`
	Metrics        map[string]any `json:"metrics"`
}

type FailCommand struct {
	JobID          string
	AttemptID      string
	ExecutionEpoch int64
	Retryable      bool
	ErrorCode      string
	ErrorMessage   string
}

type Service struct {
	repository Repository
	clock      func() time.Time
	leaseTTL   time.Duration
	verifier   interface {
		Verify(context.Context, artifact.Descriptor) error
	}
}

func NewService(repository Repository, clock func() time.Time, leaseTTL time.Duration) *Service {
	return &Service{repository: repository, clock: clock, leaseTTL: leaseTTL}
}

func NewServiceWithVerifier(
	repository Repository,
	verifier interface {
		Verify(context.Context, artifact.Descriptor) error
	},
	clock func() time.Time,
	leaseTTL time.Duration,
) *Service {
	return &Service{repository: repository, verifier: verifier, clock: clock, leaseTTL: leaseTTL}
}

func (service *Service) SetClock(clock func() time.Time) { service.clock = clock }

func (service *Service) Create(ctx context.Context, command CreateCommand) (CreatedJob, error) {
	if command.DatasetID == "" || command.DatasetVersionID == "" || command.BackboneID == "" {
		return CreatedJob{}, domainError(CodeValidationFailed, "dataset, dataset version, and backbone are required")
	}
	if command.MaxAttempts <= 0 {
		command.MaxAttempts = 3
	}
	now := service.clock().UTC()
	jobID, runID := newID("job"), newID("run")
	aggregate := &Aggregate{
		Job: Job{ID: jobID, Status: StatusQueued, Payload: cloneMap(command.Payload), MaxAttempts: command.MaxAttempts,
			DispatchGeneration: 1, AvailableAt: now, CreatedAt: now, UpdatedAt: now},
		Run: Run{ID: runID, JobID: jobID, DatasetID: command.DatasetID, DatasetVersionID: command.DatasetVersionID,
			BackboneID: command.BackboneID, Status: StatusQueued, Progress: map[string]any{}, CreatedAt: now, UpdatedAt: now},
		Attempts: map[string]*Attempt{},
		Events:   []AuditEvent{{EventType: "created", Message: "training job created", CreatedAt: now}},
		Outbox:   []OutboxEvent{readyEvent(jobID, 1, now)},
	}
	if err := service.repository.Create(ctx, aggregate); err != nil {
		return CreatedJob{}, err
	}
	return CreatedJob{JobID: jobID, TrainingRunID: runID, Status: StatusQueued}, nil
}

func (service *Service) Claim(ctx context.Context, command ClaimCommand) (result ClaimResult, err error) {
	now := service.clock().UTC()
	err = service.repository.Update(ctx, command.JobID, func(aggregate *Aggregate) error {
		job := &aggregate.Job
		if command.DispatchGeneration != job.DispatchGeneration || job.Status == StatusRunning || job.Status == StatusPaused {
			result = ClaimResult{Disposition: Obsolete, JobID: job.ID}
			return nil
		}
		if terminal(job.Status) {
			result = ClaimResult{Disposition: Terminal, JobID: job.ID}
			return nil
		}
		if job.Status != StatusQueued {
			return domainError(CodeInvalidState, "training job is not dispatchable")
		}
		if now.Before(job.AvailableAt) {
			return domainError(CodeTemporarilyUnavailable, "training job is not available yet")
		}
		if job.AttemptCount >= job.MaxAttempts {
			return domainError(CodeAttemptsExhausted, "training job has exhausted its attempts")
		}
		job.AttemptCount++
		job.ExecutionEpoch++
		attemptID := newID("attempt")
		leaseExpiresAt := now.Add(service.leaseTTL)
		attempt := &Attempt{ID: attemptID, Number: job.AttemptCount, ExecutionEpoch: job.ExecutionEpoch,
			WorkerID: command.WorkerID, Status: AttemptRunning, LeaseExpiresAt: leaseExpiresAt,
			LastHeartbeatAt: now, StartedAt: now}
		aggregate.Attempts[attemptID] = attempt
		job.ActiveAttemptID = attemptID
		job.LastHeartbeatAt = now
		job.Status = StatusRunning
		job.UpdatedAt = now
		aggregate.Run.Status = StatusRunning
		aggregate.Run.UpdatedAt = now
		aggregate.Events = append(aggregate.Events, AuditEvent{EventType: "claimed", Message: "training job claimed", Payload: map[string]any{"attempt_id": attemptID, "worker_id": command.WorkerID, "execution_epoch": job.ExecutionEpoch}, CreatedAt: now})
		result = ClaimResult{Disposition: Claimed, JobID: job.ID, TrainingRunID: aggregate.Run.ID,
			AttemptID: attemptID, ExecutionEpoch: job.ExecutionEpoch, LeaseExpiresAt: leaseExpiresAt, Payload: cloneMap(job.Payload)}
		return nil
	})
	return result, err
}

func (service *Service) Heartbeat(ctx context.Context, command HeartbeatCommand) (result HeartbeatResult, err error) {
	now := service.clock().UTC()
	err = service.repository.Update(ctx, command.JobID, func(aggregate *Aggregate) error {
		attempt, fenceErr := currentAttempt(aggregate, command.AttemptID, command.ExecutionEpoch)
		if fenceErr != nil {
			return fenceErr
		}
		attempt.LastHeartbeatAt, attempt.LeaseExpiresAt = now, now.Add(service.leaseTTL)
		aggregate.Job.LastHeartbeatAt, aggregate.Job.UpdatedAt = now, now
		result = HeartbeatResult{Directive: "continue", LeaseExpiresAt: attempt.LeaseExpiresAt}
		return nil
	})
	return result, err
}

func (service *Service) Progress(ctx context.Context, command ProgressCommand) error {
	now := service.clock().UTC()
	return service.repository.Update(ctx, command.JobID, func(aggregate *Aggregate) error {
		if _, err := currentAttempt(aggregate, command.AttemptID, command.ExecutionEpoch); err != nil {
			return err
		}
		for _, point := range command.MetricPoints {
			if err := validateMetricPoint(point); err != nil {
				return err
			}
			point.TrainingRunID = aggregate.Run.ID
			point.AttemptID = command.AttemptID
			point.ExecutionEpoch = command.ExecutionEpoch
			if point.RecordedAt.IsZero() {
				point.RecordedAt = now
			} else {
				point.RecordedAt = point.RecordedAt.UTC()
			}
			point.Context = cloneMap(point.Context)
			if !containsMetricPoint(aggregate.Metrics, point) {
				aggregate.Metrics = append(aggregate.Metrics, point)
			}
		}
		aggregate.Run.Progress = cloneMap(command.Progress)
		aggregate.Run.UpdatedAt, aggregate.Job.UpdatedAt = now, now
		return nil
	})
}

func validateMetricPoint(point MetricPoint) error {
	if strings.TrimSpace(point.Name) == "" || len(point.Name) > 100 || point.Step < 0 || math.IsNaN(point.Value) || math.IsInf(point.Value, 0) {
		return domainError(CodeValidationFailed, "metric name, step, and finite value are required")
	}
	return nil
}

func containsMetricPoint(points []MetricPoint, candidate MetricPoint) bool {
	for _, point := range points {
		if point.AttemptID == candidate.AttemptID && point.Name == candidate.Name && point.Step == candidate.Step {
			return true
		}
	}
	return false
}

func (service *Service) Pause(ctx context.Context, jobID string) error {
	return service.transitionControl(ctx, jobID, StatusPaused)
}

func (service *Service) Cancel(ctx context.Context, jobID string) error {
	return service.transitionControl(ctx, jobID, StatusCancelled)
}

func (service *Service) transitionControl(ctx context.Context, jobID string, target Status) error {
	now := service.clock().UTC()
	return service.repository.Update(ctx, jobID, func(aggregate *Aggregate) error {
		if terminal(aggregate.Job.Status) {
			return domainError(CodeInvalidState, fmt.Sprintf("training job cannot transition from %s", aggregate.Job.Status))
		}
		if target == StatusPaused && aggregate.Job.Status != StatusQueued && aggregate.Job.Status != StatusRunning {
			return domainError(CodeInvalidState, "training job cannot be paused from its current state")
		}
		aggregate.Job.ExecutionEpoch++
		if attempt := aggregate.Attempts[aggregate.Job.ActiveAttemptID]; attempt != nil && attempt.Status == AttemptRunning {
			if target == StatusCancelled {
				attempt.Status = AttemptCancelled
			} else {
				attempt.Status = AttemptFenced
			}
			attempt.FinishedAt = now
		}
		aggregate.Job.Status, aggregate.Job.UpdatedAt = target, now
		aggregate.Run.Status, aggregate.Run.UpdatedAt = target, now
		aggregate.Events = append(aggregate.Events, AuditEvent{EventType: string(target), Message: "training job " + string(target), CreatedAt: now})
		return nil
	})
}

func (service *Service) Resume(ctx context.Context, jobID string) error {
	now := service.clock().UTC()
	return service.repository.Update(ctx, jobID, func(aggregate *Aggregate) error {
		if aggregate.Job.Status != StatusPaused {
			return domainError(CodeInvalidState, "only a paused training job can be resumed")
		}
		aggregate.Job.Status, aggregate.Run.Status = StatusQueued, StatusQueued
		aggregate.Job.DispatchGeneration++
		aggregate.Job.ActiveAttemptID = ""
		aggregate.Job.AvailableAt, aggregate.Job.UpdatedAt, aggregate.Run.UpdatedAt = now, now, now
		aggregate.Outbox = append(aggregate.Outbox, readyEvent(aggregate.Job.ID, aggregate.Job.DispatchGeneration, now))
		aggregate.Events = append(aggregate.Events, AuditEvent{EventType: "resumed", Message: "training job resumed", CreatedAt: now})
		return nil
	})
}

func (service *Service) Complete(ctx context.Context, command CompleteCommand) (result CompleteResult, err error) {
	if service.verifier != nil {
		for _, descriptor := range command.Artifacts {
			if verifyErr := service.verifier.Verify(ctx, descriptor); verifyErr != nil {
				return CompleteResult{}, domainError(CodeArtifactIntegrityFailed, "artifact integrity verification failed")
			}
		}
	}
	now := service.clock().UTC()
	err = service.repository.Update(ctx, command.JobID, func(aggregate *Aggregate) error {
		if previous := aggregate.Attempts[command.AttemptID]; previous != nil && previous.CompletionKey == command.CompletionKey && command.CompletionKey != "" {
			if previous.ResultDigest != command.ResultDigest {
				return domainError(CodeIdempotencyConflict, "completion key was already used with a different result digest")
			}
			if previous.Status == AttemptSucceeded {
				result = previous.Result
				return nil
			}
		}
		attempt, fenceErr := currentAttempt(aggregate, command.AttemptID, command.ExecutionEpoch)
		if fenceErr != nil {
			return fenceErr
		}
		if command.CompletionKey == "" || command.ResultDigest == "" || len(command.Artifacts) == 0 {
			return domainError(CodeValidationFailed, "completion key, result digest, and artifacts are required")
		}
		for _, descriptor := range command.Artifacts {
			if err := validateDescriptor(descriptor, aggregate.Run.DatasetVersionID, aggregate.Run.ID, attempt.ID); err != nil {
				return err
			}
		}
		result = CompleteResult{JobID: aggregate.Job.ID, TrainingRunID: aggregate.Run.ID,
			ModelVersionID: newID("model"), Metrics: cloneMap(command.Metrics)}
		attempt.Status, attempt.FinishedAt = AttemptSucceeded, now
		attempt.CompletionKey, attempt.ResultDigest, attempt.Result = command.CompletionKey, command.ResultDigest, result
		aggregate.Artifacts = append([]artifact.Descriptor(nil), command.Artifacts...)
		aggregate.Job.Status, aggregate.Run.Status = StatusSucceeded, StatusSucceeded
		aggregate.Job.Result, aggregate.Job.UpdatedAt, aggregate.Run.UpdatedAt = result, now, now
		aggregate.Events = append(aggregate.Events, AuditEvent{EventType: "completed", Message: "training job completed", Payload: map[string]any{"attempt_id": attempt.ID, "model_version_id": result.ModelVersionID}, CreatedAt: now})
		return nil
	})
	return result, err
}

func (service *Service) Fail(ctx context.Context, command FailCommand) error {
	now := service.clock().UTC()
	return service.repository.Update(ctx, command.JobID, func(aggregate *Aggregate) error {
		attempt, fenceErr := currentAttempt(aggregate, command.AttemptID, command.ExecutionEpoch)
		if fenceErr != nil {
			return fenceErr
		}
		attempt.Status, attempt.FinishedAt = AttemptFailed, now
		attempt.ErrorCode, attempt.ErrorMessage = command.ErrorCode, command.ErrorMessage
		aggregate.Job.ErrorCode, aggregate.Job.ErrorMessage = command.ErrorCode, command.ErrorMessage
		if command.Retryable && aggregate.Job.AttemptCount < aggregate.Job.MaxAttempts {
			aggregate.Job.Status, aggregate.Run.Status = StatusQueued, StatusQueued
			aggregate.Job.DispatchGeneration++
			aggregate.Job.ExecutionEpoch++
			aggregate.Job.ActiveAttemptID = ""
			backoff := time.Duration(1<<min(aggregate.Job.AttemptCount-1, 6)) * time.Second
			aggregate.Job.AvailableAt = now.Add(backoff)
			aggregate.Outbox = append(aggregate.Outbox, readyEvent(aggregate.Job.ID, aggregate.Job.DispatchGeneration, aggregate.Job.AvailableAt))
		} else {
			aggregate.Job.Status, aggregate.Run.Status = StatusFailed, StatusFailed
		}
		aggregate.Job.UpdatedAt, aggregate.Run.UpdatedAt = now, now
		aggregate.Events = append(aggregate.Events, AuditEvent{EventType: "failed", Message: "training attempt failed", Payload: map[string]any{"attempt_id": attempt.ID, "error_code": command.ErrorCode, "retryable": command.Retryable}, CreatedAt: now})
		return nil
	})
}

func (service *Service) ReapExpired(ctx context.Context, limit int) ([]string, error) {
	now := service.clock().UTC()
	ids, err := service.repository.Expired(ctx, now, limit)
	if err != nil {
		return nil, err
	}
	reaped := make([]string, 0, len(ids))
	for _, id := range ids {
		err := service.repository.Update(ctx, id, func(aggregate *Aggregate) error {
			attempt := aggregate.Attempts[aggregate.Job.ActiveAttemptID]
			if aggregate.Job.Status != StatusRunning || attempt == nil || !attempt.LeaseExpiresAt.Before(now) {
				return nil
			}
			attempt.Status, attempt.FinishedAt = AttemptExpired, now
			aggregate.Job.ExecutionEpoch++
			aggregate.Job.ActiveAttemptID = ""
			if aggregate.Job.AttemptCount < aggregate.Job.MaxAttempts {
				aggregate.Job.Status, aggregate.Run.Status = StatusQueued, StatusQueued
				aggregate.Job.DispatchGeneration++
				aggregate.Job.AvailableAt = now
				aggregate.Outbox = append(aggregate.Outbox, readyEvent(id, aggregate.Job.DispatchGeneration, now))
			} else {
				aggregate.Job.Status, aggregate.Run.Status = StatusFailed, StatusFailed
			}
			aggregate.Job.UpdatedAt, aggregate.Run.UpdatedAt = now, now
			aggregate.Events = append(aggregate.Events, AuditEvent{EventType: "lease_expired", Message: "training attempt lease expired", Payload: map[string]any{"attempt_id": attempt.ID, "worker_id": attempt.WorkerID, "execution_epoch": attempt.ExecutionEpoch}, CreatedAt: now})
			return nil
		})
		if err != nil {
			return reaped, err
		}
		reaped = append(reaped, id)
	}
	return reaped, nil
}

func currentAttempt(aggregate *Aggregate, attemptID string, epoch int64) (*Attempt, error) {
	attempt := aggregate.Attempts[attemptID]
	if aggregate.Job.Status != StatusRunning || aggregate.Job.ActiveAttemptID != attemptID ||
		aggregate.Job.ExecutionEpoch != epoch || attempt == nil || attempt.ExecutionEpoch != epoch || attempt.Status != AttemptRunning {
		return nil, domainError(CodeFenced, "training worker no longer owns the active execution epoch")
	}
	return attempt, nil
}

func validateDescriptor(descriptor artifact.Descriptor, datasetVersionID, runID, attemptID string) error {
	parsed, err := url.Parse(descriptor.URI)
	if err != nil || (parsed.Scheme != "s3" && parsed.Scheme != "file") || parsed.Path == "" {
		return domainError(CodeValidationFailed, "artifact URI must be canonical s3:// or file://")
	}
	if len(descriptor.SHA256) != 64 || strings.Trim(descriptor.SHA256, "0123456789abcdef") != "" || descriptor.SizeBytes < 0 {
		return domainError(CodeValidationFailed, "artifact SHA-256 or size is invalid")
	}
	if descriptor.ArtifactID == "" || descriptor.ArtifactType == "" || descriptor.ContentType == "" || descriptor.Producer == "" || descriptor.SchemaVersion != 1 {
		return domainError(CodeValidationFailed, "artifact identity, type, content type, producer, and schema version are required")
	}
	if descriptor.DatasetVersionID != datasetVersionID {
		return domainError(CodeValidationFailed, "artifact Dataset Version lineage does not match")
	}
	if descriptor.TrainingRunID != runID {
		return domainError(CodeValidationFailed, "artifact training run lineage does not match")
	}
	if descriptor.AttemptID != attemptID {
		return domainError(CodeValidationFailed, "artifact attempt lineage does not match")
	}
	return nil
}

func terminal(status Status) bool {
	return status == StatusSucceeded || status == StatusFailed || status == StatusCancelled
}

func readyEvent(jobID string, generation int64, at time.Time) OutboxEvent {
	return OutboxEvent{MessageID: newID("message"), EventType: "training.job.ready.v1", SchemaVersion: 1,
		JobID: jobID, DispatchGeneration: generation, OccurredAt: at}
}

func domainError(code Code, message string) error { return &DomainError{Code: code, Message: message} }

func newID(prefix string) string {
	random := make([]byte, 16)
	if _, err := rand.Read(random); err != nil {
		panic("crypto/rand unavailable: " + err.Error())
	}
	random[6] = (random[6] & 0x0f) | 0x40
	random[8] = (random[8] & 0x3f) | 0x80
	encoded := hex.EncodeToString(random)
	_ = prefix // IDs are UUIDs because existing PostgreSQL primary keys use uuid.
	return encoded[0:8] + "-" + encoded[8:12] + "-" + encoded[12:16] + "-" + encoded[16:20] + "-" + encoded[20:32]
}

func cloneMap(source map[string]any) map[string]any {
	if source == nil {
		return map[string]any{}
	}
	result := make(map[string]any, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}

type MemoryRepository struct {
	mu         sync.Mutex
	aggregates map[string]*Aggregate
}

func NewMemoryRepository() *MemoryRepository {
	return &MemoryRepository{aggregates: map[string]*Aggregate{}}
}

func (repository *MemoryRepository) Create(_ context.Context, aggregate *Aggregate) error {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	if _, exists := repository.aggregates[aggregate.Job.ID]; exists {
		return fmt.Errorf("job already exists")
	}
	repository.aggregates[aggregate.Job.ID] = cloneAggregate(aggregate)
	return nil
}

func (repository *MemoryRepository) Get(_ context.Context, id string) (*Aggregate, error) {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	aggregate := repository.aggregates[id]
	if aggregate == nil {
		return nil, domainError(CodeNotFound, "training job not found")
	}
	return cloneAggregate(aggregate), nil
}

func (repository *MemoryRepository) Update(_ context.Context, id string, update func(*Aggregate) error) error {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	stored := repository.aggregates[id]
	if stored == nil {
		return domainError(CodeNotFound, "training job not found")
	}
	candidate := cloneAggregate(stored)
	if err := update(candidate); err != nil {
		return err
	}
	repository.aggregates[id] = candidate
	return nil
}

func (repository *MemoryRepository) Expired(_ context.Context, now time.Time, limit int) ([]string, error) {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	ids := make([]string, 0)
	for id, aggregate := range repository.aggregates {
		attempt := aggregate.Attempts[aggregate.Job.ActiveAttemptID]
		if aggregate.Job.Status == StatusRunning && attempt != nil && attempt.LeaseExpiresAt.Before(now) {
			ids = append(ids, id)
			if limit > 0 && len(ids) >= limit {
				break
			}
		}
	}
	return ids, nil
}

func cloneAggregate(source *Aggregate) *Aggregate {
	result := *source
	result.Job.Payload = cloneMap(source.Job.Payload)
	result.Job.Result.Metrics = cloneMap(source.Job.Result.Metrics)
	result.Run.Progress = cloneMap(source.Run.Progress)
	result.Attempts = make(map[string]*Attempt, len(source.Attempts))
	for id, attempt := range source.Attempts {
		copyAttempt := *attempt
		copyAttempt.Result.Metrics = cloneMap(attempt.Result.Metrics)
		result.Attempts[id] = &copyAttempt
	}
	result.Events = append([]AuditEvent(nil), source.Events...)
	result.Outbox = append([]OutboxEvent(nil), source.Outbox...)
	result.Artifacts = append([]artifact.Descriptor(nil), source.Artifacts...)
	result.Metrics = make([]MetricPoint, len(source.Metrics))
	for index, point := range source.Metrics {
		result.Metrics[index] = point
		result.Metrics[index].Context = cloneMap(point.Context)
	}
	return &result
}

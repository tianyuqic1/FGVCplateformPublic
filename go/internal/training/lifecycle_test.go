package training_test

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
)

type rejectingVerifier struct{}

func (rejectingVerifier) Verify(context.Context, artifact.Descriptor) error {
	return errors.New("sha mismatch")
}

func newService(now time.Time) (*training.Service, *training.MemoryRepository) {
	repository := training.NewMemoryRepository()
	return training.NewService(repository, func() time.Time { return now }, 2*time.Minute), repository
}

func createAndClaim(t *testing.T, service *training.Service) (training.CreatedJob, training.ClaimResult) {
	t.Helper()
	created, err := service.Create(context.Background(), training.CreateCommand{
		DatasetID: "dataset-1", DatasetVersionID: "version-1", BackboneID: "dinov3_vits16",
		Payload: map[string]any{"head_type": "ridge_linear"}, MaxAttempts: 3,
	})
	if err != nil {
		t.Fatal(err)
	}
	claimed, err := service.Claim(context.Background(), training.ClaimCommand{
		JobID: created.JobID, DispatchGeneration: 1, WorkerID: "worker-1",
	})
	if err != nil {
		t.Fatal(err)
	}
	return created, claimed
}

func TestCreatePersistsJobRunAuditAndOutboxAtomically(t *testing.T) {
	t.Parallel()
	now := time.Date(2026, 9, 7, 1, 2, 3, 0, time.UTC)
	service, repository := newService(now)

	created, err := service.Create(context.Background(), training.CreateCommand{
		DatasetID: "dataset-1", DatasetVersionID: "version-1", BackboneID: "dinov3_vits16",
		Payload: map[string]any{"epochs": 1}, MaxAttempts: 2,
	})
	if err != nil {
		t.Fatal(err)
	}
	aggregate, err := repository.Get(context.Background(), created.JobID)
	if err != nil {
		t.Fatal(err)
	}
	if aggregate.Job.Status != training.StatusQueued || aggregate.Run.Status != training.StatusQueued {
		t.Fatalf("job/run status = %s/%s", aggregate.Job.Status, aggregate.Run.Status)
	}
	if len(aggregate.Events) != 1 || len(aggregate.Outbox) != 1 {
		t.Fatalf("events/outbox = %d/%d", len(aggregate.Events), len(aggregate.Outbox))
	}
	if aggregate.Outbox[0].DispatchGeneration != 1 || aggregate.Outbox[0].EventType != "training.job.ready.v1" {
		t.Fatalf("outbox = %#v", aggregate.Outbox[0])
	}
}

func TestDuplicateDispatchCreatesOnlyOneAttempt(t *testing.T) {
	t.Parallel()
	service, repository := newService(time.Now().UTC())
	created, first := createAndClaim(t, service)
	second, err := service.Claim(context.Background(), training.ClaimCommand{
		JobID: created.JobID, DispatchGeneration: 1, WorkerID: "worker-2",
	})
	if err != nil {
		t.Fatal(err)
	}
	if first.Disposition != training.Claimed || second.Disposition != training.Obsolete {
		t.Fatalf("dispositions = %s/%s", first.Disposition, second.Disposition)
	}
	aggregate, _ := repository.Get(context.Background(), created.JobID)
	if len(aggregate.Attempts) != 1 {
		t.Fatalf("attempt count = %d", len(aggregate.Attempts))
	}
}

func TestPauseFencesWorkerAndResumePublishesNewGeneration(t *testing.T) {
	t.Parallel()
	service, repository := newService(time.Now().UTC())
	created, claimed := createAndClaim(t, service)
	if err := service.Pause(context.Background(), created.JobID); err != nil {
		t.Fatal(err)
	}
	err := service.Progress(context.Background(), training.ProgressCommand{
		JobID: created.JobID, AttemptID: claimed.AttemptID, ExecutionEpoch: claimed.ExecutionEpoch,
		Progress: map[string]any{"stage": "head"},
	})
	if training.ErrorCode(err) != training.CodeFenced {
		t.Fatalf("expected FENCED, got %v", err)
	}
	if err := service.Resume(context.Background(), created.JobID); err != nil {
		t.Fatal(err)
	}
	aggregate, _ := repository.Get(context.Background(), created.JobID)
	if aggregate.Job.DispatchGeneration != 2 || len(aggregate.Outbox) != 2 || aggregate.Job.Status != training.StatusQueued {
		t.Fatalf("aggregate after resume = %#v", aggregate)
	}
}

func TestCompletionIsIdempotentAndDigestConflictIsRejected(t *testing.T) {
	t.Parallel()
	service, _ := newService(time.Now().UTC())
	created, claimed := createAndClaim(t, service)
	command := training.CompleteCommand{
		JobID: created.JobID, AttemptID: claimed.AttemptID, ExecutionEpoch: claimed.ExecutionEpoch,
		CompletionKey: "complete-1", ResultDigest: "digest-1", Artifacts: []artifact.Descriptor{validDescriptor(created.TrainingRunID, claimed.AttemptID)},
		Metrics: map[string]any{"accuracy": 1.0},
	}
	first, err := service.Complete(context.Background(), command)
	if err != nil {
		t.Fatal(err)
	}
	second, err := service.Complete(context.Background(), command)
	if err != nil {
		t.Fatal(err)
	}
	if first.ModelVersionID == "" || first.ModelVersionID != second.ModelVersionID {
		t.Fatalf("completion results = %#v / %#v", first, second)
	}
	command.ResultDigest = "different"
	_, err = service.Complete(context.Background(), command)
	if training.ErrorCode(err) != training.CodeIdempotencyConflict {
		t.Fatalf("expected idempotency conflict, got %v", err)
	}
}

func TestInvalidArtifactCannotSucceedJob(t *testing.T) {
	t.Parallel()
	service, repository := newService(time.Now().UTC())
	created, claimed := createAndClaim(t, service)
	descriptor := validDescriptor(created.TrainingRunID, claimed.AttemptID)
	descriptor.SHA256 = "bad"
	_, err := service.Complete(context.Background(), training.CompleteCommand{
		JobID: created.JobID, AttemptID: claimed.AttemptID, ExecutionEpoch: claimed.ExecutionEpoch,
		CompletionKey: "complete-1", ResultDigest: "digest-1", Artifacts: []artifact.Descriptor{descriptor},
	})
	if training.ErrorCode(err) != training.CodeValidationFailed {
		t.Fatalf("expected validation failure, got %v", err)
	}
	aggregate, _ := repository.Get(context.Background(), created.JobID)
	if aggregate.Job.Status != training.StatusRunning {
		t.Fatalf("job status = %s", aggregate.Job.Status)
	}
}

func TestArtifactByteVerificationFailureCannotSucceedJob(t *testing.T) {
	t.Parallel()
	now := time.Now().UTC()
	repository := training.NewMemoryRepository()
	service := training.NewServiceWithVerifier(repository, rejectingVerifier{}, func() time.Time { return now }, 2*time.Minute)
	created, claimed := createAndClaim(t, service)
	_, err := service.Complete(context.Background(), training.CompleteCommand{
		JobID: created.JobID, AttemptID: claimed.AttemptID, ExecutionEpoch: claimed.ExecutionEpoch,
		CompletionKey: "complete-1", ResultDigest: "digest-1",
		Artifacts: []artifact.Descriptor{validDescriptor(created.TrainingRunID, claimed.AttemptID)},
	})
	if training.ErrorCode(err) != training.CodeArtifactIntegrityFailed {
		t.Fatalf("expected integrity failure, got %v", err)
	}
	aggregate, _ := repository.Get(context.Background(), created.JobID)
	if aggregate.Job.Status != training.StatusRunning {
		t.Fatalf("job status = %s", aggregate.Job.Status)
	}
}

func TestExpiredLeaseIsReapedAndRedispatched(t *testing.T) {
	t.Parallel()
	now := time.Now().UTC()
	service, repository := newService(now)
	created, claimed := createAndClaim(t, service)
	service.SetClock(func() time.Time { return now.Add(3 * time.Minute) })

	reaped, err := service.ReapExpired(context.Background(), 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(reaped) != 1 || reaped[0] != created.JobID {
		t.Fatalf("reaped = %#v", reaped)
	}
	aggregate, _ := repository.Get(context.Background(), created.JobID)
	if aggregate.Job.Status != training.StatusQueued || aggregate.Job.DispatchGeneration != 2 {
		t.Fatalf("job after reap = %#v", aggregate.Job)
	}
	if aggregate.Attempts[claimed.AttemptID].Status != training.AttemptExpired {
		t.Fatalf("attempt status = %s", aggregate.Attempts[claimed.AttemptID].Status)
	}
}

func TestCancelAndCompleteCannotBothWin(t *testing.T) {
	t.Parallel()
	service, repository := newService(time.Now().UTC())
	created, claimed := createAndClaim(t, service)
	start := make(chan struct{})
	var wait sync.WaitGroup
	wait.Add(2)
	results := make(chan string, 2)
	go func() {
		defer wait.Done()
		<-start
		if err := service.Cancel(context.Background(), created.JobID); err == nil {
			results <- "cancel"
		}
	}()
	go func() {
		defer wait.Done()
		<-start
		_, err := service.Complete(context.Background(), training.CompleteCommand{
			JobID: created.JobID, AttemptID: claimed.AttemptID, ExecutionEpoch: claimed.ExecutionEpoch,
			CompletionKey: "complete-1", ResultDigest: "digest-1", Artifacts: []artifact.Descriptor{validDescriptor(created.TrainingRunID, claimed.AttemptID)},
		})
		if err == nil {
			results <- "complete"
		}
	}()
	close(start)
	wait.Wait()
	close(results)
	if len(results) != 1 {
		t.Fatalf("winners = %d", len(results))
	}
	aggregate, _ := repository.Get(context.Background(), created.JobID)
	if aggregate.Job.Status != training.StatusCancelled && aggregate.Job.Status != training.StatusSucceeded {
		t.Fatalf("terminal status = %s", aggregate.Job.Status)
	}
}

func validDescriptor(runID, attemptID string) artifact.Descriptor {
	return artifact.Descriptor{
		ArtifactID: "model-1", ArtifactType: "model", URI: "s3://finevision-artifacts/models/model-1",
		SHA256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", SizeBytes: 12,
		ContentType: "application/octet-stream", StorageVersion: "s3-v1", Producer: "training-worker",
		TrainingRunID: runID, AttemptID: attemptID, SchemaVersion: 1,
	}
}

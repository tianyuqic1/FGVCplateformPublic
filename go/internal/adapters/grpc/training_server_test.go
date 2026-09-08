package grpcadapter_test

import (
	"context"
	"testing"
	"time"

	computev1 "github.com/tianyuqic1/FGVCplateformPublic/go/api/proto/finevision/compute/v1"
	grpcadapter "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/grpc"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestTrainingServerMapsClaimAndStableErrors(t *testing.T) {
	t.Parallel()
	service := training.NewService(training.NewMemoryRepository(), time.Now, 2*time.Minute)
	server := grpcadapter.NewTrainingLifecycleServer(service)

	_, err := server.Claim(context.Background(), &computev1.ClaimRequest{
		JobId: "11111111-1111-4111-8111-111111111111", DispatchGeneration: 1, WorkerId: "worker",
	})
	if status.Code(err) != codes.NotFound {
		t.Fatalf("missing claim code = %s", status.Code(err))
	}
	created, err := service.Create(context.Background(), training.CreateCommand{
		DatasetID: "dataset", DatasetVersionID: "version", BackboneID: "dinov3_vits16",
		Payload: map[string]any{"head_type": "ridge_linear"}, MaxAttempts: 2,
	})
	if err != nil {
		t.Fatal(err)
	}
	response, err := server.Claim(context.Background(), &computev1.ClaimRequest{
		JobId: created.JobID, DispatchGeneration: 1, WorkerId: "worker",
	})
	if err != nil {
		t.Fatal(err)
	}
	if response.Disposition != "claimed" || response.AttemptId == "" || response.ExecutionEpoch != 1 {
		t.Fatalf("claim response = %#v", response)
	}
}

func TestTrainingServerPersistsMetricPointsAndRejectsStaleEpoch(t *testing.T) {
	t.Parallel()
	repository := training.NewMemoryRepository()
	service := training.NewService(repository, time.Now, 2*time.Minute)
	server := grpcadapter.NewTrainingLifecycleServer(service)
	created, err := service.Create(context.Background(), training.CreateCommand{
		DatasetID: "dataset", DatasetVersionID: "version", BackboneID: "dinov3_vits16",
		Payload: map[string]any{}, MaxAttempts: 2,
	})
	if err != nil {
		t.Fatal(err)
	}
	claim, err := service.Claim(context.Background(), training.ClaimCommand{JobID: created.JobID, DispatchGeneration: 1, WorkerID: "worker"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = server.ReportProgress(context.Background(), &computev1.ProgressRequest{
		JobId: created.JobID, AttemptId: claim.AttemptID, ExecutionEpoch: claim.ExecutionEpoch,
		MetricPoints: []*computev1.MetricPoint{{Name: "eval_accuracy", Step: 1, Value: .9}},
	})
	if err != nil {
		t.Fatal(err)
	}
	aggregate, _ := repository.Get(context.Background(), created.JobID)
	if len(aggregate.Metrics) != 1 || aggregate.Metrics[0].Name != "eval_accuracy" {
		t.Fatalf("metrics = %#v", aggregate.Metrics)
	}
	_, err = server.ReportProgress(context.Background(), &computev1.ProgressRequest{
		JobId: created.JobID, AttemptId: claim.AttemptID, ExecutionEpoch: claim.ExecutionEpoch + 1,
		MetricPoints: []*computev1.MetricPoint{{Name: "eval_accuracy", Step: 2, Value: .95}},
	})
	if status.Code(err) != codes.Aborted {
		t.Fatalf("stale progress code = %s", status.Code(err))
	}
}

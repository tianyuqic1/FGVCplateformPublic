package grpcadapter

import (
	"context"

	computev1 "github.com/tianyuqic1/FGVCplateformPublic/go/api/proto/finevision/compute/v1"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/structpb"
	"google.golang.org/protobuf/types/known/timestamppb"
)

type TrainingLifecycleServer struct {
	computev1.UnimplementedTrainingLifecycleServer
	service *training.Service
}

func NewTrainingLifecycleServer(service *training.Service) *TrainingLifecycleServer {
	return &TrainingLifecycleServer{service: service}
}

func (server *TrainingLifecycleServer) Claim(ctx context.Context, request *computev1.ClaimRequest) (*computev1.ClaimResponse, error) {
	result, err := server.service.Claim(ctx, training.ClaimCommand{
		JobID: request.GetJobId(), DispatchGeneration: request.GetDispatchGeneration(), WorkerID: request.GetWorkerId(),
	})
	if err != nil {
		return nil, grpcError(err)
	}
	payload, _ := structpb.NewStruct(result.Payload)
	response := &computev1.ClaimResponse{
		Disposition: string(result.Disposition), JobId: result.JobID, TrainingRunId: result.TrainingRunID,
		AttemptId: result.AttemptID, ExecutionEpoch: result.ExecutionEpoch, Payload: payload,
	}
	if !result.LeaseExpiresAt.IsZero() {
		response.LeaseExpiresAt = timestamppb.New(result.LeaseExpiresAt)
	}
	return response, nil
}

func (server *TrainingLifecycleServer) Heartbeat(ctx context.Context, request *computev1.HeartbeatRequest) (*computev1.HeartbeatResponse, error) {
	result, err := server.service.Heartbeat(ctx, training.HeartbeatCommand{
		JobID: request.GetJobId(), AttemptID: request.GetAttemptId(), ExecutionEpoch: request.GetExecutionEpoch(),
	})
	if err != nil {
		return nil, grpcError(err)
	}
	return &computev1.HeartbeatResponse{Directive: result.Directive, LeaseExpiresAt: timestamppb.New(result.LeaseExpiresAt)}, nil
}

func (server *TrainingLifecycleServer) ReportProgress(ctx context.Context, request *computev1.ProgressRequest) (*computev1.ProgressResponse, error) {
	progress := map[string]any{}
	if request.Progress != nil {
		progress = request.Progress.AsMap()
	}
	metricPoints := make([]training.MetricPoint, 0, len(request.MetricPoints))
	for _, item := range request.MetricPoints {
		point := training.MetricPoint{Name: item.GetName(), Step: item.GetStep(), Value: item.GetValue()}
		if item.RecordedAt != nil {
			point.RecordedAt = item.RecordedAt.AsTime()
		}
		if item.Context != nil {
			point.Context = item.Context.AsMap()
		}
		metricPoints = append(metricPoints, point)
	}
	err := server.service.Progress(ctx, training.ProgressCommand{
		JobID: request.GetJobId(), AttemptID: request.GetAttemptId(), ExecutionEpoch: request.GetExecutionEpoch(),
		Progress: progress, MetricPoints: metricPoints,
	})
	if err != nil {
		return nil, grpcError(err)
	}
	return &computev1.ProgressResponse{}, nil
}

func (server *TrainingLifecycleServer) Complete(ctx context.Context, request *computev1.CompleteRequest) (*computev1.CompleteResponse, error) {
	descriptors := make([]artifact.Descriptor, 0, len(request.Artifacts))
	for _, item := range request.Artifacts {
		descriptor := artifact.Descriptor{
			ArtifactID: item.GetArtifactId(), ArtifactType: item.GetArtifactType(), URI: item.GetUri(),
			SHA256: item.GetSha256(), SizeBytes: item.GetSizeBytes(), ContentType: item.GetContentType(),
			StorageVersion: item.GetStorageVersion(), Producer: item.GetProducer(),
			DatasetVersionID: item.GetDatasetVersionId(), TrainingRunID: item.GetTrainingRunId(),
			AttemptID: item.GetAttemptId(), SchemaVersion: int(item.GetSchemaVersion()),
		}
		if item.CreatedAt != nil {
			descriptor.CreatedAt = item.CreatedAt.AsTime()
		}
		if item.VerifiedAt != nil {
			descriptor.VerifiedAt = item.VerifiedAt.AsTime()
		}
		if item.Metadata != nil {
			descriptor.Metadata = item.Metadata.AsMap()
		}
		descriptors = append(descriptors, descriptor)
	}
	metrics := map[string]any{}
	if request.Metrics != nil {
		metrics = request.Metrics.AsMap()
	}
	result, err := server.service.Complete(ctx, training.CompleteCommand{
		JobID: request.GetJobId(), AttemptID: request.GetAttemptId(), ExecutionEpoch: request.GetExecutionEpoch(),
		CompletionKey: request.GetCompletionKey(), ResultDigest: request.GetResultDigest(), Artifacts: descriptors, Metrics: metrics,
	})
	if err != nil {
		return nil, grpcError(err)
	}
	responseMetrics, _ := structpb.NewStruct(result.Metrics)
	return &computev1.CompleteResponse{
		JobId: result.JobID, TrainingRunId: result.TrainingRunID,
		ModelVersionId: result.ModelVersionID, Metrics: responseMetrics,
	}, nil
}

func (server *TrainingLifecycleServer) Fail(ctx context.Context, request *computev1.FailRequest) (*computev1.FailResponse, error) {
	err := server.service.Fail(ctx, training.FailCommand{
		JobID: request.GetJobId(), AttemptID: request.GetAttemptId(), ExecutionEpoch: request.GetExecutionEpoch(),
		Retryable: request.GetRetryable(), ErrorCode: request.GetErrorCode(), ErrorMessage: request.GetErrorMessage(),
	})
	if err != nil {
		return nil, grpcError(err)
	}
	return &computev1.FailResponse{}, nil
}

func grpcError(err error) error {
	code := training.ErrorCode(err)
	grpcCode := codes.Internal
	switch code {
	case training.CodeNotFound:
		grpcCode = codes.NotFound
	case training.CodeValidationFailed:
		grpcCode = codes.InvalidArgument
	case training.CodeArtifactIntegrityFailed:
		grpcCode = codes.DataLoss
	case training.CodeFenced, training.CodeObsoleteDispatch, training.CodeInvalidState,
		training.CodeIdempotencyConflict, training.CodeAttemptsExhausted:
		grpcCode = codes.Aborted
	case training.CodeTemporarilyUnavailable:
		grpcCode = codes.Unavailable
	}
	return status.Errorf(grpcCode, "%s: %s", code, err.Error())
}

package httpapi

import (
	"context"
	"errors"
	"sync"

	"github.com/go-chi/chi/v5/middleware"
	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/go/api/openapi"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
)

var ErrReadModelNotFound = errors.New("read model not found")

type ReadModels interface {
	ListDatasets(context.Context) ([]map[string]any, error)
	GetDataset(context.Context, string) (map[string]any, error)
	ListJobs(context.Context) ([]map[string]any, error)
	GetJob(context.Context, string) (map[string]any, error)
	ListTrainingRuns(context.Context) ([]map[string]any, error)
	GetTrainingRun(context.Context, string) (map[string]any, error)
	JobIDForRun(context.Context, string) (string, error)
	ResolveDatasetScope(context.Context, string, string) (string, string, string, error)
}

type Server struct {
	lifecycle *training.Service
	reads     ReadModels
	llm       *llm.Application
	created   sync.Map
}

func NewServer(lifecycle *training.Service, reads ReadModels, llmApplication *llm.Application) *Server {
	return &Server{lifecycle: lifecycle, reads: reads, llm: llmApplication}
}

func (server *Server) GenerateLLMAssistance(ctx context.Context, request openapi.GenerateLLMAssistanceRequestObject) (openapi.GenerateLLMAssistanceResponseObject, error) {
	if request.Body == nil {
		return openapi.GenerateLLMAssistance422JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "VALIDATION_FAILED", "request body is required"))}, nil
	}
	if server.llm == nil {
		return openapi.GenerateLLMAssistance503JSONResponse(errorEnvelope(ctx, "LLM_GATEWAY_UNAVAILABLE", "LLM Gateway is not configured")), nil
	}
	assistance, err := server.llm.Assist(ctx, string(request.Body.Task), map[string]any(request.Body.Context))
	if err != nil {
		return openapi.GenerateLLMAssistance503JSONResponse(errorEnvelope(ctx, "LLM_GATEWAY_UNAVAILABLE", "LLM assistance could not be generated")), nil
	}
	return openapi.GenerateLLMAssistance200JSONResponse{LLMAssistanceResponseJSONResponse: openapi.LLMAssistanceResponseJSONResponse(openapi.LLMAssistanceEnvelope{Assistance: openapi.FreeFormObject(assistance)})}, nil
}

func (server *Server) GetHealth(context.Context, openapi.GetHealthRequestObject) (openapi.GetHealthResponseObject, error) {
	return openapi.GetHealth200JSONResponse{Status: openapi.Ok, Runtime: openapi.GoControlPlane}, nil
}

func (server *Server) ListDatasets(ctx context.Context, _ openapi.ListDatasetsRequestObject) (openapi.ListDatasetsResponseObject, error) {
	items, err := server.reads.ListDatasets(ctx)
	if err != nil {
		return nil, err
	}
	return openapi.ListDatasets200JSONResponse{Datasets: freeFormList(items)}, nil
}

func (server *Server) GetDataset(ctx context.Context, request openapi.GetDatasetRequestObject) (openapi.GetDatasetResponseObject, error) {
	item, err := server.reads.GetDataset(ctx, request.DatasetId)
	if errors.Is(err, ErrReadModelNotFound) {
		return openapi.GetDataset404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "NOT_FOUND", "dataset not found"))}, nil
	}
	if err != nil {
		return nil, err
	}
	return openapi.GetDataset200JSONResponse{DatasetResponseJSONResponse: openapi.DatasetResponseJSONResponse(openapi.DatasetEnvelope{Dataset: openapi.FreeFormObject(item)})}, nil
}

func (server *Server) ListJobs(ctx context.Context, _ openapi.ListJobsRequestObject) (openapi.ListJobsResponseObject, error) {
	items, err := server.reads.ListJobs(ctx)
	if err != nil {
		return nil, err
	}
	return openapi.ListJobs200JSONResponse{Jobs: freeFormList(items)}, nil
}

func (server *Server) GetJob(ctx context.Context, request openapi.GetJobRequestObject) (openapi.GetJobResponseObject, error) {
	item, err := server.reads.GetJob(ctx, request.JobId.String())
	if errors.Is(err, ErrReadModelNotFound) {
		return openapi.GetJob404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "NOT_FOUND", "job not found"))}, nil
	}
	if err != nil {
		return nil, err
	}
	return openapi.GetJob200JSONResponse{JobResponseJSONResponse: openapi.JobResponseJSONResponse(openapi.JobEnvelope{Job: openapi.FreeFormObject(item)})}, nil
}

func (server *Server) ListModelWeights(context.Context, openapi.ListModelWeightsRequestObject) (openapi.ListModelWeightsResponseObject, error) {
	return openapi.ListModelWeights200JSONResponse{Weights: freeFormList([]map[string]any{managedViTSWeight()})}, nil
}

func (server *Server) EvictModelWeightCache(ctx context.Context, request openapi.EvictModelWeightCacheRequestObject) (openapi.EvictModelWeightCacheResponseObject, error) {
	if request.Preset != "dinov3_vits" {
		return openapi.EvictModelWeightCache404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "NOT_FOUND", "pretrained weight preset not found"))}, nil
	}
	weight := managedViTSWeight()
	// The canonical Git LFS/S3 object is immutable. Runtime cache eviction is a
	// separate, auditable compute-plane operation and this catalog endpoint never
	// deletes the canonical object.
	return openapi.EvictModelWeightCache200JSONResponse(openapi.FreeFormObject{
		"deleted": false, "preset": request.Preset, "cache_dir": weight["cache_dir"],
		"before": weight, "after": weight, "canonical_preserved": true,
	}), nil
}

func managedViTSWeight() map[string]any {
	const digest = "2a1ec16ae28ffa07bc0ead0241ee7df9fc26451fe6f9f839b7b3afa0a906b040"
	return map[string]any{
		"preset": "dinov3_vits", "extractor": "dinov3_vits", "backbone_id": "dinov3_vits16",
		"model_name": "vit_small_patch16_dinov3", "repo_id": "timm/vit_small_patch16_dinov3.lvd1689m",
		"state": "managed", "cache_status": "managed", "cached": true, "cache_bytes": int64(86362376),
		"complete_size_bytes": int64(86362376), "complete_file_count": 1, "partial_bytes": 0,
		"incomplete_file_count": 0, "sha256": digest,
		"cache_dir":     "s3://finevision-artifacts/pretrained/dinov3/2a/" + digest,
		"download_hint": "Git LFS release weight is verified and mirrored into the S3-compatible ArtifactStore.",
		"description":   "Phase 1 approved DINOv3 ViT-S/16 pretrained backbone.",
	}
}

func (server *Server) ListTrainingRuns(ctx context.Context, _ openapi.ListTrainingRunsRequestObject) (openapi.ListTrainingRunsResponseObject, error) {
	items, err := server.reads.ListTrainingRuns(ctx)
	if err != nil {
		return nil, err
	}
	return openapi.ListTrainingRuns200JSONResponse{TrainingRuns: freeFormList(items)}, nil
}

func (server *Server) CreateTrainingRun(ctx context.Context, request openapi.CreateTrainingRunRequestObject) (openapi.CreateTrainingRunResponseObject, error) {
	if request.Body == nil {
		return openapi.CreateTrainingRun422JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, string(training.CodeValidationFailed), "request body is required"))}, nil
	}
	extractor := "color_stats"
	if request.Body.Extractor != nil {
		extractor = string(*request.Body.Extractor)
	}
	backboneByExtractor := map[string]string{
		"color_stats": "color_stats_v1", "dinov3_vits": "dinov3_vits16",
		"dinov3_vitb": "dinov3_vitb16", "dinov3_vitl": "dinov3_vitl16",
	}
	backboneID := backboneByExtractor[extractor]
	if request.Body.BackboneId != nil && *request.Body.BackboneId != "" {
		backboneID = *request.Body.BackboneId
	}
	maxAttempts := 3
	if request.Body.MaxAttempts != nil {
		maxAttempts = *request.Body.MaxAttempts
	}
	featureBatchSize := 8
	if request.Body.FeatureBatchSize != nil {
		featureBatchSize = *request.Body.FeatureBatchSize
	}
	featurePool := "cls"
	if request.Body.FeaturePool != nil {
		featurePool = string(*request.Body.FeaturePool)
	}
	imageSize := 0
	if request.Body.ImageSize != nil {
		imageSize = *request.Body.ImageSize
	} else if extractor != "color_stats" {
		imageSize = 448
	}
	if imageSize != 0 && imageSize%16 != 0 {
		return openapi.CreateTrainingRun422JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, string(training.CodeValidationFailed), "image_size must be divisible by 16 for DINOv3 patch16 backbones"))}, nil
	}
	extractorConfig := map[string]any{"type": extractor, "backbone_id": backboneID}
	if extractor != "color_stats" {
		modelByExtractor := map[string]string{
			"dinov3_vits": "vit_small_patch16_dinov3", "dinov3_vitb": "vit_base_patch16_dinov3",
			"dinov3_vitl": "vit_large_patch16_dinov3",
		}
		extractorConfig = map[string]any{
			"type": "timm_dinov3", "preset": extractor, "model_name": modelByExtractor[extractor],
			"pretrained": true, "backbone_id": backboneID, "feature_pool": featurePool, "image_size": imageSize,
		}
	}
	if request.Body.ExtractorConfig != nil {
		for key, value := range map[string]any(*request.Body.ExtractorConfig) {
			extractorConfig[key] = value
		}
	}
	datasetKey := ""
	if request.Body.DatasetId != nil {
		datasetKey = *request.Body.DatasetId
	}
	payload := map[string]any{
		"dataset_id": datasetKey, "dataset_version_id": request.Body.DatasetVersionId,
		"backbone_id": backboneID, "extractor": extractor, "extractor_config": extractorConfig,
		"batch_size": featureBatchSize, "image_size": imageSize, "feature_pool": featurePool,
	}
	if request.Body.HeadConfig != nil {
		payload["head_config"] = map[string]any(*request.Body.HeadConfig)
	}
	if request.Body.TargetSelectiveRisk != nil {
		payload["target_selective_risk"] = *request.Body.TargetSelectiveRisk
	}
	if request.Body.ReviewCostPerItem != nil {
		payload["review_cost_per_item"] = *request.Body.ReviewCostPerItem
	}
	datasetID, datasetVersionID, resolvedDatasetKey, err := server.reads.ResolveDatasetScope(ctx, datasetKey, request.Body.DatasetVersionId)
	if err != nil {
		return openapi.CreateTrainingRun422JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, string(training.CodeValidationFailed), "dataset version is not ready or does not exist"))}, nil
	}
	datasetKey = resolvedDatasetKey
	payload["dataset_id"] = datasetKey
	created, err := server.lifecycle.Create(ctx, training.CreateCommand{
		DatasetID: datasetID, DatasetVersionID: datasetVersionID,
		BackboneID: backboneID, Payload: payload, MaxAttempts: maxAttempts,
	})
	if err != nil {
		return openapi.CreateTrainingRun422JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, string(training.ErrorCode(err)), err.Error()))}, nil
	}
	readModel := map[string]any{
		"id": created.TrainingRunID, "run_id": created.TrainingRunID, "job_id": created.JobID,
		"dataset_id": datasetKey, "dataset_version_id": request.Body.DatasetVersionId,
		"backbone_id": backboneID, "status": created.Status, "extractor_config": payload["extractor_config"],
		"head_config": payload["head_config"],
	}
	server.created.Store(created.TrainingRunID, readModel)
	return openapi.CreateTrainingRun202JSONResponse{TrainingRunResponseJSONResponse: openapi.TrainingRunResponseJSONResponse(openapi.TrainingRunEnvelope{TrainingRun: openapi.FreeFormObject(readModel)})}, nil
}

func (server *Server) GetTrainingRun(ctx context.Context, request openapi.GetTrainingRunRequestObject) (openapi.GetTrainingRunResponseObject, error) {
	item, err := server.trainingRun(ctx, request.RunId.String())
	if errors.Is(err, ErrReadModelNotFound) {
		return openapi.GetTrainingRun404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "NOT_FOUND", "training run not found"))}, nil
	}
	if err != nil {
		return nil, err
	}
	return openapi.GetTrainingRun200JSONResponse{TrainingRunResponseJSONResponse: openapi.TrainingRunResponseJSONResponse(openapi.TrainingRunEnvelope{TrainingRun: openapi.FreeFormObject(item)})}, nil
}

func (server *Server) PauseTrainingRun(ctx context.Context, request openapi.PauseTrainingRunRequestObject) (openapi.PauseTrainingRunResponseObject, error) {
	jobID, err := server.jobIDForRun(ctx, request.RunId.String())
	if err != nil {
		return openapi.PauseTrainingRun404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "NOT_FOUND", err.Error()))}, nil
	}
	if err := server.lifecycle.Pause(ctx, jobID); err != nil {
		return openapi.PauseTrainingRun409JSONResponse(errorEnvelope(ctx, string(training.ErrorCode(err)), err.Error())), nil
	}
	return openapi.PauseTrainingRun200JSONResponse{TrainingRunResponseJSONResponse: openapi.TrainingRunResponseJSONResponse(openapi.TrainingRunEnvelope{TrainingRun: openapi.FreeFormObject{"id": request.RunId.String(), "job_id": jobID, "status": "paused"}})}, nil
}

func (server *Server) ResumeTrainingRun(ctx context.Context, request openapi.ResumeTrainingRunRequestObject) (openapi.ResumeTrainingRunResponseObject, error) {
	jobID, err := server.jobIDForRun(ctx, request.RunId.String())
	if err != nil {
		return openapi.ResumeTrainingRun404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "NOT_FOUND", err.Error()))}, nil
	}
	if err := server.lifecycle.Resume(ctx, jobID); err != nil {
		return openapi.ResumeTrainingRun409JSONResponse(errorEnvelope(ctx, string(training.ErrorCode(err)), err.Error())), nil
	}
	return openapi.ResumeTrainingRun200JSONResponse{TrainingRunResponseJSONResponse: openapi.TrainingRunResponseJSONResponse(openapi.TrainingRunEnvelope{TrainingRun: openapi.FreeFormObject{"id": request.RunId.String(), "job_id": jobID, "status": "queued"}})}, nil
}

func (server *Server) CancelTrainingRun(ctx context.Context, request openapi.CancelTrainingRunRequestObject) (openapi.CancelTrainingRunResponseObject, error) {
	jobID, err := server.jobIDForRun(ctx, request.RunId.String())
	if err != nil {
		return openapi.CancelTrainingRun404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "NOT_FOUND", err.Error()))}, nil
	}
	if err := server.lifecycle.Cancel(ctx, jobID); err != nil {
		return openapi.CancelTrainingRun409JSONResponse(errorEnvelope(ctx, string(training.ErrorCode(err)), err.Error())), nil
	}
	return openapi.CancelTrainingRun200JSONResponse{TrainingRunResponseJSONResponse: openapi.TrainingRunResponseJSONResponse(openapi.TrainingRunEnvelope{TrainingRun: openapi.FreeFormObject{"id": request.RunId.String(), "job_id": jobID, "status": "cancelled"}})}, nil
}

func (server *Server) ClaimTrainingJob(ctx context.Context, request openapi.ClaimTrainingJobRequestObject) (openapi.ClaimTrainingJobResponseObject, error) {
	result, err := server.lifecycle.Claim(ctx, training.ClaimCommand{
		JobID: request.JobId.String(), DispatchGeneration: request.Body.DispatchGeneration, WorkerID: request.Body.WorkerId,
	})
	if err != nil {
		envelope := errorEnvelope(ctx, string(training.ErrorCode(err)), err.Error())
		if training.ErrorCode(err) == training.CodeNotFound {
			return openapi.ClaimTrainingJob404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(envelope)}, nil
		}
		return openapi.ClaimTrainingJob409JSONResponse(envelope), nil
	}
	claimResponse := openapi.ClaimResult{Disposition: openapi.ClaimResultDisposition(result.Disposition), JobId: uuid.MustParse(result.JobID)}
	if result.TrainingRunID != "" {
		value := uuid.MustParse(result.TrainingRunID)
		claimResponse.TrainingRunId = &value
	}
	if result.AttemptID != "" {
		value := uuid.MustParse(result.AttemptID)
		claimResponse.AttemptId = &value
		claimResponse.ExecutionEpoch = &result.ExecutionEpoch
		claimResponse.LeaseExpiresAt = &result.LeaseExpiresAt
		payload := openapi.FreeFormObject(result.Payload)
		claimResponse.Payload = &payload
	}
	return openapi.ClaimTrainingJob200JSONResponse{ClaimResponseJSONResponse: openapi.ClaimResponseJSONResponse(claimResponse)}, nil
}

func (server *Server) HeartbeatTrainingJob(ctx context.Context, request openapi.HeartbeatTrainingJobRequestObject) (openapi.HeartbeatTrainingJobResponseObject, error) {
	result, err := server.lifecycle.Heartbeat(ctx, training.HeartbeatCommand{
		JobID: request.JobId.String(), AttemptID: request.Body.AttemptId.String(), ExecutionEpoch: request.Body.ExecutionEpoch,
	})
	if err != nil {
		envelope := errorEnvelope(ctx, string(training.ErrorCode(err)), err.Error())
		if training.ErrorCode(err) == training.CodeNotFound {
			return openapi.HeartbeatTrainingJob404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(envelope)}, nil
		}
		return openapi.HeartbeatTrainingJob409JSONResponse(envelope), nil
	}
	return openapi.HeartbeatTrainingJob200JSONResponse{HeartbeatResponseJSONResponse: openapi.HeartbeatResponseJSONResponse(openapi.HeartbeatResult{Directive: openapi.HeartbeatResultDirective(result.Directive), LeaseExpiresAt: result.LeaseExpiresAt})}, nil
}

func (server *Server) ReportTrainingProgress(ctx context.Context, request openapi.ReportTrainingProgressRequestObject) (openapi.ReportTrainingProgressResponseObject, error) {
	err := server.lifecycle.Progress(ctx, training.ProgressCommand{
		JobID: request.JobId.String(), AttemptID: request.Body.AttemptId.String(), ExecutionEpoch: request.Body.ExecutionEpoch,
		Progress: map[string]any(request.Body.Progress),
	})
	if err != nil {
		envelope := errorEnvelope(ctx, string(training.ErrorCode(err)), err.Error())
		if training.ErrorCode(err) == training.CodeNotFound {
			return openapi.ReportTrainingProgress404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(envelope)}, nil
		}
		return openapi.ReportTrainingProgress409JSONResponse(envelope), nil
	}
	return openapi.ReportTrainingProgress204Response{}, nil
}

func (server *Server) CompleteTrainingJob(ctx context.Context, request openapi.CompleteTrainingJobRequestObject) (openapi.CompleteTrainingJobResponseObject, error) {
	descriptors := make([]artifact.Descriptor, 0, len(request.Body.Artifacts))
	for _, item := range request.Body.Artifacts {
		descriptor := artifact.Descriptor{
			ArtifactID: item.ArtifactId.String(), ArtifactType: item.ArtifactType, URI: item.Uri,
			SHA256: item.Sha256, SizeBytes: item.SizeBytes, ContentType: item.ContentType,
			StorageVersion: item.StorageVersion, Producer: item.Producer, SchemaVersion: int(item.SchemaVersion),
		}
		if item.DatasetVersionId != nil {
			descriptor.DatasetVersionID = *item.DatasetVersionId
		}
		if item.TrainingRunId != nil {
			descriptor.TrainingRunID = *item.TrainingRunId
		}
		if item.AttemptId != nil {
			descriptor.AttemptID = *item.AttemptId
		}
		if item.CreatedAt != nil {
			descriptor.CreatedAt = *item.CreatedAt
		}
		if item.VerifiedAt != nil {
			descriptor.VerifiedAt = *item.VerifiedAt
		}
		if item.Metadata != nil {
			descriptor.Metadata = map[string]any(*item.Metadata)
		}
		descriptors = append(descriptors, descriptor)
	}
	metrics := map[string]any{}
	if request.Body.Metrics != nil {
		metrics = map[string]any(*request.Body.Metrics)
	}
	result, err := server.lifecycle.Complete(ctx, training.CompleteCommand{
		JobID: request.JobId.String(), AttemptID: request.Body.AttemptId.String(), ExecutionEpoch: request.Body.ExecutionEpoch,
		CompletionKey: request.Body.CompletionKey, ResultDigest: request.Body.ResultDigest, Artifacts: descriptors, Metrics: metrics,
	})
	if err != nil {
		envelope := errorEnvelope(ctx, string(training.ErrorCode(err)), err.Error())
		switch training.ErrorCode(err) {
		case training.CodeNotFound:
			return openapi.CompleteTrainingJob404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(envelope)}, nil
		case training.CodeValidationFailed, training.CodeArtifactIntegrityFailed:
			return openapi.CompleteTrainingJob422JSONResponse(envelope), nil
		default:
			return openapi.CompleteTrainingJob409JSONResponse(envelope), nil
		}
	}
	return openapi.CompleteTrainingJob200JSONResponse{CompleteResponseJSONResponse: openapi.CompleteResponseJSONResponse(openapi.CompleteResult{
		JobId: uuid.MustParse(result.JobID), TrainingRunId: uuid.MustParse(result.TrainingRunID),
		ModelVersionId: uuid.MustParse(result.ModelVersionID), Metrics: openapi.FreeFormObject(result.Metrics),
	})}, nil
}

func (server *Server) FailTrainingJob(ctx context.Context, request openapi.FailTrainingJobRequestObject) (openapi.FailTrainingJobResponseObject, error) {
	err := server.lifecycle.Fail(ctx, training.FailCommand{
		JobID: request.JobId.String(), AttemptID: request.Body.AttemptId.String(), ExecutionEpoch: request.Body.ExecutionEpoch,
		Retryable: request.Body.Retryable, ErrorCode: request.Body.ErrorCode, ErrorMessage: request.Body.ErrorMessage,
	})
	if err != nil {
		envelope := errorEnvelope(ctx, string(training.ErrorCode(err)), err.Error())
		if training.ErrorCode(err) == training.CodeNotFound {
			return openapi.FailTrainingJob404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(envelope)}, nil
		}
		return openapi.FailTrainingJob409JSONResponse(envelope), nil
	}
	return openapi.FailTrainingJob204Response{}, nil
}

func (server *Server) trainingRun(ctx context.Context, runID string) (map[string]any, error) {
	item, err := server.reads.GetTrainingRun(ctx, runID)
	if err == nil {
		return item, nil
	}
	if value, ok := server.created.Load(runID); ok {
		return value.(map[string]any), nil
	}
	return nil, err
}

func (server *Server) jobIDForRun(ctx context.Context, runID string) (string, error) {
	jobID, err := server.reads.JobIDForRun(ctx, runID)
	if err == nil {
		return jobID, nil
	}
	if value, ok := server.created.Load(runID); ok {
		return value.(map[string]any)["job_id"].(string), nil
	}
	return "", ErrReadModelNotFound
}

func errorEnvelope(ctx context.Context, code, message string) openapi.ErrorEnvelope {
	if code == "" {
		code = "INTERNAL_ERROR"
	}
	requestID := middleware.GetReqID(ctx)
	if requestID == "" {
		requestID = "req_unknown"
	}
	return openapi.ErrorEnvelope{Error: openapi.Error{Code: code, Message: message, Details: openapi.FreeFormObject{}, RequestId: requestID}}
}

func freeFormList(items []map[string]any) []openapi.FreeFormObject {
	result := make([]openapi.FreeFormObject, len(items))
	for index, item := range items {
		result[index] = openapi.FreeFormObject(item)
	}
	return result
}

type emptyReadModels struct{}

func (emptyReadModels) ListDatasets(context.Context) ([]map[string]any, error) {
	return []map[string]any{}, nil
}
func (emptyReadModels) GetDataset(context.Context, string) (map[string]any, error) {
	return nil, ErrReadModelNotFound
}
func (emptyReadModels) ListJobs(context.Context) ([]map[string]any, error) {
	return []map[string]any{}, nil
}
func (emptyReadModels) GetJob(context.Context, string) (map[string]any, error) {
	return nil, ErrReadModelNotFound
}
func (emptyReadModels) ListTrainingRuns(context.Context) ([]map[string]any, error) {
	return []map[string]any{}, nil
}
func (emptyReadModels) GetTrainingRun(context.Context, string) (map[string]any, error) {
	return nil, ErrReadModelNotFound
}
func (emptyReadModels) JobIDForRun(context.Context, string) (string, error) {
	return "", ErrReadModelNotFound
}
func (emptyReadModels) ResolveDatasetScope(_ context.Context, datasetID, versionID string) (string, string, string, error) {
	if datasetID == "" {
		datasetID = "dataset-for-" + versionID
	}
	return datasetID, versionID, datasetID, nil
}

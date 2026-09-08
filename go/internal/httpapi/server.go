package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"unicode/utf8"

	"github.com/go-chi/chi/v5/middleware"
	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/go/api/openapi"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/datasetcard"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelcatalog"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
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
	GetTrainingRunMetrics(context.Context, string, MetricsQuery) (map[string]any, error)
	JobIDForRun(context.Context, string) (string, error)
	ResolveDatasetScope(context.Context, string, string) (string, string, string, error)
}

type MetricsQuery struct {
	AttemptID  string
	MetricName string
	AfterID    int64
	Limit      int
}

type Server struct {
	cards     *datasetcard.Service
	datasets  *dataset.Service
	lifecycle *training.Service
	reads     ReadModels
	llm       *llm.Application
	registry  *modelregistry.Service
	created   sync.Map
}

func NewServer(lifecycle *training.Service, reads ReadModels, llmApplication *llm.Application, registry *modelregistry.Service) *Server {
	return &Server{lifecycle: lifecycle, reads: reads, llm: llmApplication, registry: registry}
}

func (server *Server) ListModelVersions(ctx context.Context, request openapi.ListModelVersionsRequestObject) (openapi.ListModelVersionsResponseObject, error) {
	filter := modelregistry.Filter{}
	if request.Params.DatasetId != nil {
		filter.DatasetID = *request.Params.DatasetId
	}
	if request.Params.DatasetVersionId != nil {
		filter.DatasetVersionID = *request.Params.DatasetVersionId
	}
	if request.Params.Architecture != nil {
		filter.Architecture = *request.Params.Architecture
	}
	if request.Params.Pretraining != nil {
		filter.Pretraining = *request.Params.Pretraining
	}
	if request.Params.Status != nil {
		filter.Status = modelregistry.Status(*request.Params.Status)
	}
	versions, err := server.registry.List(ctx, filter)
	if err != nil {
		return nil, err
	}
	return openapi.ListModelVersions200JSONResponse(openapi.FreeFormObject{"model_versions": jsonValue(versions)}), nil
}

func (server *Server) GetModelVersion(ctx context.Context, request openapi.GetModelVersionRequestObject) (openapi.GetModelVersionResponseObject, error) {
	version, err := server.registry.Get(ctx, request.ModelVersionId.String())
	if errors.Is(err, modelregistry.ErrNotFound) {
		return openapi.GetModelVersion404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "NOT_FOUND", "model version not found"))}, nil
	}
	if err != nil {
		return nil, err
	}
	return openapi.GetModelVersion200JSONResponse(openapi.FreeFormObject{"model_version": jsonValue(version)}), nil
}

func (server *Server) CompareModelVersions(ctx context.Context, request openapi.CompareModelVersionsRequestObject) (openapi.CompareModelVersionsResponseObject, error) {
	if request.Body == nil {
		return openapi.CompareModelVersions422JSONResponse(errorEnvelope(ctx, "VALIDATION_FAILED", "request body is required")), nil
	}
	ids := make([]string, len(request.Body.ModelVersionIds))
	for index, id := range request.Body.ModelVersionIds {
		ids[index] = id.String()
	}
	comparison, err := server.registry.Compare(ctx, ids)
	if errors.Is(err, modelregistry.ErrNotFound) {
		return openapi.CompareModelVersions404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "NOT_FOUND", "model version not found"))}, nil
	}
	if errors.Is(err, modelregistry.ErrInvalid) {
		return openapi.CompareModelVersions422JSONResponse(errorEnvelope(ctx, "VALIDATION_FAILED", err.Error())), nil
	}
	if err != nil {
		return nil, err
	}
	return openapi.CompareModelVersions200JSONResponse(openapi.FreeFormObject{"comparison": jsonValue(comparison)}), nil
}

func (server *Server) PromoteModelVersion(ctx context.Context, request openapi.PromoteModelVersionRequestObject) (openapi.PromoteModelVersionResponseObject, error) {
	if request.Body == nil {
		return openapi.PromoteModelVersion422JSONResponse(errorEnvelope(ctx, "VALIDATION_FAILED", "request body is required")), nil
	}
	version, err := server.registry.Promote(ctx, request.ModelVersionId.String(), modelregistry.Status(request.Body.TargetStatus), request.Body.Actor, request.Body.Reason)
	if err != nil {
		if response := promoteError(ctx, err); response != nil {
			return response, nil
		}
		return nil, err
	}
	return openapi.PromoteModelVersion200JSONResponse(openapi.FreeFormObject{"model_version": jsonValue(version)}), nil
}

func promoteError(ctx context.Context, err error) openapi.PromoteModelVersionResponseObject {
	if err == nil {
		return nil
	}
	if errors.Is(err, modelregistry.ErrNotFound) {
		return openapi.PromoteModelVersion404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "NOT_FOUND", err.Error()))}
	}
	if errors.Is(err, modelregistry.ErrInvalid) {
		return openapi.PromoteModelVersion422JSONResponse(errorEnvelope(ctx, "VALIDATION_FAILED", err.Error()))
	}
	if errors.Is(err, modelregistry.ErrConflict) {
		return openapi.PromoteModelVersion409JSONResponse(errorEnvelope(ctx, "INVALID_STATE_TRANSITION", err.Error()))
	}
	return nil
}

func (server *Server) ArchiveModelVersion(ctx context.Context, request openapi.ArchiveModelVersionRequestObject) (openapi.ArchiveModelVersionResponseObject, error) {
	if request.Body == nil {
		return openapi.ArchiveModelVersion422JSONResponse(errorEnvelope(ctx, "VALIDATION_FAILED", "request body is required")), nil
	}
	version, err := server.registry.Archive(ctx, request.ModelVersionId.String(), request.Body.Actor, request.Body.Reason)
	if errors.Is(err, modelregistry.ErrNotFound) {
		return openapi.ArchiveModelVersion404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "NOT_FOUND", err.Error()))}, nil
	}
	if errors.Is(err, modelregistry.ErrInvalid) {
		return openapi.ArchiveModelVersion422JSONResponse(errorEnvelope(ctx, "VALIDATION_FAILED", err.Error())), nil
	}
	if errors.Is(err, modelregistry.ErrConflict) {
		return openapi.ArchiveModelVersion409JSONResponse(errorEnvelope(ctx, "INVALID_STATE_TRANSITION", err.Error())), nil
	}
	if err != nil {
		return nil, err
	}
	return openapi.ArchiveModelVersion200JSONResponse(openapi.FreeFormObject{"model_version": jsonValue(version)}), nil
}

func (server *Server) SetModelAlias(ctx context.Context, request openapi.SetModelAliasRequestObject) (openapi.SetModelAliasResponseObject, error) {
	if request.Body == nil {
		return openapi.SetModelAlias422JSONResponse(errorEnvelope(ctx, "VALIDATION_FAILED", "request body is required")), nil
	}
	result, err := server.registry.SetAlias(ctx, request.Body.DatasetId, request.Alias, request.Body.ModelVersionId.String(), request.Body.Actor, request.Body.Reason)
	if errors.Is(err, modelregistry.ErrNotFound) {
		return openapi.SetModelAlias404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "NOT_FOUND", err.Error()))}, nil
	}
	if errors.Is(err, modelregistry.ErrInvalid) {
		return openapi.SetModelAlias422JSONResponse(errorEnvelope(ctx, "VALIDATION_FAILED", err.Error())), nil
	}
	if errors.Is(err, modelregistry.ErrConflict) {
		return openapi.SetModelAlias409JSONResponse(errorEnvelope(ctx, "MODEL_SCOPE_CONFLICT", err.Error())), nil
	}
	if err != nil {
		return nil, err
	}
	return openapi.SetModelAlias200JSONResponse(openapi.FreeFormObject{"model_alias": jsonValue(result)}), nil
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
	items := make([]map[string]any, 0, len(modelcatalog.Approved()))
	for _, backbone := range modelcatalog.Approved() {
		items = append(items, managedWeight(backbone))
	}
	return openapi.ListModelWeights200JSONResponse{Weights: freeFormList(items)}, nil
}

func managedWeight(backbone modelcatalog.Backbone) map[string]any {
	category := "imagenet/vit-small"
	if backbone.Key == modelcatalog.DINOv3ViTSKey {
		category = "dinov3"
	} else if backbone.Key == modelcatalog.ResNet50Key {
		category = "imagenet/resnet-50"
	}
	return map[string]any{
		"preset": backbone.Key, "extractor": backbone.LegacyExtractor, "backbone_key": backbone.Key,
		"backbone_id": backbone.Key, "display_name": backbone.DisplayName, "architecture": backbone.Architecture,
		"model_name": backbone.ModelName, "repo_id": backbone.ModelID, "revision": backbone.Revision,
		"pretraining_method": backbone.PretrainingMethod, "pretraining_dataset": backbone.PretrainingDataset,
		"input_size": backbone.InputSize, "feature_dim": backbone.FeatureDim, "parameter_count": backbone.ParameterCount,
		"pooling": backbone.Pooling, "license": backbone.License, "license_url": backbone.LicenseURL,
		"state": "managed", "cache_status": "managed", "cached": true, "cache_bytes": backbone.SizeBytes,
		"complete_size_bytes": backbone.SizeBytes, "complete_file_count": 1, "partial_bytes": 0,
		"incomplete_file_count": 0, "sha256": backbone.SHA256, "size_bytes": backbone.SizeBytes,
		"cache_dir":     "s3://finevision-artifacts/pretrained/" + category + "/" + backbone.SHA256[:2] + "/" + backbone.SHA256,
		"download_hint": "Git LFS release weight is verified and mirrored into the S3-compatible ArtifactStore.",
		"description":   backbone.DisplayName + "，冻结特征提取后训练轻量分类头。",
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
	backbone, colorStats, err := resolveTrainingBackbone(request.Body)
	if err != nil {
		return openapi.CreateTrainingRun422JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, string(training.CodeValidationFailed), err.Error()))}, nil
	}
	extractor := backbone.LegacyExtractor
	backboneID := backbone.Key
	if colorStats {
		extractor = "color_stats"
		backboneID = "color_stats_v1"
	}
	maxAttempts := 3
	if request.Body.MaxAttempts != nil {
		maxAttempts = *request.Body.MaxAttempts
	}
	featureBatchSize := 8
	if request.Body.FeatureBatchSize != nil {
		featureBatchSize = *request.Body.FeatureBatchSize
	}
	featurePool := backbone.Pooling
	if colorStats {
		featurePool = "model"
	}
	if request.Body.FeaturePool != nil {
		featurePool = string(*request.Body.FeaturePool)
	}
	imageSize := 0
	if request.Body.ImageSize != nil {
		imageSize = *request.Body.ImageSize
	} else if !colorStats {
		imageSize = backbone.InputSize
	}
	if !colorStats && backbone.Architecture == "vit_small_patch16" && imageSize%16 != 0 {
		return openapi.CreateTrainingRun422JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, string(training.CodeValidationFailed), "image_size must be divisible by 16 for ViT-S/16 backbones"))}, nil
	}
	extractorConfig := map[string]any{"type": extractor, "backbone_id": backboneID}
	if !colorStats {
		extractorConfig = map[string]any{
			"type": "timm", "preset": backbone.Key, "backbone_key": backbone.Key, "model_name": backbone.ModelName,
			"pretrained": true, "backbone_id": backboneID, "feature_pool": featurePool, "image_size": imageSize,
			"architecture": backbone.Architecture, "pretraining_method": backbone.PretrainingMethod,
			"pretraining_dataset": backbone.PretrainingDataset, "feature_dim": backbone.FeatureDim,
			"parameter_count": backbone.ParameterCount, "pretrained_sha256": backbone.SHA256,
		}
	}
	if request.Body.ExtractorConfig != nil {
		for key, value := range map[string]any(*request.Body.ExtractorConfig) {
			if !colorStats && immutableExtractorField(key) {
				if current, exists := extractorConfig[key]; !exists || current != value {
					return openapi.CreateTrainingRun422JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, string(training.CodeValidationFailed), "extractor_config cannot override managed backbone identity: "+key))}, nil
				}
			}
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
	if request.Body.Name != nil {
		name := strings.TrimSpace(*request.Body.Name)
		if name == "" || utf8.RuneCountInString(name) > 80 || strings.ContainsAny(name, "\n\r\x00") {
			return openapi.CreateTrainingRun422JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "VALIDATION_FAILED", "训练任务名须为 1–80 个字符，不能包含换行"))}, nil
		}
		payload["name"] = name
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
		"name": payload["name"],
		"id":   created.TrainingRunID, "run_id": created.TrainingRunID, "job_id": created.JobID,
		"dataset_id": datasetKey, "dataset_version_id": request.Body.DatasetVersionId,
		"backbone_id": backboneID, "status": created.Status, "extractor_config": payload["extractor_config"],
		"head_config": payload["head_config"],
	}
	server.created.Store(created.TrainingRunID, readModel)
	return openapi.CreateTrainingRun202JSONResponse{TrainingRunResponseJSONResponse: openapi.TrainingRunResponseJSONResponse(openapi.TrainingRunEnvelope{TrainingRun: openapi.FreeFormObject(readModel)})}, nil
}

func resolveTrainingBackbone(request *openapi.CreateTrainingRun) (modelcatalog.Backbone, bool, error) {
	key := modelcatalog.DINOv3ViTSKey
	if request.BackboneKey != nil {
		key = string(*request.BackboneKey)
	} else if request.Extractor != nil {
		key = string(*request.Extractor)
	} else if request.BackboneId != nil && *request.BackboneId != "" {
		key = *request.BackboneId
	}
	if key == "color_stats" || key == "color_stats_v1" {
		return modelcatalog.Backbone{}, true, nil
	}
	if key == "dinov3_vits16" {
		key = modelcatalog.DINOv3ViTSKey
	}
	backbone, exists := modelcatalog.Resolve(key)
	if !exists {
		return modelcatalog.Backbone{}, false, errors.New("backbone_key is not approved for Phase 2")
	}
	return backbone, false, nil
}

func immutableExtractorField(key string) bool {
	switch key {
	case "type", "preset", "backbone_key", "model_name", "pretrained", "backbone_id", "architecture",
		"pretraining_method", "pretraining_dataset", "feature_dim", "parameter_count", "pretrained_sha256", "checkpoint_path":
		return true
	default:
		return false
	}
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

func (server *Server) GetTrainingRunMetrics(ctx context.Context, request openapi.GetTrainingRunMetricsRequestObject) (openapi.GetTrainingRunMetricsResponseObject, error) {
	query := MetricsQuery{Limit: 1000}
	if request.Params.AttemptId != nil {
		query.AttemptID = request.Params.AttemptId.String()
	}
	if request.Params.MetricName != nil {
		query.MetricName = *request.Params.MetricName
	}
	if request.Params.AfterId != nil {
		query.AfterID = *request.Params.AfterId
	}
	if request.Params.Limit != nil {
		query.Limit = *request.Params.Limit
	}
	payload, err := server.reads.GetTrainingRunMetrics(ctx, request.RunId.String(), query)
	if errors.Is(err, ErrReadModelNotFound) {
		return openapi.GetTrainingRunMetrics404JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "NOT_FOUND", "training run not found"))}, nil
	}
	if err != nil {
		return nil, err
	}
	return openapi.GetTrainingRunMetrics200JSONResponse(openapi.FreeFormObject(payload)), nil
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
	metricPoints := make([]training.MetricPoint, 0)
	if request.Body.MetricPoints != nil {
		metricPoints = make([]training.MetricPoint, 0, len(*request.Body.MetricPoints))
		for _, item := range *request.Body.MetricPoints {
			point := training.MetricPoint{Name: item.Name, Step: item.Step, Value: item.Value}
			if item.RecordedAt != nil {
				point.RecordedAt = *item.RecordedAt
			}
			if item.Context != nil {
				point.Context = map[string]any(*item.Context)
			}
			metricPoints = append(metricPoints, point)
		}
	}
	err := server.lifecycle.Progress(ctx, training.ProgressCommand{
		JobID: request.JobId.String(), AttemptID: request.Body.AttemptId.String(), ExecutionEpoch: request.Body.ExecutionEpoch,
		Progress: map[string]any(request.Body.Progress), MetricPoints: metricPoints,
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

func jsonValue(value any) any {
	encoded, err := json.Marshal(value)
	if err != nil {
		return nil
	}
	var result any
	if err := json.Unmarshal(encoded, &result); err != nil {
		return nil
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
func (emptyReadModels) GetTrainingRunMetrics(context.Context, string, MetricsQuery) (map[string]any, error) {
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

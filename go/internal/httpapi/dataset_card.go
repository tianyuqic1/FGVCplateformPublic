package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/go/api/openapi"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/datasetcard"
)

func cardError(ctx context.Context, err error) (int, openapi.ErrorEnvelope) {
	status, code, message := 503, "DATASET_CARD_UNAVAILABLE", "数据集说明服务不可用，请稍后重试"
	switch {
	case errors.Is(err, datasetcard.ErrNotFound):
		status, code, message = 404, "DATASET_NOT_FOUND", "数据集版本不存在"
	case errors.Is(err, datasetcard.ErrConflict):
		status, code, message = 409, "CARD_CONFLICT", "卡片修订已变化、草稿已过期或正在生成；请刷新后重试"
	case errors.Is(err, datasetcard.ErrInvalid):
		status, code, message = 422, "INVALID_DATASET_CARD", "卡片字段无效，或类别清单为空/上下文过长"
	case errors.Is(err, datasetcard.ErrGeneration):
		code, message = "LLM_GENERATION_FAILED", "AI 草稿生成失败，原卡片未修改；请检查模型、密钥、余额或稍后重试"
	}
	return status, errorEnvelope(ctx, code, message)
}
func cardMap(value any) map[string]any {
	data, _ := json.Marshal(value)
	var result map[string]any
	_ = json.Unmarshal(data, &result)
	return result
}
func (s *Server) GetDatasetCard(ctx context.Context, r openapi.GetDatasetCardRequestObject) (openapi.GetDatasetCardResponseObject, error) {
	if s.cards == nil {
		status, body := cardError(ctx, nil)
		return openapi.GetDatasetCarddefaultJSONResponse{StatusCode: status, Body: body}, nil
	}
	state, err := s.cards.Get(ctx, r.DatasetVersionId)
	if err != nil {
		status, body := cardError(ctx, err)
		return openapi.GetDatasetCarddefaultJSONResponse{StatusCode: status, Body: body}, nil
	}
	return openapi.GetDatasetCard200JSONResponse(cardMap(state)), nil
}
func (s *Server) SaveDatasetCard(ctx context.Context, r openapi.SaveDatasetCardRequestObject) (openapi.SaveDatasetCardResponseObject, error) {
	var state datasetcard.State
	err := datasetcard.ErrInvalid
	if s.cards != nil && r.Body != nil {
		c := r.Body.DatasetCard
		draft := ""
		if r.Body.DraftId != nil {
			draft = r.Body.DraftId.String()
		}
		state, err = s.cards.Save(ctx, r.DatasetVersionId, r.Body.ExpectedRevision, datasetcard.Card{Task: string(c.Task), Domain: c.Domain, Summary: c.Summary, KnownConfusions: c.KnownConfusions, OODPolicy: c.OodPolicy, ReviewGuidance: c.ReviewGuidance}, draft)
	}
	if err != nil {
		status, body := cardError(ctx, err)
		return openapi.SaveDatasetCarddefaultJSONResponse{StatusCode: status, Body: body}, nil
	}
	return openapi.SaveDatasetCard200JSONResponse(cardMap(state)), nil
}
func (s *Server) GenerateDatasetCard(ctx context.Context, r openapi.GenerateDatasetCardRequestObject) (openapi.GenerateDatasetCardResponseObject, error) {
	var generation datasetcard.Generation
	err := datasetcard.ErrInvalid
	if s.cards != nil && r.Body != nil && r.Body.RequestId != uuid.Nil {
		generation, err = s.cards.Generate(ctx, r.DatasetVersionId, r.Body.RequestId.String())
	}
	if err != nil {
		status, body := cardError(ctx, err)
		return openapi.GenerateDatasetCarddefaultJSONResponse{StatusCode: status, Body: body}, nil
	}
	return openapi.GenerateDatasetCard200JSONResponse{"generation": generation}, nil
}

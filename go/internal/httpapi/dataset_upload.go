package httpapi

import (
	"archive/zip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/tianyuqic1/FGVCplateformPublic/go/api/openapi"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
	"io"
	"log/slog"
	"mime"
	"mime/multipart"
	"os"
	"path"
	"strings"
)

func readDatasetUpload(body *multipart.Reader) (map[string]string, string, error) {
	invalid := func(message string) (map[string]string, string, error) {
		return nil, "", fmt.Errorf("%w: %s", dataset.ErrInvalid, message)
	}
	if body == nil {
		return invalid("请选择数据")
	}
	success := false
	file, err := os.CreateTemp("", "finevision-upload-*.zip")
	if err != nil {
		return nil, "", err
	}
	defer func() {
		if !success {
			os.Remove(file.Name())
		}
	}()
	defer file.Close()
	writer := zip.NewWriter(file)
	defer writer.Close()
	fields := map[string]string{}
	seen := map[string]bool{}
	count := 0
	var total int64
	for {
		part, err := body.NextPart()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return invalid("上传数据不完整或超过 5 GB")
		}
		_, params, err := mime.ParseMediaType(part.Header.Get("Content-Disposition"))
		if err != nil {
			return invalid("无效的上传字段")
		}
		filename := params["filename"]
		if filename == "" {
			data, err := io.ReadAll(io.LimitReader(part, 512*1024+1))
			part.Close()
			if err != nil || len(data) > 512*1024 {
				return invalid("字段过长")
			}
			fields[part.FormName()] = strings.TrimSpace(string(data))
			continue
		}
		if strings.Contains(filename, "\\") || strings.HasPrefix(filename, "/") {
			return invalid("图片路径必须是文件夹相对路径")
		}
		parts := strings.Split(filename, "/")
		for _, p := range parts {
			if p == ".." || p == "." || p == "" {
				return invalid("图片路径包含非法目录")
			}
		}
		switch strings.ToLower(path.Ext(filename)) {
		case ".png", ".jpg", ".jpeg", ".webp", ".bmp":
		default:
			part.Close()
			continue
		}
		// Browser folder uploads include the selected root; strip exactly that component.
		if len(parts) >= 3 && parts[0] != "train" && parts[0] != "val" && parts[0] != "test" {
			filename = strings.Join(parts[1:], "/")
		}
		if len(strings.Split(filename, "/")) < 2 {
			return invalid("请保留 类别/图片 的 ImageFolder 目录结构")
		}
		if seen[filename] {
			return invalid("存在重复图片路径")
		}
		seen[filename] = true
		count++
		if count > dataset.MaxUploadImages {
			return invalid("最多支持 100000 张图片")
		}
		destination, err := writer.Create(filename)
		if err != nil {
			return nil, "", err
		}
		n, err := io.Copy(destination, io.LimitReader(part, (32<<20)+1))
		part.Close()
		total += n
		if err != nil || n > 32<<20 || total > dataset.MaxUploadBytes {
			return invalid("单张图片上限 32 MiB，数据集上限 5 GB")
		}
	}

	if err = writer.Close(); err != nil {
		return nil, "", err
	}
	if err = file.Close(); err != nil {
		return nil, "", err
	}
	if count == 0 {
		return fields, "", nil
	}
	success = true
	return fields, file.Name(), nil
}

func datasetError(ctx context.Context, err error) (int, openapi.ErrorEnvelope) {
	status, code, message := 503, "DATASET_IMPORT_FAILED", "数据集扫描或存储失败，请检查计算服务"
	switch {
	case errors.Is(err, dataset.ErrNotFound):
		status, code, message = 404, "DATASET_NOT_FOUND", err.Error()
	case errors.Is(err, dataset.ErrConflict):
		status, code, message = 409, "DATASET_VERSION_CONFLICT", err.Error()
	case errors.Is(err, dataset.ErrInvalid), errors.Is(err, dataset.ErrInvalidArchive):
		status, code, message = 422, "INVALID_DATASET", err.Error()
	default:
		slog.ErrorContext(ctx, "dataset request failed", "error", err)
	}
	return status, errorEnvelope(ctx, code, message)
}
func (s *Server) UploadImagefolder(ctx context.Context, r openapi.UploadImagefolderRequestObject) (openapi.UploadImagefolderResponseObject, error) {
	fields, archive, err := readDatasetUpload(r.Body)
	if archive != "" {
		defer os.Remove(archive)
	}
	var result map[string]any
	if err == nil && s.datasets != nil {
		result, err = s.datasets.Import(ctx, fields["name"], fields["request_id"], archive)
	} else if err == nil {
		err = errors.New("dataset service unavailable")
	}
	if err != nil {
		status, body := datasetError(ctx, err)
		switch status {
		case 422:
			return openapi.UploadImagefolder422JSONResponse(body), nil
		case 409:
			return openapi.UploadImagefolder409JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(body)}, nil
		default:
			return openapi.UploadImagefolder503JSONResponse(body), nil
		}
	}
	return openapi.UploadImagefolder201JSONResponse(result), nil
}
func (s *Server) ExpandDataset(ctx context.Context, r openapi.ExpandDatasetRequestObject) (openapi.ExpandDatasetResponseObject, error) {
	fields, archive, err := readDatasetUpload(r.Body)
	if archive != "" {
		defer os.Remove(archive)
	}
	var result map[string]any
	var feedbackIDs []string
	if err == nil && fields["feedback_ids"] != "" {
		if json.Unmarshal([]byte(fields["feedback_ids"]), &feedbackIDs) != nil {
			err = fmt.Errorf("%w: feedback_ids 必须是数组", dataset.ErrInvalid)
		}
	}
	if err == nil && s.datasets != nil {
		result, err = s.datasets.Expand(ctx, r.DatasetId, fields["base_version_id"], fields["request_id"], archive, feedbackIDs)
	} else if err == nil {
		err = errors.New("dataset service unavailable")
	}
	if err != nil {
		status, body := datasetError(ctx, err)
		return openapi.ExpandDatasetdefaultJSONResponse{StatusCode: status, Body: body}, nil
	}
	return openapi.ExpandDataset201JSONResponse(result), nil
}
func (s *Server) ListTrainingCandidates(ctx context.Context, r openapi.ListTrainingCandidatesRequestObject) (openapi.ListTrainingCandidatesResponseObject, error) {
	if s.datasets == nil {
		status, body := datasetError(ctx, errors.New("dataset service unavailable"))
		return openapi.ListTrainingCandidatesdefaultJSONResponse{StatusCode: status, Body: body}, nil
	}
	candidates, err := s.datasets.Repository.Candidates(ctx, r.DatasetId)
	if err != nil {
		status, body := datasetError(ctx, err)
		return openapi.ListTrainingCandidatesdefaultJSONResponse{StatusCode: status, Body: body}, nil
	}
	return openapi.ListTrainingCandidates200JSONResponse{"candidates": candidates}, nil
}

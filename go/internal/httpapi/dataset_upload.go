package httpapi

import (
	"archive/zip"
	"context"
	"errors"
	"github.com/tianyuqic1/FGVCplateformPublic/go/api/openapi"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
	"io"
	"log/slog"
	"mime"
	"os"
	"path"
	"strings"
)

func (s *Server) UploadImagefolder(ctx context.Context, r openapi.UploadImagefolderRequestObject) (openapi.UploadImagefolderResponseObject, error) {
	invalid := func(message string) (openapi.UploadImagefolderResponseObject, error) {
		return openapi.UploadImagefolder422JSONResponse(errorEnvelope(ctx, "INVALID_IMAGEFOLDER", message)), nil
	}
	if r.Body == nil {
		return invalid("请选择 ImageFolder 文件夹")
	}
	if s.datasets == nil {
		return openapi.UploadImagefolder503JSONResponse(errorEnvelope(ctx, "DATASET_SERVICE_UNAVAILABLE", "数据集服务不可用")), nil
	}
	file, err := os.CreateTemp("", "finevision-upload-*.zip")
	if err != nil {
		return nil, err
	}
	defer os.Remove(file.Name())
	defer file.Close()
	writer := zip.NewWriter(file)
	defer writer.Close()
	fields := map[string]string{}
	seen := map[string]bool{}
	count := 0
	var total int64
	for {
		part, err := r.Body.NextPart()
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
			data, err := io.ReadAll(io.LimitReader(part, 1025))
			part.Close()
			if err != nil || len(data) > 1024 {
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
			return nil, err
		}
		n, err := io.Copy(destination, io.LimitReader(part, (32<<20)+1))
		part.Close()
		total += n
		if err != nil || n > 32<<20 || total > dataset.MaxUploadBytes {
			return invalid("单张图片上限 32 MiB，数据集上限 5 GB")
		}
	}
	if count == 0 {
		return invalid("未找到图片，请选择包含类别子文件夹的 ImageFolder")
	}
	if fields["dataset_id"] == "" || fields["dataset_version_id"] == "" {
		return invalid("请填写数据集名称和版本")
	}
	if err = writer.Close(); err != nil {
		return nil, err
	}
	if err = file.Close(); err != nil {
		return nil, err
	}
	result, err := s.datasets.Import(ctx, fields["dataset_id"], fields["dataset_version_id"], file.Name())
	if errors.Is(err, dataset.ErrInvalidArchive) {
		return invalid("图片损坏或 ImageFolder 目录结构不合法")
	}
	if errors.Is(err, dataset.ErrConflict) {
		return openapi.UploadImagefolder409JSONResponse{ErrorResponseJSONResponse: openapi.ErrorResponseJSONResponse(errorEnvelope(ctx, "DATASET_VERSION_EXISTS", "该数据集版本已存在，请填写新的版本号"))}, nil
	}
	if err != nil {
		slog.ErrorContext(ctx, "dataset import failed", "error", err)
		return openapi.UploadImagefolder503JSONResponse(errorEnvelope(ctx, "DATASET_IMPORT_FAILED", "数据集扫描或存储失败，请检查图片文件与计算服务")), nil
	}
	return openapi.UploadImagefolder201JSONResponse(result), nil
}

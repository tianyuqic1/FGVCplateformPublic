package httpapi

import (
	"context"
	"encoding/json"
	"github.com/go-chi/chi/v5"
	"io"
	"math"
	"net/http"
	"strconv"
)

type InferenceApplication interface {
	Predict(context.Context, map[string]any, []byte) (map[string]any, error)
	Image(context.Context, string) ([]byte, string, error)
}

func registerInference(router chi.Router, service InferenceApplication) {
	available := func(w http.ResponseWriter) bool {
		if service == nil {
			reviewResponse(w, 503, map[string]any{"detail": "推理服务未配置"})
			return false
		}
		return true
	}
	router.Post("/api/inference", func(w http.ResponseWriter, r *http.Request) {
		if !available(w) {
			return
		}
		input := map[string]any{}
		decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16384))
		if err := decoder.Decode(&input); err != nil || input == nil {
			reviewResponse(w, 422, map[string]any{"detail": "推理参数无效"})
			return
		}
		if decoder.Decode(new(any)) != io.EOF {
			reviewResponse(w, 422, map[string]any{"detail": "推理参数无效"})
			return
		}
		result, err := service.Predict(r.Context(), input, nil)
		if err != nil {
			reviewError(w, err)
			return
		}
		reviewResponse(w, 200, map[string]any{"inference_result": result})
	})
	for _, folder := range []bool{false, true} {
		path := "/api/inference/upload"
		if folder {
			path += "-folder"
		}
		router.Post(path, func(w http.ResponseWriter, r *http.Request) {
			if !available(w) {
				return
			}
			r.Body = http.MaxBytesReader(w, r.Body, 128<<20)
			if err := r.ParseMultipartForm(8 << 20); err != nil {
				reviewResponse(w, 422, map[string]any{"detail": "图片上传无效或超过 128 MiB"})
				return
			}
			defer r.MultipartForm.RemoveAll()
			input := map[string]any{"dataset_version_id": r.FormValue("dataset_version_id"), "model_version_id": r.FormValue("model_version_id")}
			input["deployment_id"] = r.FormValue("deployment_id")
			for _, key := range []string{"top_k", "evidence_k", "accept_threshold", "margin_threshold", "ood_distance_threshold"} {
				if raw := r.FormValue(key); raw != "" {
					n, err := strconv.ParseFloat(raw, 64)
					if err != nil || math.IsNaN(n) || math.IsInf(n, 0) {
						reviewResponse(w, 422, map[string]any{"detail": "数值参数无效"})
						return
					}
					input[key] = n
				}
			}
			key := "image"
			if folder {
				key = "images"
				input["force_review"] = r.FormValue("route_all_to_review") != "false"
			}
			files := r.MultipartForm.File[key]
			if len(files) == 0 || len(files) > 100 || (!folder && len(files) != 1) {
				reviewResponse(w, 422, map[string]any{"detail": "请选择 1–100 张图片"})
				return
			}
			results, failures := []map[string]any{}, []map[string]any{}
			for _, file := range files {
				if file.Size > 20<<20 {
					failures = append(failures, map[string]any{"filename": file.Filename, "error": "图片超过 20 MiB"})
					continue
				}
				f, err := file.Open()
				if err != nil {
					reviewError(w, err)
					return
				}
				data, err := io.ReadAll(io.LimitReader(f, (20<<20)+1))
				f.Close()
				if err != nil {
					reviewError(w, err)
					return
				}
				result, err := service.Predict(r.Context(), input, data)
				if err != nil {
					if !folder {
						reviewError(w, err)
						return
					}
					failures = append(failures, map[string]any{"filename": file.Filename, "error": "推理失败"})
					continue
				}
				results = append(results, result)
			}
			if !folder {
				if len(results) == 0 {
					reviewResponse(w, 422, map[string]any{"detail": "图片无效"})
					return
				}
				reviewResponse(w, 200, map[string]any{"inference_result": results[0]})
				return
			}
			ids := []string{}
			for _, result := range results {
				if id, ok := result["review_item_id"].(string); ok && id != "" {
					ids = append(ids, id)
				}
			}
			reviewResponse(w, 200, map[string]any{"results": results, "failures": failures, "batch": map[string]any{"total": len(files), "succeeded": len(results), "failed": len(failures), "review_item_ids": ids, "review_item_count": len(ids), "route_all_to_review": input["force_review"]}})
		})
	}
	router.Get("/api/uploads/{image_id}", func(w http.ResponseWriter, r *http.Request) {
		if !available(w) {
			return
		}
		data, kind, err := service.Image(r.Context(), chi.URLParam(r, "image_id"))
		if err != nil {
			reviewError(w, err)
			return
		}
		w.Header().Set("Content-Type", kind)
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Write(data)
	})
}

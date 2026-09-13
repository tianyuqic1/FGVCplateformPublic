package httpapi

import (
	"encoding/json"
	"errors"
	"github.com/go-chi/chi/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
	"net/http"
	"net/url"
	"strconv"
)

func registerDatasetPreviews(router chi.Router, service *dataset.Service) {
	var previews *dataset.PreviewService
	if service != nil {
		if repository, ok := service.Repository.(dataset.PreviewRepository); ok {
			previews = &dataset.PreviewService{Repository: repository, Store: service.Store}
		}
	}
	fail := func(w http.ResponseWriter, err error) {
		status, message := 503, "样本服务暂不可用"
		if errors.Is(err, dataset.ErrNotFound) {
			status, message = 404, "数据版本或样本不存在"
		}
		if errors.Is(err, dataset.ErrInvalidArchive) {
			status, message = 422, "样本归档或图片校验失败"
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		json.NewEncoder(w).Encode(map[string]any{"error": map[string]string{"message": message}})
	}
	router.Get("/api/dataset-versions/{dataset_version_id}/sample-previews", func(w http.ResponseWriter, r *http.Request) {
		if previews == nil {
			fail(w, errors.New("unavailable"))
			return
		}
		limit := 6
		if raw := r.URL.Query().Get("limit"); raw != "" {
			n, err := strconv.Atoi(raw)
			if err != nil || n < 1 || n > 24 {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(422)
				json.NewEncoder(w).Encode(map[string]any{"error": map[string]string{"message": "limit 必须在 1–24 之间"}})
				return
			}
			limit = n
		}
		snapshot, samples, err := previews.Snapshot(r.Context(), chi.URLParam(r, "dataset_version_id"))
		if err != nil {
			fail(w, err)
			return
		}
		output := []map[string]string{}
		// Round-robin by class avoids previews consisting entirely of the first label.
		groups := map[string][]dataset.PreviewSample{}
		labels := []string{}
		for _, sample := range samples {
			if _, ok := groups[sample.Label]; !ok {
				labels = append(labels, sample.Label)
			}
			groups[sample.Label] = append(groups[sample.Label], sample)
		}
		for index := 0; len(output) < limit; index++ {
			added := false
			for _, label := range labels {
				if index >= len(groups[label]) {
					continue
				}
				sample := groups[label][index]
				added = true
				output = append(output, map[string]string{"sample_id": sample.ID, "label": sample.Label, "split": sample.Split, "image_url": "/api/dataset-versions/" + url.PathEscape(snapshot.VersionID) + "/samples/" + url.PathEscape(sample.ID) + "/image"})
				if len(output) == limit {
					break
				}
			}
			if !added {
				break
			}
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"dataset_id": snapshot.DatasetKey, "dataset_version_id": snapshot.VersionID, "samples": output})
	})
	router.Get("/api/dataset-versions/{dataset_version_id}/samples/{sample_id}/image", func(w http.ResponseWriter, r *http.Request) {
		if previews == nil {
			fail(w, errors.New("unavailable"))
			return
		}
		data, kind, err := previews.Image(r.Context(), chi.URLParam(r, "dataset_version_id"), chi.URLParam(r, "sample_id"))
		if err != nil {
			fail(w, err)
			return
		}
		w.Header().Set("Content-Type", kind)
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Cache-Control", "private, max-age=3600")
		w.Header().Set("Content-Length", strconv.Itoa(len(data)))
		w.Write(data)
	})
}

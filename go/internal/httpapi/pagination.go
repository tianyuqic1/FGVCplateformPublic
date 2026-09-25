package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
)

type PageFilter struct {
	Query, Status, DatasetID, BackboneID string
	Limit, Offset                        int
}

func registerTrainingSummary(router chi.Router, reads ReadModels) {
	router.Get("/api/training-runs/summary", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if summary, ok := reads.(interface {
			TrainingRunStatusCounts(context.Context) (map[string]int, error)
		}); ok {
			counts, err := summary.TrainingRunStatusCounts(r.Context())
			if err == nil {
				_ = json.NewEncoder(w).Encode(map[string]any{"counts": counts})
				return
			}
		}
		items, err := reads.ListTrainingRuns(r.Context())
		if err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			_ = json.NewEncoder(w).Encode(map[string]any{"error": "训练摘要暂不可用"})
			return
		}
		counts := map[string]int{}
		for _, item := range items {
			if status, ok := item["status"].(string); ok {
				counts[status]++
			}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"counts": counts})
	})
}

type PagedReadModels interface {
	ListDatasetsPage(context.Context, PageFilter) ([]map[string]any, int, error)
	ListTrainingRunsPage(context.Context, PageFilter) ([]map[string]any, int, error)
}

func pageFilter(q, status, datasetID, backboneID *string, limit, offset *int) PageFilter {
	filter := PageFilter{}
	if q != nil {
		filter.Query = strings.TrimSpace(*q)
	}
	if status != nil {
		filter.Status = *status
	}
	if datasetID != nil {
		filter.DatasetID = *datasetID
	}
	if backboneID != nil {
		filter.BackboneID = *backboneID
	}
	if limit != nil {
		filter.Limit = *limit
	}
	if offset != nil {
		filter.Offset = *offset
	}
	filter.Limit, filter.Offset = normalizePageBounds(filter.Limit, filter.Offset)
	return filter
}

func normalizePageBounds(limit, offset int) (int, int) {
	if limit < 1 {
		limit = 1
	} else if limit > 100 {
		limit = 100
	}
	if offset < 0 {
		offset = 0
	}
	return limit, offset
}

func pageWindow(total, limit, offset int) (int, int) {
	if offset > total {
		offset = total
	}
	end := offset + limit
	if end > total {
		end = total
	}
	return offset, end
}

func paginationValue(total, limit, offset int) map[string]any {
	return map[string]any{"total": total, "limit": limit, "offset": offset, "has_more": offset+limit < total}
}

func valueOrZero(value *int) int {
	if value == nil {
		return 0
	}
	return *value
}

package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"unicode/utf8"

	"github.com/go-chi/chi/v5"
)

type SearchReadModels interface {
	Search(context.Context, string, int) ([]map[string]any, error)
}

func registerSearch(router chi.Router, reads ReadModels) {
	router.Get("/api/search", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		query := strings.TrimSpace(r.URL.Query().Get("q"))
		if utf8.RuneCountInString(query) < 2 || utf8.RuneCountInString(query) > 100 {
			w.WriteHeader(http.StatusUnprocessableEntity)
			_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]string{"message": "搜索词需为 2–100 个字符"}})
			return
		}
		searcher, ok := reads.(SearchReadModels)
		if !ok {
			w.WriteHeader(http.StatusServiceUnavailable)
			_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]string{"message": "业务搜索暂不可用"}})
			return
		}
		items, err := searcher.Search(r.Context(), query, 10)
		if err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]string{"message": "业务搜索暂不可用"}})
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"items": items})
	})
}

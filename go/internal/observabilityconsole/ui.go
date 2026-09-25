package observabilityconsole

import (
	"embed"
	"io/fs"
	"net/http"
)

//go:embed ui/*
var embeddedUI embed.FS

func (service *Service) uiHandler() http.Handler {
	assets, err := fs.Sub(embeddedUI, "ui")
	if err != nil {
		return http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
			http.Error(writer, "observability console unavailable", http.StatusInternalServerError)
		})
	}
	return http.FileServer(http.FS(assets))
}

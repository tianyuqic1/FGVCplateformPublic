package httpapi

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/swaggest/swgui"
	"github.com/swaggest/swgui/v5emb"
	"github.com/tianyuqic1/FGVCplateformPublic/go/api/openapi"
)

const swaggerBasePath = "/swagger/"

func registerSwagger(router chi.Router) {
	registerSpecification(router, "/openapi/finevision.yaml", "finevision.yaml")
	registerSpecification(router, "/openapi/hardware.yaml", "hardware.yaml")

	router.Get("/swagger", func(writer http.ResponseWriter, request *http.Request) {
		http.Redirect(writer, request, swaggerBasePath, http.StatusPermanentRedirect)
	})
	swagger := v5emb.NewHandlerWithConfig(swgui.Config{
		Title:       "FineVision API",
		SwaggerJSON: "/openapi/finevision.yaml",
		BasePath:    swaggerBasePath,
		ShowTopBar:  true,
		SettingsUI: map[string]string{
			"deepLinking":              "true",
			"defaultModelsExpandDepth": "-1",
			"displayRequestDuration":   "true",
			"docExpansion":             `"list"`,
			"filter":                   "true",
			"operationsSorter":         `"method"`,
			"persistAuthorization":     "true",
			"urls": `[
				{url: "/openapi/finevision.yaml", name: "FineVision Control Plane"},
				{url: "/openapi/hardware.yaml", name: "Hardware Monitoring"}
			]`,
		},
	})
	router.Handle("/swagger/*", swagger)
}

func registerSpecification(router chi.Router, route, name string) {
	router.Get(route, func(writer http.ResponseWriter, _ *http.Request) {
		content, err := openapi.Specifications.ReadFile(name)
		if err != nil {
			http.Error(writer, "OpenAPI specification unavailable", http.StatusInternalServerError)
			return
		}
		writer.Header().Set("Content-Type", "application/yaml; charset=utf-8")
		writer.Header().Set("Cache-Control", "public, max-age=300")
		writer.Header().Set("Content-Disposition", fmt.Sprintf(`inline; filename="%s"`, strings.TrimSpace(name)))
		_, _ = writer.Write(content)
	})
}

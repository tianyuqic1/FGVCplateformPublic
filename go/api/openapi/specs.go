package openapi

import "embed"

// Specifications contains the source OpenAPI contracts used both for code
// generation and for the embedded Swagger UI. Keeping the YAML inside the Go
// binary makes the documentation available without a CDN or a sidecar.
//
//go:embed finevision.yaml hardware.yaml auth.yaml workers.yaml
var Specifications embed.FS

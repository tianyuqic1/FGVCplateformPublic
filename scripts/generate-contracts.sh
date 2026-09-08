#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GENERATOR_DIR="$(mktemp -d)"
trap 'rm -rf -- "$GENERATOR_DIR"' EXIT

(
  cd "$ROOT_DIR/go"
  go generate ./api/openapi
  GOBIN="$GENERATOR_DIR" go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.11
  GOBIN="$GENERATOR_DIR" go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.6.1
  PATH="$GENERATOR_DIR:$PATH" protoc \
    -I api/proto \
    --go_out=api/proto --go_opt=paths=source_relative \
    --go-grpc_out=api/proto --go-grpc_opt=paths=source_relative \
    api/proto/finevision/compute/v1/artifact.proto \
    api/proto/finevision/compute/v1/training_lifecycle.proto \
    api/proto/finevision/compute/v1/inference_runtime.proto \
    api/proto/finevision/compute/v1/dataset_compute.proto
)

uv run python -m grpc_tools.protoc \
  -I "$ROOT_DIR/go/api/proto" \
  --python_out="$ROOT_DIR/backend/src" \
  --grpc_python_out="$ROOT_DIR/backend/src" \
  "$ROOT_DIR/go/api/proto/finevision/compute/v1/artifact.proto" \
  "$ROOT_DIR/go/api/proto/finevision/compute/v1/training_lifecycle.proto" \
  "$ROOT_DIR/go/api/proto/finevision/compute/v1/inference_runtime.proto" \
  "$ROOT_DIR/go/api/proto/finevision/compute/v1/dataset_compute.proto"

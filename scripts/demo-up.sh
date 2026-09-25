#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

compose=(docker compose -f docker-compose.yml)
run_smoke=1
build_flag=(--build)

usage() {
  cat <<'USAGE'
Usage: scripts/demo-up.sh [--gpu] [--no-build] [--skip-smoke]

Starts the FineVision development stack. The `migrate` service applies Alembic
migrations before the Go Control Plane, relay, and Python compute runtimes start.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu)
      compose+=(-f docker-compose.gpu.yml)
      ;;
    --no-build)
      build_flag=()
      ;;
    --skip-smoke)
      run_smoke=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

echo "Validating compose configuration..."
"${compose[@]}" config >/dev/null

if [[ "${#build_flag[@]}" -gt 0 ]]; then
  # The inference image extends the local compute image. Build that base first
  # so a clean machine never races Docker Compose's parallel image builds.
  echo "Building Python compute base image..."
  "${compose[@]}" build python-training-worker
fi

echo "Starting FineVision demo stack..."
"${compose[@]}" up -d "${build_flag[@]}" \
  frontend go-control-plane outbox-relay go-llm-gateway \
  python-training-worker python-inference-runtime hardware-collector

if [[ "$run_smoke" -eq 1 ]]; then
  scripts/smoke-demo.sh
fi

cat <<'INFO'

FineVision demo is starting:
  frontend  http://localhost:5173
  hardware  http://localhost:5173/hardware
  Go API    http://localhost:8001
  MinIO     http://localhost:9001
  RabbitMQ  http://localhost:15672
  postgres  localhost:5432

Useful checks:
  docker compose ps
  docker compose logs migrate
  curl -fsS http://localhost:8001/api/health
INFO

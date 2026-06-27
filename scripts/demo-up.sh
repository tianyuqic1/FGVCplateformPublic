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

Starts the local FineVision demo stack. The compose `migrate` service applies
Alembic migrations after PostgreSQL is healthy and before api/ml-worker start.
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

echo "Starting FineVision demo stack..."
"${compose[@]}" up -d "${build_flag[@]}" frontend api ml-worker adminer

if [[ "$run_smoke" -eq 1 ]]; then
  scripts/smoke-demo.sh
fi

cat <<'INFO'

FineVision demo is starting:
  frontend  http://localhost:5173
  api       http://localhost:8001
  adminer   http://localhost:8081
  postgres  localhost:5432

Useful checks:
  docker compose ps
  docker compose logs migrate
  curl -fsS http://localhost:8001/api/health
INFO

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

API_URL="${API_URL:-http://localhost:8001}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:5173}"
WAIT_SECONDS="${WAIT_SECONDS:-90}"
RUN_FRONTEND_ROUTE_SMOKE="${RUN_FRONTEND_ROUTE_SMOKE:-0}"
contracts_only=0
release_acceptance=0

usage() {
  cat <<'USAGE'
Usage: scripts/smoke-demo.sh [--contracts-only] [--release-acceptance]

Default checks are lightweight contract and service-availability smoke checks,
not a complete browser E2E suite.

Use --contracts-only to run frontend API-client contract smokes without probing
API/frontend HTTP services.

Use --release-acceptance against a running demo stack to include API/frontend
probes, frontend API-client contracts, a production build, and route smoke.
The Go package test suite owns control-plane policy and state-machine coverage.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --contracts-only)
      contracts_only=1
      ;;
    --release-acceptance)
      release_acceptance=1
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

if [[ "$contracts_only" -eq 1 && "$release_acceptance" -eq 1 ]]; then
  echo "--contracts-only and --release-acceptance cannot be combined." >&2
  usage >&2
  exit 2
fi

if [[ "$release_acceptance" -eq 1 ]]; then
  RUN_FRONTEND_ROUTE_SMOKE=1
fi

wait_for_url() {
  local name="$1"
  local url="$2"
  local deadline=$((SECONDS + WAIT_SECONDS))

  if ! command -v curl >/dev/null 2>&1; then
    echo "curl is not installed; skipping ${name} HTTP probe (${url})."
    return 0
  fi

  until curl -fsS "$url" >/dev/null; do
    if (( SECONDS >= deadline )); then
      echo "Timed out waiting for ${name}: ${url}" >&2
      return 1
    fi
    sleep 2
  done
  echo "${name} OK: ${url}"
}

wait_for_anonymous_denial() {
  local name="$1"
  local url="$2"
  local deadline=$((SECONDS + WAIT_SECONDS))
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl is not installed; skipping ${name} authorization probe (${url})."
    return 0
  fi
  local status=""
  while (( SECONDS < deadline )); do
    status="$(curl -sS -o /dev/null -w '%{http_code}' "$url" || true)"
    if [[ "$status" == "401" ]]; then
      echo "${name} protected: anonymous request returned 401"
      return 0
    fi
    sleep 2
  done
  echo "Expected 401 for anonymous ${name}, got ${status:-no response}: ${url}" >&2
  return 1
}

echo "Validating compose configuration..."
docker compose -f docker-compose.yml config >/dev/null

if [[ "$contracts_only" -eq 0 ]]; then
  wait_for_url "api health" "${API_URL%/}/api/health"
  wait_for_anonymous_denial "swagger ui" "${API_URL%/}/swagger/"
  wait_for_anonymous_denial "control-plane openapi" "${API_URL%/}/openapi/finevision.yaml"
  wait_for_anonymous_denial "hardware openapi" "${API_URL%/}/openapi/hardware.yaml"
  wait_for_anonymous_denial "auth openapi" "${API_URL%/}/openapi/auth.yaml"
  wait_for_anonymous_denial "business API" "${API_URL%/}/api/datasets"
  wait_for_url "frontend" "$FRONTEND_URL"
else
  echo "Skipping HTTP probes in contracts-only mode."
fi

if [[ "$release_acceptance" -eq 1 ]]; then
  echo "Running release acceptance smoke gate."
else
  echo "Running lightweight demo smoke gate; this is not a complete browser E2E suite."
fi

echo "Running frontend client contract smoke checks..."
npm --prefix frontend run smoke:api-client
npm --prefix frontend run smoke:jobs-client
npm --prefix frontend run smoke:training-client
npm --prefix frontend run smoke:registry-client
npm --prefix frontend run smoke:inference-client
npm --prefix frontend run smoke:abstention-client
npm --prefix frontend run smoke:review-client
npm --prefix frontend run smoke:llm-client
npm --prefix frontend run smoke:diagnostics-client

if [[ "$contracts_only" -eq 0 && "$RUN_FRONTEND_ROUTE_SMOKE" == "1" ]]; then
  echo "Running frontend build and route availability smoke..."
  npm --prefix frontend run build
  npm --prefix frontend run smoke:routes
elif [[ "$contracts_only" -eq 1 ]]; then
  echo "Skipping route availability smoke in contracts-only mode."
else
  echo "Skipping route availability smoke; set RUN_FRONTEND_ROUTE_SMOKE=1 or use --release-acceptance to include it."
fi

echo "FineVision demo smoke passed."

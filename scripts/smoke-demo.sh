#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

API_URL="${API_URL:-http://localhost:8001}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:5173}"
WAIT_SECONDS="${WAIT_SECONDS:-90}"
RUN_FRONTEND_ROUTE_SMOKE="${RUN_FRONTEND_ROUTE_SMOKE:-0}"
RUN_ONLINE_ABSTENTION_CONTRACT_SMOKE="${RUN_ONLINE_ABSTENTION_CONTRACT_SMOKE:-1}"
RUN_ABSTENTION_ACTIVATION_CONTRACT_SMOKE="${RUN_ABSTENTION_ACTIVATION_CONTRACT_SMOKE:-0}"
contracts_only=0

usage() {
  cat <<'USAGE'
Usage: scripts/smoke-demo.sh [--contracts-only]

Checks a running demo stack by default. Use --contracts-only to run the
frontend API-client contract smokes without probing API/frontend HTTP services.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --contracts-only)
      contracts_only=1
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

echo "Validating compose configuration..."
docker compose -f docker-compose.yml config >/dev/null

if [[ "$contracts_only" -eq 0 ]]; then
  wait_for_url "api health" "${API_URL%/}/api/health"
  wait_for_url "frontend" "$FRONTEND_URL"
else
  echo "Skipping HTTP probes in contracts-only mode."
fi

echo "Running frontend client contract smoke checks..."
npm --prefix frontend run smoke:api-client
npm --prefix frontend run smoke:jobs-client
npm --prefix frontend run smoke:training-client
npm --prefix frontend run smoke:inference-client
npm --prefix frontend run smoke:abstention-client
npm --prefix frontend run smoke:review-client
npm --prefix frontend run smoke:llm-client

if [[ "$RUN_ONLINE_ABSTENTION_CONTRACT_SMOKE" == "1" ]]; then
  echo "Running online abstention contract smoke..."
  activation_args=()
  if [[ "$RUN_ABSTENTION_ACTIVATION_CONTRACT_SMOKE" == "1" ]]; then
    activation_args+=(--with-activation-contracts)
  fi
  scripts/smoke-online-abstention-contract.sh --skip-if-unavailable "${activation_args[@]}"
else
  echo "Skipping online abstention contract smoke; set RUN_ONLINE_ABSTENTION_CONTRACT_SMOKE=1 to include it."
fi

if [[ "$contracts_only" -eq 0 && "$RUN_FRONTEND_ROUTE_SMOKE" == "1" ]]; then
  echo "Running optional frontend route smoke..."
  npm --prefix frontend run build
  npm --prefix frontend run smoke:routes
elif [[ "$contracts_only" -eq 1 ]]; then
  echo "Skipping route preview smoke in contracts-only mode."
else
  echo "Skipping route preview smoke; set RUN_FRONTEND_ROUTE_SMOKE=1 to include it."
fi

echo "FineVision demo smoke passed."

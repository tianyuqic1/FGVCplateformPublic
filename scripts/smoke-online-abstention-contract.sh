#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

FINEVISION_TEST_DATABASE_URL="${FINEVISION_TEST_DATABASE_URL:-postgresql+psycopg://finevision:finevision@localhost:5432/finevision_test}"
FINEVISION_TEST_DATABASE_NAME="${FINEVISION_TEST_DATABASE_NAME:-finevision_test}"
prepare_db=1
skip_if_unavailable=0
dry_run=0
lock_file="${TMPDIR:-/tmp}/finevision-online-abstention-smoke-${USER:-user}.lock"

usage() {
  cat <<'USAGE'
Usage: scripts/smoke-online-abstention-contract.sh [--no-prepare] [--skip-if-unavailable] [--dry-run]

Runs the minimal online-abstention contract smoke against a dedicated test
database. The default URL is:

  postgresql+psycopg://finevision:finevision@localhost:5432/finevision_test

The smoke uses toy data and the lightweight default extractor; it does not run
DINOv3 or download model weights.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-prepare)
      prepare_db=0
      ;;
    --skip-if-unavailable)
      skip_if_unavailable=1
      ;;
    --dry-run)
      dry_run=1
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

require_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  if [[ "$skip_if_unavailable" -eq 1 ]]; then
    echo "uv is not installed; skipping online abstention contract smoke."
    exit 0
  fi
  echo "uv is required for the online abstention contract smoke." >&2
  exit 1
}

db_available() {
  FINEVISION_TEST_DATABASE_URL="$FINEVISION_TEST_DATABASE_URL" uv run python - <<'PY' >/dev/null 2>&1
import os
import sqlalchemy as sa

engine = sa.create_engine(os.environ["FINEVISION_TEST_DATABASE_URL"])
with engine.connect() as conn:
    conn.execute(sa.text("select 1"))
PY
}

prepare_test_database() {
  if [[ "$prepare_db" -eq 0 ]]; then
    return 0
  fi

  if db_available; then
    return 0
  fi

  if ! command -v docker >/dev/null 2>&1; then
    return 1
  fi

  if ! docker compose ps --status running postgres >/dev/null 2>&1; then
    return 1
  fi

  echo "Preparing ${FINEVISION_TEST_DATABASE_NAME} in the running compose PostgreSQL service..."
  if ! docker compose exec -T postgres psql -U finevision -d finevision -tAc \
    "SELECT 1 FROM pg_database WHERE datname='${FINEVISION_TEST_DATABASE_NAME}'" | grep -q 1; then
    docker compose exec -T postgres createdb -U finevision "$FINEVISION_TEST_DATABASE_NAME"
  fi

  DATABASE_URL="$FINEVISION_TEST_DATABASE_URL" uv run alembic upgrade head
}

run_smoke() {
  FINEVISION_TEST_DATABASE_URL="$FINEVISION_TEST_DATABASE_URL" \
    uv run --group dev pytest \
      backend/tests/test_online_abstention_policy.py \
      backend/tests/test_api_online_abstention_policy_contract.py \
      -q
}

require_uv

if [[ "$dry_run" -eq 1 ]]; then
  cat <<DRYRUN
FINEVISION_TEST_DATABASE_URL=$FINEVISION_TEST_DATABASE_URL \\
  uv run --group dev pytest \\
    backend/tests/test_online_abstention_policy.py \\
    backend/tests/test_api_online_abstention_policy_contract.py \\
    -q
DRYRUN
  exit 0
fi

if command -v flock >/dev/null 2>&1; then
  exec 9>"$lock_file"
  flock 9
else
  echo "flock is not installed; continuing without a local test-database lock."
fi

if ! prepare_test_database && ! db_available; then
  if [[ "$skip_if_unavailable" -eq 1 ]]; then
    cat <<SKIP
Skipping online abstention contract smoke because ${FINEVISION_TEST_DATABASE_URL} is unavailable.
Start the compose database or create the dedicated test database, then run:
  scripts/smoke-online-abstention-contract.sh
SKIP
    exit 0
  fi
  cat <<ERROR >&2
Online abstention contract smoke requires a reachable dedicated test database:
  ${FINEVISION_TEST_DATABASE_URL}

Start PostgreSQL with:
  docker compose up -d postgres

Then rerun:
  scripts/smoke-online-abstention-contract.sh
ERROR
  exit 1
fi

echo "Running online abstention contract smoke against ${FINEVISION_TEST_DATABASE_URL}..."
run_smoke

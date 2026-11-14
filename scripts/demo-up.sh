#!/usr/bin/env bash
# Compatibility with the original all-in-one local development command.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
args=(--with-worker)
for argument in "$@"; do
  case "$argument" in
    --gpu) args+=(--device cuda) ;;
    --skip-smoke) ;; # deploy.sh performs readiness probes without host npm.
    *) args+=("$argument") ;;
  esac
done
echo 'scripts/demo-up.sh is deprecated; use ./deploy.sh --with-worker.' >&2
exec "$ROOT_DIR/deploy.sh" "${args[@]}"

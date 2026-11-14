#!/usr/bin/env bash
# Single deployment entry point. Docker orchestration deliberately stays here.
set -euo pipefail
umask 077
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'HELP'
FineVision deployment
Usage: ./deploy.sh [up] [options]
       ./deploy.sh {status|logs [SERVICE]|doctor|restart|stop} [options]
       ./deploy.sh models {list|pull|check} [--models NAME,...]

Options:
  --with-worker          Start local training/inference workers and host collector
  --with-models          Prepare all registered weights and enable local workers
  --models NAME,...      Select weights (alias or preset); implies --with-models
  --device cpu|cuda      CPU default; CUDA requires local workers and NVIDIA access
  --with-observability   Start the log/metrics/trace stack
  --env-file PATH        Existing dotenv file; default .env is created if missing
  --project-name NAME    Isolated Compose project/state (default: finevision)
  --no-build             Reuse existing local application images
  --pull                 Pull service images; build local application images normally
  --timeout SECONDS      Readiness timeout (default: 180)
  --dry-run              Print the selection; no init, download, build or service changes
  --follow               Follow logs (logs only)
  --tail LINES           Log tail length (default: 100)
  -h, --help             Show this help

Default: platform + Agent + required artifact runtime, no local training/inference.
Saved selections are reused; omitted flags never stop existing components.
AI annotation and TensorRT/Ascend retain their separate deployment procedures.
HELP
}
fail() { echo "Deployment error: $*" >&2; exit 2; }
operation=up; model_action=""; log_service=""; dry_run=0; no_build=0; pull=0
follow=0; tail_lines=100; timeout=180; project=finevision; env_argument=""
resolve_args=(); model_names=all; topology=0
if [[ $# -gt 0 && "$1" != -* ]]; then
  operation="$1"; shift
  case "$operation" in
    models) [[ $# -gt 0 ]] || fail 'models requires list, pull or check'; model_action="$1"; shift
      case "$model_action" in list|pull|check) ;; *) fail "unknown models action: $model_action" ;; esac ;;
    up|status|doctor|restart|stop) ;;
    logs) if [[ $# -gt 0 && "$1" != -* ]]; then log_service="$1"; shift; fi ;;
    *) fail "unknown command: $operation" ;;
  esac
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --with-worker) resolve_args+=(--worker); topology=1; shift ;;
    --with-models) resolve_args+=(--with-models); topology=1; shift ;;
    --with-observability) resolve_args+=(--observability); topology=1; shift ;;
    --models|--device|--env-file|--project-name|--timeout|--tail)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail "$1 requires a value"
      case "$1" in
        --models) model_names="$2"; resolve_args+=(--models "$2"); topology=1 ;;
        --device) resolve_args+=(--device "$2"); topology=1 ;;
        --env-file) env_argument="$2"; resolve_args+=(--env-file "$2") ;;
        --project-name) project="$2" ;;
        --timeout) timeout="$2" ;;
        --tail) tail_lines="$2" ;;
      esac
      shift 2 ;;
    --no-build) no_build=1; shift ;;
    --pull) pull=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    --follow) follow=1; shift ;;
    *) fail "unknown option: $1; see ./deploy.sh --help" ;;
  esac
done
[[ "$timeout" =~ ^[1-9][0-9]*$ && "$tail_lines" =~ ^[0-9]+$ ]] || fail 'timeout/tail must be nonnegative integers; timeout must be positive'
[[ "$project" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ ]] || fail 'invalid --project-name'
if [[ "$operation" != up && "$operation" != doctor && "$operation" != models && "$topology" == 1 ]]; then
  fail "$operation reuses the saved selection; use up to change deployment options"
fi
[[ "$follow" == 0 || "$operation" == logs ]] || fail '--follow is only valid for logs'
if [[ -n "$env_argument" ]]; then
  [[ -f "$env_argument" ]] || fail 'custom --env-file does not exist'
  env_argument="$(cd "$(dirname "$env_argument")" && pwd)/$(basename "$env_argument")"
fi

# Helpers use only Python's standard library; Python is optional on the host.
helper() {
  if [[ "${FINEVISION_DEPLOY_BOOTSTRAP:-auto}" != container ]] && command -v python3 >/dev/null 2>&1 \
      && python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
    python3 "$ROOT_DIR/deploy/manage.py" --root "$ROOT_DIR" "$@"
  else
    command -v docker >/dev/null 2>&1 || fail 'Docker is required (or install Python 3 for planning)'
    local mounts=(-v "$ROOT_DIR:$ROOT_DIR" -w "$ROOT_DIR")
    # Explicit custom config outside the checkout is mounted at the same path.
    if [[ -n "$env_argument" && "$env_argument" == /* && "$env_argument" != "$ROOT_DIR/"* ]]; then
      [[ -f "$env_argument" ]] || fail 'custom --env-file does not exist'
      mounts+=(-v "$env_argument:$env_argument")
    fi
    docker run --rm --user "$(id -u):$(id -g)" "${mounts[@]}" \
      -e PYTHONDONTWRITEBYTECODE=1 python:3.12-alpine \
      python3 "$ROOT_DIR/deploy/manage.py" --root "$ROOT_DIR" "$@"
  fi
}

temporary=""; health_file=""; lock_dir=""
cleanup() {
  [[ -z "$temporary" ]] || rm -f "$temporary"
  [[ -z "$health_file" ]] || rm -f "$health_file"
  if [[ -n "$lock_dir" ]]; then rm -f "$lock_dir/pid"; rmdir "$lock_dir" 2>/dev/null || true; fi
  return 0
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ "$operation" == models ]]; then
  if [[ "$dry_run" == 1 ]]; then
    echo "Models action: $model_action (preview; no download)"
    helper models list --models "$model_names"
  else
    helper models "$model_action" --models "$model_names"
  fi
  exit 0
fi
if [[ "$dry_run" == 1 ]]; then
  if [[ -z "$env_argument" ]]; then env_argument="$(helper saved-env --project-name "$project")"; fi
  helper resolve --operation "$operation" --project-name "$project" "${resolve_args[@]}" --summary
  echo "Command: $operation; build=$((1-no_build)); pull=$pull; timeout=${timeout}s"
  exit 0
fi
command -v docker >/dev/null 2>&1 || fail 'Docker is not installed'
compose_version="$(docker compose version --short)" || fail 'Docker Compose v2 is required'
if [[ "$compose_version" =~ ^v?([0-9]+)\.([0-9]+) ]]; then
  (( BASH_REMATCH[1] > 2 || (BASH_REMATCH[1] == 2 && BASH_REMATCH[2] >= 30) )) || fail 'Docker Compose 2.30+ is required'
else
  fail 'cannot determine Docker Compose version'
fi
docker info >/dev/null 2>&1 || fail 'Docker daemon is unavailable'

# Serialize mutations per Compose project. Never remove a live deployment lock.
if [[ "$operation" == up || "$operation" == restart || "$operation" == stop ]]; then
  mkdir -p "$ROOT_DIR/.deploy/$project"
  candidate="$ROOT_DIR/.deploy/$project/lock"
  if ! mkdir "$candidate" 2>/dev/null; then
    fail "another deployment operation owns $candidate; inspect its pid before removing a stale lock"
  fi
  lock_dir="$candidate"
  echo "$$" > "$lock_dir/pid"
fi

if [[ -z "$env_argument" ]]; then
  env_argument="$(helper saved-env --project-name "$project")"
fi
temporary="$(mktemp "$ROOT_DIR/.deploy-plan.XXXXXX")"
helper resolve --operation "$operation" --project-name "$project" "${resolve_args[@]}" --write > "$temporary"
services=(); worker=0; with_models=0; observability=0; device=cpu
while IFS=$'\t' read -r key value; do
  case "$key" in
    project) project="$value" ;;
    env_file) env_file="$value" ;;
    plan_file) plan_file="$value" ;;
    override_file) override_file="$value" ;;
    worker) worker="$value" ;;
    with_models) with_models="$value" ;;
    observability) observability="$value" ;;
    device) device="$value" ;;
    models) model_names="$value" ;;
    service) services+=("$value") ;;
  esac
done < "$temporary"
rm -f "$temporary"; temporary=""

if [[ "$operation" == up ]]; then
  helper init --env-file "$env_file"
elif [[ ! -f "$env_file" ]]; then
  fail 'no configuration yet; run ./deploy.sh or use --dry-run'
fi
compose=(docker compose --project-directory "$ROOT_DIR" --project-name "$project" --env-file "$env_file" -f "$ROOT_DIR/docker-compose.yml")
[[ "$observability" == 0 ]] || compose+=(-f "$ROOT_DIR/compose.observability.yml" --profile observability)
[[ "$worker" == 0 ]] || compose+=(--profile local-worker)
compose+=(-f "$override_file")
"${compose[@]}" config --quiet

# Run hardware validation only for a deployment/diagnostic operation, not stop/status.
if [[ "$device" == cuda && ( "$operation" == up || "$operation" == doctor ) ]]; then
  echo 'Checking NVIDIA container access...'
  docker run --rm --gpus all --entrypoint nvidia-smi ubuntu:24.04 -L >/dev/null \
    || fail 'NVIDIA container access failed; fix driver/toolkit or select --device cpu'
fi

verify_health() {
  health_file="$(mktemp "$ROOT_DIR/.deploy-health.XXXXXX")"
  "${compose[@]}" ps --all --format json > "$health_file"
  helper health "$health_file" "${services[@]}"
  rm -f "$health_file"; health_file=""
}
case "$operation" in
  status) "${compose[@]}" ps --all "${services[@]}" ;;
  logs)
    if [[ -n "$log_service" ]]; then
      valid=0
      for service in "${services[@]}"; do [[ "$service" != "$log_service" ]] || valid=1; done
      [[ "$valid" == 1 ]] || fail 'log service is not in the saved selection'
      targets=("$log_service")
    else targets=("${services[@]}"); fi
    log_args=(--tail "$tail_lines"); [[ "$follow" == 0 ]] || log_args+=(-f)
    "${compose[@]}" logs "${log_args[@]}" "${targets[@]}" ;;
  stop) "${compose[@]}" stop "${services[@]}"; echo 'Stopped selected services; data volumes and model cache preserved.' ;;
  restart)
    "${compose[@]}" restart "${services[@]}"
    "${compose[@]}" up -d --no-build --wait --wait-timeout "$timeout" "${services[@]}"
    verify_health ;;
  doctor)
    echo 'OK Docker daemon and Compose configuration'
    if [[ "$with_models" == 1 ]]; then helper models check --models "$model_names"; fi
    verify_health
    echo 'LLM responses and actual training capacity are not exercised by health probes.' ;;
  up)
    helper resolve --operation up --project-name "$project" "${resolve_args[@]}" --summary
    # Large downloads must succeed before changing any running platform services.
    if [[ "$with_models" == 1 ]]; then helper models pull --models "$model_names"; fi
    if [[ "$pull" == 1 ]]; then
      # Local-image consumers such as artifact runtime/relays have no build section.
      # --ignore-buildable alone would try pulling their private local tags.
      pull_services=(postgres rabbitmq object-store agent-postgres object-store-init)
      if [[ "$worker" == 1 ]]; then pull_services+=(pretrained-weight-init); fi
      if [[ "$observability" == 1 ]]; then
        pull_services+=(alloy loki tempo prometheus postgres-exporter grafana)
      fi
      "${compose[@]}" pull "${pull_services[@]}"
    fi
    if [[ "$no_build" == 0 ]]; then
      # Artifact runtime shares this image even when local workers are disabled.
      "${compose[@]}" build python-training-worker
      build_services=(go-control-plane migrate frontend agent-service)
      if [[ "$worker" == 1 ]]; then build_services+=(python-inference-runtime hardware-collector); fi
      "${compose[@]}" build "${build_services[@]}"
    fi
    "${compose[@]}" up -d --no-build --wait --wait-timeout "$timeout" "${services[@]}"
    verify_health
    helper save "$plan_file"
    echo 'Deployment ready. Selection saved for status/logs/restart/stop.'
    "${compose[@]}" port frontend 5173
    echo 'Create the first admin (password is prompted securely):'
    printf '  '; printf '%q ' "${compose[@]}" run --rm --no-deps --entrypoint /usr/local/bin/finevision-admin go-control-plane --email admin@example.com --name 平台管理员; echo
    ;;
esac

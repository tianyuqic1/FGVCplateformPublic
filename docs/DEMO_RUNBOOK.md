# FineVision Demo Runbook

This runbook is the short path for a reproducible local demo with a fresh PostgreSQL database.
It avoids long training jobs and DINOv3 weight downloads by default.

## Prerequisites

- Docker with Compose v2.
- Node/npm for frontend client smoke scripts.
- Optional: `uv` for host-run Alembic or backend toolkit checks.

## Environment

Create `.env` from `.env.example` only when you need local secrets or provider overrides:

```bash
cp .env.example .env
```

The default compose stack already supplies the local PostgreSQL URL to `api`, `ml-worker`, and
`migrate`. LLM and Hugging Face tokens are optional and are not needed for the default smoke path.

## Start The Demo

```bash
scripts/demo-up.sh
```

What happens:

1. Compose config is validated.
2. PostgreSQL starts and waits until healthy.
3. The one-shot `migrate` service runs `alembic upgrade head`.
4. `api`, `ml-worker`, `frontend`, and `adminer` start.
5. `scripts/smoke-demo.sh` runs the lightweight demo gate: API/frontend availability, frontend
   API-client contracts, and the online abstention contract smoke when a dedicated test database is
   available. This default gate is not a complete browser E2E suite.

Services:

```text
frontend  http://localhost:5173
api       http://localhost:8001
adminer   http://localhost:8081
postgres  localhost:5432
```

GPU check for DINOv3 extraction and classifier training:

```bash
docker compose exec ml-worker python - <<'PY'
import torch
print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)
PY
```

Expected on the local workstation: `True` and the NVIDIA GPU name. If this prints `False`, check
that `docker compose config` includes `gpus` for `api` and `ml-worker`, and that
`docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi` works on the host.

## Migration-Only Check

For a focused fresh-database acceptance check:

```bash
docker compose up -d postgres
docker compose run --rm migrate
```

`docker compose run --rm migrate` prints Alembic output directly. When `migrate` is started as a
dependency of `docker compose up`, inspect it with `docker compose logs migrate`.

`docker compose up frontend api ml-worker` also runs `migrate` automatically because `api` and
`ml-worker` depend on the migration service completing successfully.

## Smoke Checks

Run the lightweight smoke framework against an already running stack. This is a contract and service
availability gate, not full browser E2E coverage:

```bash
scripts/smoke-demo.sh
```

Run the stronger MVP release gate before publishing a demo build:

```bash
scripts/smoke-demo.sh --release-acceptance
```

The release gate includes API/frontend probes, frontend API-client contracts, online abstention plus
manual activation contracts, frontend production build, and route availability smoke.

Run only contract checks without probing API/frontend HTTP services:

```bash
scripts/smoke-demo.sh --contracts-only
```

Run only the online abstention contract smoke:

```bash
scripts/smoke-online-abstention-contract.sh
```

This smoke defaults to `finevision_test`, prepares that database through the running compose
PostgreSQL service when possible, and runs only toy-data policy tests. It does not start DINOv3
training or download DINO weights.

Include frontend build and route availability checks without the full release gate when needed:

```bash
RUN_FRONTEND_ROUTE_SMOKE=1 scripts/smoke-demo.sh
```

Route availability smoke verifies that Vite preview returns HTTP 200 for SPA routes only. It does
not execute browser interactions or prove dataset/import/training/review workflows end to end.

Backend toolkit smoke remains separate and CPU-safe:

```bash
uv run --group dev python -m finevision.ml_toolkit.smoke --work-dir .finevision-smoke
```

## Stop And Reset

Stop containers while keeping database and artifact volumes:

```bash
docker compose down
```

Reset the local demo database and volumes:

```bash
docker compose down -v
```

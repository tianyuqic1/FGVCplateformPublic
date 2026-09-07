# Phase 1 Verification Record

> Date: 2026-09-07
>
> Branch: `codex/phase1-refactor`

## Automated Results

| Area | Command / scope | Result |
| --- | --- | --- |
| Python | `uv run pytest -q` | 49 passed, 37 environment-gated skipped |
| ViT-S | `test_dinov3_vits_training_integration.py` | passed; 12 images → 384-dim CLS → ridge head |
| Go | `go test ./...` | passed |
| Go static | `go vet ./...` | passed |
| Concurrency | `go test -race ./internal/training ./internal/outbox ./internal/httpapi` | passed |
| PostgreSQL | lifecycle integration against PostgreSQL 16 | passed |
| Alembic | fresh upgrade → downgrade to `0010` → upgrade `0011` | passed |
| MinIO | S3 upload, HEAD, read-back and SHA-cache materialize | passed |
| RabbitMQ | durable topology, persistent mandatory publish and publisher confirm | passed |
| Frontend | `npm ci`, production build, seven API-client smokes | passed |
| Compose | `docker compose config --quiet` | passed |
| Go images | Control Plane, Outbox Relay, LLMGateway multi-stage builds | passed |
| Compute image | lockfile-pinned Python 3.12 image; Protobuf import and MinIO weight fetch | passed |
| Inference runtime | containerized gRPC `Health` call after verified ViT-S materialization | passed |

Environment-gated Python tests require optional real PostgreSQL or downloaded benchmark datasets;
their skips are intentional. The ViT-S integration test is not skipped when the `dinov3` extra is
installed and uses the repository's approved LFS checkpoint without a network download.

## Fault And Correctness Coverage

- Job/Training/Outbox atomic creation.
- Duplicate dispatch returns obsolete without creating another active attempt.
- Monotonic execution-epoch fencing rejects stale heartbeat, progress, complete and fail.
- Independent heartbeat extends the lease; reaper expires and redispatches abandoned attempts.
- Pause/resume/cancel transitions and cancel/complete commit-order race.
- Completion digest idempotency returns the same Model Version after response loss.
- Artifact SHA/size mismatch prevents success and Model Version publication.
- Corrupted local SHA cache is replaced only from a verified canonical object.
- Relay crash after broker confirm but before database write-back produces a safe duplicate.
- Mandatory RabbitMQ routing, publisher confirms, prefetch=1 and poison-message DLQ behavior.
- LLM internal authentication, configured-model enforcement, path redaction and advisory-only output.
- Fine-R1 route/module/UI/dependency removal.

## Live Tracer Check

A running Compose subset was used to verify:

```text
POST /api/training-runs
  -> PostgreSQL Job + Training Run + Outbox commit
  -> Go Outbox Relay
  -> RabbitMQ finevision.training.v1
  -> versioned claim payload
```

The same stack promoted the 86,362,376-byte ViT-S checkpoint into MinIO, then streamed it back and
validated SHA-256 `2a1ec16ae28ffa07bc0ead0241ee7df9fc26451fe6f9f839b7b3afa0a906b040`.

## Release Notes

- Phase 1 publishes only DINOv3 ViT-S; ViT-B/L are deliberately excluded.
- The base Compose file is CPU-safe. `docker-compose.gpu.yml` requests NVIDIA GPUs for Python compute.
- MinIO and RabbitMQ credentials in `.env.example` are local-development defaults only.
- `npm ci` currently reports dependency audit findings; this verification did not apply an automatic
  major-version upgrade because it is outside the Phase 1 architecture change.

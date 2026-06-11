from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any

from finevision.api.store import JobRecord, JobStore, MetadataStore
from finevision.ml_toolkit.datasets import scan_imagefolder


DEFAULT_METADATA_DIR = ".finevision-api/metadata"


def run_next_job(metadata_dir: str | Path | None = None) -> JobRecord | None:
    root = metadata_dir or os.environ.get("FINEVISION_METADATA_DIR", DEFAULT_METADATA_DIR)
    metadata_store = MetadataStore(root)
    job_store = JobStore(root)
    job = job_store.next_queued_job()
    if job is None:
        return None

    running = job_store.mark_running(job)
    try:
        result = _run_job(running, metadata_store)
    except Exception as exc:  # The worker boundary persists failures for API inspection.
        return job_store.mark_failed(running, str(exc))
    return job_store.mark_succeeded(running, result)


def run_worker_loop(
    metadata_dir: str | Path | None = None,
    *,
    poll_interval_seconds: float = 2.0,
    max_jobs: int | None = None,
) -> int:
    completed = 0
    while max_jobs is None or completed < max_jobs:
        job = run_next_job(metadata_dir)
        if job is None:
            time.sleep(poll_interval_seconds)
            continue
        completed += 1
    return completed


def _run_job(job: JobRecord, metadata_store: MetadataStore) -> dict[str, Any]:
    if job.type == "import_imagefolder":
        return _run_import_imagefolder(job.payload, metadata_store)
    raise ValueError(f"Unsupported job type: {job.type}")


def _run_import_imagefolder(payload: dict[str, Any], metadata_store: MetadataStore) -> dict[str, Any]:
    root = Path(_required_payload_value(payload, "path"))
    dataset_id = _required_payload_value(payload, "dataset_id")
    dataset_version_id = _required_payload_value(payload, "dataset_version_id")
    manifest = scan_imagefolder(root, dataset_id, dataset_version_id)
    metadata_store.save_dataset_manifest(manifest)
    return {
        "dataset_id": manifest.dataset_id,
        "dataset_version_id": manifest.dataset_version_id,
        "sample_count": len(manifest.samples),
        "class_count": len(manifest.classes),
        "ready": manifest.readiness.get("ready") is True,
    }


def _required_payload_value(payload: dict[str, Any], field: str) -> str:
    value = str(payload.get(field, "")).strip()
    if not value:
        raise ValueError(f"Missing job payload field: {field}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run FineVision worker jobs.")
    parser.add_argument("--metadata-dir", default=None)
    parser.add_argument("--once", action="store_true", help="Execute at most one queued job.")
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    args = parser.parse_args()

    if args.once:
        job = run_next_job(args.metadata_dir)
        if job is None:
            print("No queued job.")
            return
        print(f"{job.job_id} {job.status}")
        return

    run_worker_loop(args.metadata_dir, poll_interval_seconds=args.poll_interval_seconds)


if __name__ == "__main__":
    main()

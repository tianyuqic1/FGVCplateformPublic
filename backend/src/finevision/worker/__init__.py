from __future__ import annotations

from pathlib import Path

from finevision.persistence.store import JobRecord


def run_next_job(metadata_dir: str | Path | None = None) -> JobRecord | None:
    from .jobs import run_next_job as _run_next_job

    return _run_next_job(metadata_dir)


def run_worker_loop(
    metadata_dir: str | Path | None = None,
    *,
    poll_interval_seconds: float = 2.0,
    max_jobs: int | None = None,
) -> int:
    from .jobs import run_worker_loop as _run_worker_loop

    return _run_worker_loop(
        metadata_dir,
        poll_interval_seconds=poll_interval_seconds,
        max_jobs=max_jobs,
    )

__all__ = ["run_next_job", "run_worker_loop"]

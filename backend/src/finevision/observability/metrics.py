"""Low-cardinality Prometheus metrics shared by Python runtimes.

Business identifiers deliberately never appear as labels. They belong in logs,
traces and PostgreSQL audit records instead.
"""

from __future__ import annotations

import os
import re
import threading
import logging
from typing import Iterable

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server


_SAFE_LABEL = re.compile(r"[^a-z0-9_:.-]+")
_DEFAULT_OUTCOMES = {"success", "failed", "rejected", "cancelled", "retry", "unknown"}
_servers: set[tuple[str, int]] = set()
_server_lock = threading.Lock()


def label_value(value: object, *, allowed: Iterable[str] | None = None) -> str:
    normalized = _SAFE_LABEL.sub("_", str(value or "").strip().lower()).strip("_") or "unknown"
    if normalized != "unknown" and allowed is not None and normalized not in set(allowed):
        return "other"
    return normalized[:64]


class RuntimeMetrics:
    def __init__(self, service: str, *, registry: CollectorRegistry | None = None) -> None:
        self.service = label_value(service)
        self.registry = registry
        common = {"registry": registry} if registry is not None else {}
        self.work_total = Counter(
            "finevision_runtime_work_total",
            "Completed FineVision runtime work items.",
            ("service", "kind", "outcome"),
            **common,
        )
        self.work_duration = Histogram(
            "finevision_runtime_work_duration_seconds",
            "FineVision runtime work item duration.",
            ("service", "kind", "outcome"),
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 15, 30, 60, 300, 900, 3600),
            **common,
        )
        self.inflight = Gauge(
            "finevision_runtime_inflight",
            "Current FineVision runtime work items.",
            ("service", "kind"),
            **common,
        )
        self.heartbeat_age = Gauge(
            "finevision_runtime_heartbeat_age_seconds",
            "Seconds since the runtime last reported healthy progress.",
            ("service",),
            **common,
        )
        self.integrity_failures = Counter(
            "finevision_artifact_integrity_failures_total",
            "Artifact SHA or size verification failures.",
            ("service", "artifact_kind"),
            **common,
        )

    def record_work(self, *, kind: str, outcome: str, duration_seconds: float) -> None:
        labels = (
            self.service,
            label_value(kind),
            label_value(outcome, allowed=_DEFAULT_OUTCOMES),
        )
        self.work_total.labels(*labels).inc()
        self.work_duration.labels(*labels).observe(max(0.0, float(duration_seconds)))

    def set_inflight(self, *, kind: str, value: float) -> None:
        self.inflight.labels(self.service, label_value(kind)).set(max(0.0, float(value)))

    def change_inflight(self, *, kind: str, delta: float) -> None:
        self.inflight.labels(self.service, label_value(kind)).inc(float(delta))

    def set_heartbeat_age(self, value: float) -> None:
        self.heartbeat_age.labels(self.service).set(max(0.0, float(value)))

    def record_integrity_failure(self, artifact_kind: str) -> None:
        self.integrity_failures.labels(self.service, label_value(artifact_kind)).inc()


def start_metrics_server(service: str, default_port: int) -> RuntimeMetrics:
    """Start a fail-open localhost-independent metrics endpoint once per process."""

    metrics = RuntimeMetrics(service)
    if os.environ.get("FINEVISION_METRICS_ENABLED", "true").lower() in {"0", "false", "no", "off"}:
        return metrics
    try:
        host = os.environ.get("FINEVISION_METRICS_ADDRESS", "0.0.0.0")
        port = int(os.environ.get("FINEVISION_METRICS_PORT", str(default_port)))
        if not 0 < port < 65536:
            raise ValueError("metrics port must be between 1 and 65535")
        with _server_lock:
            key = (host, port)
            if key not in _servers:
                start_http_server(port, addr=host)
                _servers.add(key)
    except Exception as error:
        logging.getLogger(__name__).warning(
            "Metrics endpoint initialization failed; continuing without HTTP export",
            extra={"event": "metrics_initialization_failed", "error_type": type(error).__name__, "outcome": "failed"},
        )
    return metrics


__all__ = ["RuntimeMetrics", "label_value", "start_metrics_server"]

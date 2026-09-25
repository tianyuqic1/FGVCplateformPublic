"""FineVision runtime telemetry contract.

Business audit events remain in PostgreSQL. This module only formats and
correlates operational records and deliberately fails open if logging breaks.
"""

from __future__ import annotations

import contextlib
import contextvars
from datetime import datetime, timezone
import json
import logging
import os
import sys
import traceback
from typing import Any, Iterator, TextIO

from opentelemetry import trace

RedactedValue = "[REDACTED]"
_MAX_FIELD_BYTES = 4_096
_MAX_EXCEPTION_BYTES = 16_384
_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "finevision_observability_context", default={}
)

_STANDARD_RECORD_FIELDS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
}


class JSONFormatter(logging.Formatter):
    def __init__(self, service: str, environment: str, service_version: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment
        self.service_version = service_version

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "service_version": self.service_version,
            "environment": self.environment,
            "message": _sanitize_value("message", record.getMessage()),
        }
        payload.update({key: _sanitize_value(key, value) for key, value in _context.get().items()})
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            payload["trace_id"] = format(span_context.trace_id, "032x")
            payload["span_id"] = format(span_context.span_id, "016x")
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and key not in payload:
                payload[key] = _sanitize_value(key, value)
        if record.exc_info:
            exception = "".join(traceback.format_exception(*record.exc_info))
            payload["exception"] = _truncate(exception, _MAX_EXCEPTION_BYTES)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_logging(
    service: str,
    *,
    environment: str | None = None,
    service_version: str | None = None,
    level: str | int | None = None,
    stream: TextIO | None = None,
    force: bool = False,
) -> logging.Logger:
    """Configure the process root logger and return the named service logger."""

    configured_level = level or os.environ.get("FINEVISION_LOG_LEVEL", "INFO")
    output = stream or sys.stdout
    handler = logging.StreamHandler(output)
    log_format = os.environ.get("FINEVISION_LOG_FORMAT", "json").lower()
    if log_format == "text":
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    else:
        handler.setFormatter(
            JSONFormatter(
                service,
                environment or os.environ.get("FINEVISION_ENVIRONMENT", "local"),
                service_version or os.environ.get("FINEVISION_SERVICE_VERSION", "dev"),
            )
        )
    root = logging.getLogger()
    if force:
        for existing in list(root.handlers):
            root.removeHandler(existing)
            with contextlib.suppress(Exception):
                existing.close()
    if force or not root.handlers:
        root.addHandler(handler)
    root.setLevel(configured_level)
    return logging.getLogger(service)


@contextlib.contextmanager
def bind_context(**fields: Any) -> Iterator[None]:
    merged = {**_context.get(), **{key: value for key, value in fields.items() if value not in (None, "")}}
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_context.get())


def _sensitive_key(key: str) -> bool:
    normalized = "".join(character.lower() if character.isalnum() else "_" for character in key)
    sensitive = (
        "authorization",
        "api_key",
        "apikey",
        "password",
        "secret",
        "access_token",
        "refresh_token",
        "database_url",
        "image_data",
        "data_url",
        "prompt",
        "request_body",
        "response_body",
    )
    return normalized == "image" or any(
        normalized == item or normalized.endswith("_" + item) for item in sensitive
    )


def _sanitize_value(key: str, value: Any) -> Any:
    if _sensitive_key(key):
        return RedactedValue
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered.startswith(("data:image/", "bearer ", "sk-")):
            return RedactedValue
        return _truncate(value, _MAX_FIELD_BYTES)
    if isinstance(value, dict):
        return {str(item_key): _sanitize_value(str(item_key), item_value) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(key, item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _truncate(str(value), _MAX_FIELD_BYTES)


def _truncate(value: str, maximum_bytes: int) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= maximum_bytes:
        return value
    suffix = "...[TRUNCATED]"
    available = maximum_bytes - len(suffix.encode())
    return encoded[:available].decode("utf-8", errors="ignore") + suffix


__all__ = [
    "JSONFormatter",
    "RedactedValue",
    "bind_context",
    "configure_logging",
    "current_context",
]

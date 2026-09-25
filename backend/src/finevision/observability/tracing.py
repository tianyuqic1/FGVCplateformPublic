"""Fail-open OpenTelemetry bootstrap and asynchronous message links."""

from __future__ import annotations

import contextlib
import logging
import os
from functools import wraps
from typing import Iterator, Mapping

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import DEPLOYMENT_ENVIRONMENT, SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased


def configure_tracing(service: str):
    if os.environ.get("FINEVISION_OBSERVABILITY_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        provider = TracerProvider(
            resource=Resource.create({
                SERVICE_NAME: service,
                SERVICE_VERSION: os.environ.get("FINEVISION_SERVICE_VERSION", "dev"),
                DEPLOYMENT_ENVIRONMENT: os.environ.get("FINEVISION_ENVIRONMENT", "local"),
            }),
            sampler=ParentBased(TraceIdRatioBased(_sample_ratio())),
        )
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://alloy:4317")
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=endpoint.startswith("http://"))))
    except Exception as error:
        logging.getLogger(__name__).warning(
            "Tracing initialization failed; continuing without export",
            extra={"event": "tracing_initialization_failed", "error_type": type(error).__name__, "outcome": "failed"},
        )
        return None
    trace.set_tracer_provider(provider)
    try:
        from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient, GrpcInstrumentorServer
        GrpcInstrumentorClient().instrument()
        GrpcInstrumentorServer().instrument()
    except Exception as error:
        logging.getLogger(__name__).warning(
            "Automatic gRPC tracing unavailable; OTLP export remains active",
            extra={"event": "grpc_tracing_instrumentation_failed", "error_type": type(error).__name__, "outcome": "failed"},
        )
    return provider


@contextlib.contextmanager
def linked_message_span(name: str, headers: Mapping[str, object] | None = None) -> Iterator[trace.Span]:
    carrier = {
        str(key): value.decode() if isinstance(value, bytes) else str(value)
        for key, value in (headers or {}).items()
        if key in {"traceparent", "tracestate", "baggage"}
    }
    parent = trace.get_current_span(propagate.extract(carrier)).get_span_context()
    links = [trace.Link(parent)] if parent.is_valid else []
    with trace.get_tracer("finevision.message").start_as_current_span(name, links=links) as span:
        yield span


def observed_message_callback(name: str, callback):
    @wraps(callback)
    def wrapped(channel, method, properties, body):
        with linked_message_span(name, getattr(properties, "headers", None)):
            return callback(channel, method, properties, body)
    return wrapped


def _sample_ratio() -> float:
    try:
        return min(1.0, max(0.0, float(os.environ.get("OTEL_TRACES_SAMPLER_ARG", "0.1"))))
    except ValueError:
        return 0.1


__all__ = ["configure_tracing", "linked_message_span", "observed_message_callback"]

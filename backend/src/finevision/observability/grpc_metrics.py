from __future__ import annotations

import time
import logging
import grpc

from .metrics import RuntimeMetrics


class MetricsServerInterceptor(grpc.ServerInterceptor):
    """Record RPC totals and latency without high-cardinality method labels."""

    def __init__(self, metrics: RuntimeMetrics) -> None:
        self.metrics = metrics

    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler
        original = handler.unary_unary

        def observed(request, context):
            started, outcome = time.monotonic(), "success"
            self.metrics.change_inflight(kind="grpc", delta=1)
            try:
                return original(request, context)
            except Exception:
                outcome = "failed"
                raise
            finally:
                if context.code() not in (None, grpc.StatusCode.OK):
                    outcome = "failed"
                self.metrics.change_inflight(kind="grpc", delta=-1)
                self.metrics.record_work(kind="grpc", outcome=outcome, duration_seconds=time.monotonic() - started)
                if outcome == "failed":
                    logging.getLogger(__name__).warning(
                        "gRPC request failed",
                        extra={"event": "grpc_request_failed", "rpc_method": handler_call_details.method, "grpc_code": str(context.code()), "outcome": outcome},
                    )

        return grpc.unary_unary_rpc_method_handler(
            observed,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


__all__ = ["MetricsServerInterceptor"]

import json
import urllib.error
from unittest.mock import MagicMock

import pytest
from finevision.compute.worker_reporter import WorkerReporter, grpc_interceptor


def test_idle_heartbeat_and_sequence(monkeypatch):
    calls = []
    def send(request, timeout):
        calls.append((request.full_url, json.loads(request.data), timeout))
        return MagicMock()
    monkeypatch.setattr("urllib.request.urlopen", send)
    reporter = WorkerReporter("training", token="test-only")
    reporter.state("ready")
    reporter.send_once()
    reporter.send_once()
    assert calls[0][0].endswith("/register")
    assert calls[1][0].endswith("/heartbeat")
    assert calls[1][1]["sequence"] == 1
    assert calls[1][1]["tasks"] == []
    assert "token" not in calls[1][1]


def test_tasks_clear_even_on_failure():
    reporter = WorkerReporter("training")
    with pytest.raises(ValueError):
        with reporter.task("run-1", "训练"):
            assert reporter.data["tasks"][0]["id"] == "run-1"
            raise ValueError("private exception")
    assert reporter.data["tasks"] == []
    assert "private" not in reporter.data["reason"]


def test_rejected_session_never_reregisters(monkeypatch):
    reporter = WorkerReporter("training", token="test-only")
    calls = []
    def reject():
        calls.append(1)
        raise urllib.error.HTTPError("http://local", 409, "retired", {}, None)
    monkeypatch.setattr(reporter, "send_once", reject)
    reporter._loop()
    assert len(calls) == 1
    assert reporter.registered is False


def test_grpc_health_not_counted_and_request_cleared():
    import grpc
    from types import SimpleNamespace
    reporter = WorkerReporter("inference", 4)
    def invoke(request, context):
        assert len(reporter.data["tasks"]) == 1
        return "ok"
    handler = grpc.unary_unary_rpc_method_handler(invoke)
    interceptor = grpc_interceptor(reporter)
    assert interceptor.intercept_service(lambda _: handler, SimpleNamespace(method="/service/Health")) is handler
    wrapped = interceptor.intercept_service(lambda _: handler, SimpleNamespace(method="/service/Predict"))
    assert wrapped.unary_unary(None, None) == "ok"
    assert reporter.data["tasks"] == []

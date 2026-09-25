from __future__ import annotations

from prometheus_client import CollectorRegistry, generate_latest

from finevision.observability.metrics import RuntimeMetrics, label_value, start_metrics_server


def test_runtime_metrics_record_low_cardinality_work() -> None:
    registry = CollectorRegistry()
    metrics = RuntimeMetrics("training_worker", registry=registry)

    metrics.record_work(kind="training", outcome="success", duration_seconds=1.25)
    metrics.record_work(kind="training", outcome="failed", duration_seconds=0.5)
    metrics.set_inflight(kind="training", value=2)
    metrics.set_heartbeat_age(4.5)

    output = generate_latest(registry).decode()
    assert 'finevision_runtime_work_total{kind="training",outcome="success",service="training_worker"} 1.0' in output
    assert 'finevision_runtime_work_total{kind="training",outcome="failed",service="training_worker"} 1.0' in output
    assert 'finevision_runtime_inflight{kind="training",service="training_worker"} 2.0' in output
    assert 'finevision_runtime_heartbeat_age_seconds{service="training_worker"} 4.5' in output
    assert "job_id" not in output


def test_metric_label_values_are_bounded() -> None:
    assert label_value(" TensorRT ") == "tensorrt"
    assert label_value("anything-new", allowed={"success", "failed"}) == "other"
    assert label_value("", allowed={"success", "failed"}) == "unknown"


def test_metrics_server_configuration_fails_open(monkeypatch) -> None:
    monkeypatch.setenv("FINEVISION_METRICS_PORT", "not-a-port")
    metrics = start_metrics_server("test-runtime", 9999)
    metrics.record_work(kind="test", outcome="success", duration_seconds=0)

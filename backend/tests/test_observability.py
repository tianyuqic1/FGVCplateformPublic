import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from finevision.observability import RedactedValue, bind_context, configure_logging
from finevision.observability import tracing


def _last_record(stream: io.StringIO) -> dict:
    lines = [line for line in stream.getvalue().splitlines() if line]
    return json.loads(lines[-1])


def test_json_logging_redacts_secrets_and_includes_bound_context():
    stream = io.StringIO()
    logger = configure_logging("python-training-worker", environment="test", stream=stream, force=True)

    with bind_context(job_id="job-1", attempt_id="attempt-2", request_id="request-3"):
        logger.info(
            "training started",
            extra={
                "event": "training_attempt_started",
                "authorization": "Bearer secret",
                "image_data_url": "data:image/png;base64,AAAA",
                "safe": "visible",
            },
        )

    record = _last_record(stream)
    assert record["service"] == "python-training-worker"
    assert record["environment"] == "test"
    assert record["event"] == "training_attempt_started"
    assert record["job_id"] == "job-1"
    assert record["attempt_id"] == "attempt-2"
    assert record["request_id"] == "request-3"
    assert record["authorization"] == RedactedValue
    assert record["image_data_url"] == RedactedValue
    assert record["safe"] == "visible"


def test_context_is_isolated_between_worker_threads():
    stream = io.StringIO()
    logger = configure_logging("worker", environment="test", stream=stream, force=True)

    def emit(job_id: str) -> None:
        with bind_context(job_id=job_id):
            logger.info("work", extra={"event": "work"})

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(emit, ["job-a", "job-b"]))

    records = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    assert {record["job_id"] for record in records} == {"job-a", "job-b"}
    assert all(record["event"] == "work" for record in records)


def test_unhandled_exception_is_bounded_and_classified():
    stream = io.StringIO()
    logger = configure_logging("worker", environment="test", stream=stream, force=True)
    try:
        raise RuntimeError("x" * 8_000)
    except RuntimeError:
        logger.exception("task failed", extra={"event": "task_failed", "error_type": "RuntimeError"})

    record = _last_record(stream)
    assert record["error_type"] == "RuntimeError"
    assert "RuntimeError" in record["exception"]
    assert len(record["exception"].encode()) <= 16_384
    assert logging.getLogger().handlers


def test_tracing_exporter_configuration_fails_open(monkeypatch):
    monkeypatch.setenv("FINEVISION_OBSERVABILITY_ENABLED", "true")
    monkeypatch.setattr(tracing, "OTLPSpanExporter", lambda **_: (_ for _ in ()).throw(RuntimeError("offline")))
    assert tracing.configure_tracing("test-runtime") is None

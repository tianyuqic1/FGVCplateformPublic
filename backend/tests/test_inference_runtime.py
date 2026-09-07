from __future__ import annotations

from pathlib import Path

import grpc
import pytest

from finevision.artifact_store import LocalFilesystemArtifactStore
from finevision.compute.inference_runtime import InferenceRuntimeService
from finevision.compute.v1 import inference_runtime_pb2


def test_inference_runtime_health_and_exact_sha_cache_eviction(tmp_path: Path) -> None:
    service = InferenceRuntimeService(cache_root=tmp_path / "cache")
    assert service.Health(inference_runtime_pb2.HealthRequest(), None).status == "ok"

    cached = tmp_path / "cache" / "sha256" / ("a" * 64)
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"cached")
    response = service.EvictCache(inference_runtime_pb2.EvictCacheRequest(sha256="a" * 64), None)
    assert response.evicted is True
    assert not cached.exists()


def test_inference_runtime_fails_closed_for_corrupted_model_bundle(tmp_path: Path) -> None:
    source = tmp_path / "bundle.json"
    source.write_text("{}", encoding="utf-8")
    store = LocalFilesystemArtifactStore(tmp_path / "objects")
    descriptor = store.put_file(
        source,
        artifact_id="11111111-1111-4111-8111-111111111111",
        artifact_type="model_bundle",
        content_type="application/json",
        producer="pytest",
    )
    Path(descriptor.uri.removeprefix("file://")).write_bytes(b"tampered")
    request = inference_runtime_pb2.PredictRequest()
    request.model_bundle.artifact_id = descriptor.artifact_id
    request.model_bundle.artifact_type = descriptor.artifact_type
    request.model_bundle.uri = descriptor.uri
    request.model_bundle.sha256 = descriptor.sha256
    request.model_bundle.size_bytes = descriptor.size_bytes
    request.model_bundle.content_type = descriptor.content_type
    request.model_bundle.storage_version = descriptor.storage_version
    request.model_bundle.producer = descriptor.producer
    request.model_bundle.schema_version = 1
    service = InferenceRuntimeService(cache_root=tmp_path / "cache")

    class Context:
        def abort(self, code, details):
            raise grpc.RpcError(f"{code}: {details}")

    with pytest.raises(grpc.RpcError, match="integrity"):
        service.Predict(request, Context())

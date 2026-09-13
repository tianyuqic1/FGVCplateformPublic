from __future__ import annotations

from pathlib import Path

import grpc
import pytest

from finevision.artifact_store import ArtifactDescriptor, LocalFilesystemArtifactStore
from finevision.compute.inference_runtime import InferenceRuntimeService, _validate_bundle_scope
from finevision.compute.v1 import inference_runtime_pb2


@pytest.mark.parametrize("precision", ["FP32", "FP16"])
def test_published_onnx_inference_and_threshold_override(tmp_path, precision):
    import json
    import numpy as np
    import onnx
    from onnx import helper, TensorProto, numpy_helper
    from PIL import Image
    from google.protobuf.json_format import ParseDict
    store = LocalFilesystemArtifactStore(tmp_path / "objects")
    scope = dict(dataset_version_id="dataset-v1", training_run_id="run-1")
    classes = ["circle", "square"]
    bundle = {"model_format": "image_classifier_v2", "model_artifact": {"classes": classes, "dataset_version_id": "dataset-v1"}, "threshold_strategy": {"dataset_version_id": "dataset-v1", "temperature": 1., "accept_threshold": .5, "margin_threshold": 0.}}
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(bundle))
    descriptor = store.put_file(source, artifact_id="bundle", artifact_type="model_bundle", content_type="application/json", producer="test", **scope)
    path = tmp_path / "published.onnx"
    dtype = np.float16 if precision == "FP16" else np.float32
    tensor_type = TensorProto.FLOAT16 if precision == "FP16" else TensorProto.FLOAT
    graph = helper.make_graph([helper.make_node("Constant", [], ["logits"], value=numpy_helper.from_array(np.array([[2., 1.]], dtype=dtype)))], "test", [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1,3,4,4])], [helper.make_tensor_value_info("logits", tensor_type, [1,2])])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    onnx.save(model, path)
    published = store.put_file(path, artifact_id="onnx", artifact_type="full_onnx", content_type="application/octet-stream", producer="test", **scope)
    image_path = tmp_path / "image.png"
    Image.new("RGB", (8,8), "red").save(image_path)
    image = store.put_file(image_path, artifact_id="image", artifact_type="inference_image", content_type="image/png", producer="test")
    from dataclasses import asdict
    metadata = {"source_bundle_sha256": descriptor.sha256, "classes": classes, "preprocessing": {"input_size": [3,4,4], "mean": [.5,.5,.5], "std": [.5,.5,.5], "interpolation": "bicubic", "crop_pct": 1.}}
    request = inference_runtime_pb2.PredictRequest()
    for target, artifact in [(request.model_bundle, descriptor), (request.input_image, image)]:
        for field in ["artifact_id", "artifact_type", "uri", "sha256", "size_bytes", "content_type", "dataset_version_id", "training_run_id"]:
            value = getattr(artifact, field)
            if value is not None:
                setattr(target, field, value)
    payload = asdict(published)
    # Descriptor timestamps are not needed by the compute contract.
    payload = {k:v for k,v in payload.items() if k not in {"created_at", "verified_at"}}
    payload["metadata"] = metadata
    ParseDict({"published_onnx": payload, "top_k": 2, "accept_threshold": .9}, request.policy)
    class Context:
        def abort(self, code, details):
            raise grpc.RpcError(f"{code}: {details}")
    runtime = InferenceRuntimeService(tmp_path / "cache")
    result = runtime.Predict(request, Context())
    assert result.top_k[0].label == "circle"
    assert result.decision == "abstain"
    assert result.confidence == pytest.approx(.731, abs=.002)
    payload["metadata"]["source_bundle_sha256"] = "0" * 64
    request.policy.Clear()
    ParseDict({"published_onnx": payload}, request.policy)
    with pytest.raises(grpc.RpcError, match="does not match"):
        runtime.Predict(request, Context())


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


def test_inference_runtime_rejects_cross_dataset_artifact_bundle() -> None:
    def descriptor(artifact_id: str, dataset_version_id: str) -> ArtifactDescriptor:
        return ArtifactDescriptor(
            artifact_id=artifact_id,
            artifact_type="model",
            uri=f"s3://finevision-artifacts/compute/model/aa/{'a' * 64}",
            sha256="a" * 64,
            size_bytes=1,
            content_type="application/octet-stream",
            storage_version="s3-v1",
            producer="pytest",
            dataset_version_id=dataset_version_id,
            training_run_id="run-1",
        )

    bundle = descriptor("bundle", "dataset-version-a")
    model = descriptor("model", "dataset-version-b")
    features = descriptor("features", "dataset-version-a")
    with pytest.raises(ValueError, match="different Dataset Version"):
        _validate_bundle_scope(bundle, model, features, {})

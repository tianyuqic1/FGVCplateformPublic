"""Export verified linear heads; never deserialize user-supplied pickle models."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import threading
from uuid import UUID, uuid4

import grpc
import numpy as np
from google.protobuf.struct_pb2 import Struct

from finevision.compute.artifacts import descriptor_from_dict, descriptor_from_proto
from finevision.compute.v1 import model_export_pb2_grpc
from finevision.compute.export_precision import validate_precision, convert_graph, deployment_state, check_parity


def export_linear_head(source: Path, destination: Path, classes: list[str], model_version_id: str, precision="FP32"):
    import torch
    import onnx
    import onnxruntime as ort
    validate_precision(precision)

    with np.load(source, allow_pickle=False) as data:
        arrays = {key: np.array(data[key], dtype=np.float32, copy=True) for key in ("weights", "bias", "feature_mean", "feature_std")}
    weights, bias, mean, std = (arrays[key] for key in ("weights", "bias", "feature_mean", "feature_std"))
    if weights.ndim != 2 or not 0 < weights.shape[0] <= 8192 or not 0 < weights.shape[1] <= 10000:
        raise ValueError("invalid linear head shape")
    dim, count = weights.shape
    if bias.shape != (count,) or mean.shape != (dim,) or std.shape != (dim,):
        raise ValueError("head normalization shape mismatch")
    if len(classes) != count or len(set(classes)) != count or any(not isinstance(label, str) or not label for label in classes):
        raise ValueError("head class mapping mismatch")
    if not all(np.isfinite(array).all() for array in arrays.values()):
        raise ValueError("head contains non-finite parameters")

    class LinearHead(torch.nn.Module):
        def __init__(self):
            super().__init__()
            for name, array in arrays.items():
                self.register_buffer(name, torch.from_numpy(array))

        def forward(self, features):
            safe_std = torch.where(self.feature_std < 1e-6, torch.ones_like(self.feature_std), self.feature_std)
            return ((features - self.feature_mean) / safe_std) @ self.weights + self.bias

    destination.mkdir(parents=True, exist_ok=True)
    pt_path, onnx_path = destination / "linear_head.pt", destination / "linear_head.onnx"
    model = LinearHead().eval()
    torch.save({"state_dict": deployment_state(model.state_dict(), precision), "classes": classes, "feature_dim": dim, "model_version_id": model_version_id, "precision": precision}, pt_path)
    # Reload the checkpoint produced above; arbitrary uploaded .pt files are not accepted.
    checkpoint = torch.load(pt_path, map_location="cpu", weights_only=True)
    # Export from the full-precision source; quantize once in the ONNX graph.
    torch.onnx.export(model, (torch.zeros(1, dim),), str(onnx_path), input_names=["features"], output_names=["logits"],
                      dynamic_axes={"features": {0: "batch"}, "logits": {0: "batch"}}, opset_version=17, dynamo=False)
    graph = convert_graph(onnx.load(str(onnx_path)), precision)
    metadata = {"precision": precision, "validation_provider": "CPUExecutionProvider", "model_version_id": model_version_id, "classes": json.dumps(classes, ensure_ascii=False), "input_contract": "float32[batch,feature_dim]", "output_contract": "unnormalized_logits", "feature_dim": str(dim)}
    onnx.helper.set_model_props(graph, metadata)
    onnx.checker.check_model(graph)
    onnx.save(graph, str(onnx_path))
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    session = ort.InferenceSession(str(onnx_path), sess_options=options, providers=["CPUExecutionProvider"])
    rng = np.random.default_rng(7)
    max_error = 0.0
    for batch in (1, 5, 32):
        samples = (rng.normal(size=(batch, dim)).astype(np.float32) * np.where(std < 1e-6, 1.0, std) + mean).astype(np.float32)
        expected = ((samples - mean) / np.where(std < 1e-6, 1.0, std)) @ weights + bias
        with torch.inference_mode():
            pytorch = model(torch.from_numpy(samples)).numpy()
        actual = session.run(["logits"], {"features": samples})[0]
        check_parity(actual, expected, precision)
        check_parity(actual, pytorch, precision)
        max_error = max(max_error, float(np.abs(actual - expected).max()))
    return pt_path, onnx_path, {**metadata, "classes": classes, "feature_dim": dim, "opset": 17, "max_abs_error": max_error, "parity_passed": True}


class ModelExportService(model_export_pb2_grpc.ModelExportServicer):
    def __init__(self, reader, store):
        self.reader, self.store = reader, store
        self.slots = threading.BoundedSemaphore(1)

    def ExportHead(self, request, context):
        if not self.slots.acquire(blocking=False):
            context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "another export is running; retry later")
        try:
            precision = validate_precision(request.precision or "FP32")
            for value in (request.model_version_id, request.dataset_version_id, request.training_run_id):
                UUID(value)
            bundle_descriptor = descriptor_from_proto(request.source_bundle)
            if bundle_descriptor.artifact_type != "model_bundle" or bundle_descriptor.size_bytes > 1024 * 1024:
                raise ValueError("invalid source bundle")
            if bundle_descriptor.dataset_version_id != request.dataset_version_id or bundle_descriptor.training_run_id != request.training_run_id:
                raise ValueError("source bundle scope mismatch")
            bundle = json.loads(self.reader.materialize(bundle_descriptor).read_text())
            source = descriptor_from_dict(bundle["model"])
            image_model = bundle.get("model_format") == "image_classifier_v2"
            limit = 512 * 1024 * 1024 if image_model else 32 * 1024 * 1024
            if source.artifact_type != "model" or not 0 < source.size_bytes <= limit:
                raise ValueError("invalid head artifact")
            if source.dataset_version_id != request.dataset_version_id or source.training_run_id != request.training_run_id:
                raise ValueError("head scope mismatch")
            artifact = bundle["model_artifact"]
            if artifact["dataset_version_id"] != request.dataset_version_id:
                raise ValueError("class mapping scope mismatch")
            with tempfile.TemporaryDirectory(prefix="model-export-") as directory:
                exporter = export_linear_head
                if image_model:
                    from finevision.compute.image_export import export_image_classifier
                    exporter = export_image_classifier
                pt, onnx, metadata = exporter(self.reader.materialize(source), Path(directory), artifact["classes"], request.model_version_id, precision=precision)
                if not context.is_active():
                    raise TimeoutError("export request expired")
                metadata.update({"source_sha256": source.sha256, "source_bundle_sha256": bundle_descriptor.sha256})
                descriptors = []
                for path, kind in ((pt, "full_pt" if image_model else "head_pt"), (onnx, "full_onnx" if image_model else "head_onnx")):
                    descriptor = self.store.put_file(path, artifact_id=str(uuid4()), artifact_type=kind,
                        content_type="application/octet-stream", producer="python-model-export/v1",
                        dataset_version_id=request.dataset_version_id, training_run_id=request.training_run_id,
                        metadata={**metadata, "source_name": path.name})
                    descriptors.append(asdict(descriptor))
                result = Struct()
                result.update({"artifacts": descriptors})
                return result
        except Exception as error:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, f"model export failed: {type(error).__name__}")
        finally:
            self.slots.release()

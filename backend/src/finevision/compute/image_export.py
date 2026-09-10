"""Complete, merged image classifier publication, with numerical parity checks."""
import json
from pathlib import Path

import numpy as np
import torch

from finevision.ml_toolkit.image_training import load_classifier, merged_classifier
from finevision.compute.export_precision import validate_precision, convert_graph, deployment_state, check_parity


def export_image_classifier(source, destination, classes, model_version_id, precision="FP32"):
    import onnx
    import onnxruntime as ort
    validate_precision(precision)
    model, checkpoint = load_classifier(source)
    if classes != checkpoint["classes"]:
        raise ValueError("checkpoint and registry class mappings differ")
    merged = merged_classifier(model)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    pt, onnx_path = destination / "image_classifier.pt", destination / "image_classifier.onnx"
    checkpoint.update({"state_dict": deployment_state(merged.state_dict(), precision), "merged": True, "model_version_id": model_version_id, "precision": precision})
    torch.save(checkpoint, pt)
    shape = tuple(checkpoint["preprocessing"]["input_size"])
    torch.onnx.export(merged, (torch.zeros(1, *shape),), str(onnx_path),
        input_names=["images"], output_names=["logits"],
        dynamic_axes={"images": {0: "batch"}, "logits": {0: "batch"}}, opset_version=17, dynamo=False)
    metadata = {"model_version_id": model_version_id, "model_format": "image_classifier_v2", "classes": classes,
                "preprocessing": checkpoint["preprocessing"], "training_mode": checkpoint["training_config"]["training_mode"],
                "input_contract": "float32[N,3,H,W], RGB preprocessed", "output_contract": "unnormalized_logits",
                "merged": True, "opset": 17, "precision": precision,
                "validation_provider": "CPUExecutionProvider", "mixed_precision": precision == "FP16"}
    graph = convert_graph(onnx.load(str(onnx_path)), precision)
    onnx.helper.set_model_props(graph, {k: json.dumps(v, ensure_ascii=False) for k, v in metadata.items()})
    onnx.checker.check_model(graph)
    onnx.save(graph, str(onnx_path))
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    session = ort.InferenceSession(str(onnx_path), sess_options=options, providers=["CPUExecutionProvider"])
    rng = np.random.default_rng(42)
    max_error = 0.0
    for batch in (1, 2):
        images = rng.normal(size=(batch, *shape)).astype(np.float32)
        with torch.inference_mode():
            expected = model(torch.from_numpy(images)).numpy()
            merged_logits = merged(torch.from_numpy(images)).numpy()
        actual = session.run(["logits"], {"images": images})[0]
        np.testing.assert_allclose(merged_logits, expected, rtol=1e-3, atol=1e-4)
        check_parity(actual, expected, precision)
        max_error = max(max_error, float(np.abs(actual - expected).max()))
    return pt, onnx_path, {**metadata, "parity_passed": True, "max_abs_error": max_error}

"""One isolated build process. Its result is accepted only with a live build lease."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import numpy as np

from finevision.artifact_store import S3ArtifactStore
from finevision.compute.artifacts import VerifiedArtifactReader, create_s3_client, descriptor_from_dict
from finevision.compute.deployment_backends import TensorRTEngine, AscendEngine, build_tensorrt, build_ascend, fingerprint
from finevision.compute.export_precision import check_parity


def validate_engine(reference, engine, shape, precision, max_batch):
    """Synthetic numerical smoke gate, not a task accuracy benchmark."""
    rng = np.random.default_rng(20260913)
    errors, agreements, elapsed = [], [], []
    for batch in sorted({1, min(2, max_batch), max_batch}):
        for images in (np.zeros((batch, *shape), np.float32), rng.normal(size=(batch, *shape)).astype(np.float32)):
            expected = reference.run(None, {reference.get_inputs()[0].name: images})[0]
            start = time.perf_counter()
            actual = engine.run(images)
            elapsed.append((time.perf_counter() - start) * 1000)
            if actual.shape != expected.shape or actual.ndim != 2:
                raise ValueError("compiled logits shape mismatch")
            check_parity(actual, expected, precision)
            errors.append(float(np.max(np.abs(actual - expected))))
            agreements.extend((actual.argmax(1) == expected.argmax(1)).tolist())
    return {"parity_passed": True, "validation_kind": "synthetic_numerical_smoke",
            "max_abs_error": max(errors), "top1_agreement": float(np.mean(agreements)),
            "probe_latency_ms": elapsed, "real_dataset_evaluated": False}


def build(job, work, reader, store):
    import onnxruntime as ort
    source = descriptor_from_dict(job["source"])
    if source.artifact_type != "full_onnx" or source.metadata.get("precision", "FP32") != "FP32":
        raise ValueError("compiled deployments require a published full FP32 ONNX")
    shape = source.metadata["preprocessing"]["input_size"]
    if len(shape) != 3 or shape[0] != 3 or any(not isinstance(x, int) or x < 1 or x > 1024 for x in shape):
        raise ValueError("invalid image shape")
    if job["precision"] not in ("FP32", "FP16") or not 1 <= job["max_batch"] <= 32:
        raise ValueError("invalid build precision/batch")
    path = reader.materialize(source)
    identity = fingerprint(job["runtime"])
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    reference = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
    if len(reference.get_inputs()) != 1 or len(reference.get_outputs()) != 1:
        raise ValueError("only single image input and logits output supported")
    if job["runtime"] == "tensorrt":
        output, kind = work / "model.plan", "tensorrt_engine"
        build_tensorrt(path, output, shape, job["precision"], job["max_batch"])
        engine = TensorRTEngine(output)
    elif job["runtime"] == "ascend_acl":
        output, kind = work / "model.om", "ascend_om"
        build_ascend(path, output, shape, job["precision"], job["max_batch"], work / "build.log")
        engine = AscendEngine(output)
    else:
        raise ValueError("unsupported build runtime")
    try:
        report = validate_engine(reference, engine, shape, job["precision"], job["max_batch"])
    finally:
        engine.close()
    metadata = {**source.metadata, **report, "deployment_id": job["id"], "source_onnx_sha256": source.sha256,
                "runtime": job["runtime"], "precision": job["precision"], "target_profile": job["target_profile"],
                "max_batch": job["max_batch"], "runtime_fingerprint": identity}
    descriptor = store.put_file(output, artifact_id=str(uuid4()), artifact_type=kind,
                                content_type="application/octet-stream", producer="deployment-builder/v1",
                                dataset_version_id=source.dataset_version_id, training_run_id=source.training_run_id,
                                metadata=metadata)
    return {"artifact": asdict(descriptor), "validation": report}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    args = parser.parse_args()
    job = json.loads(args.job.read_text())
    work = args.job.parent
    client = create_s3_client()
    # Keep hardware variants within the model's dataset/version storage hierarchy.
    from uuid import UUID
    dataset_id = str(UUID(job["dataset_id"]))
    version_id = str(UUID(job["source"]["dataset_version_id"]))
    source_prefix = f"datasets/{dataset_id}/versions/{version_id}/models/{job['model_version_id']}"
    store = S3ArtifactStore(client=client, bucket=os.environ["FINEVISION_ARTIFACT_BUCKET"],
                            prefix=f"{source_prefix}/deployments/{job['id']}/{job['build_token']}")
    reader = VerifiedArtifactReader(os.environ.get("FINEVISION_ARTIFACT_CACHE_DIR", "/data/cache"), client)
    result = build(job, work, reader, store)
    (work / "result.json").write_text(json.dumps(result, default=str))


if __name__ == "__main__":
    main()

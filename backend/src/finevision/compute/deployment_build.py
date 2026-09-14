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
    errors, relative_errors, cosine_similarities, agreements, elapsed = [], [], [], [], []
    for batch in sorted({1, min(2, max_batch), max_batch}):
        for images in (np.zeros((batch, *shape), np.float32), rng.normal(size=(batch, *shape)).astype(np.float32)):
            expected = reference.run(None, {reference.get_inputs()[0].name: images})[0]
            start = time.perf_counter()
            actual = engine.run(images)
            elapsed.append((time.perf_counter() - start) * 1000)
            if actual.shape != expected.shape or actual.ndim != 2:
                raise ValueError("compiled logits shape mismatch")
            if not np.isfinite(actual).all():
                raise ValueError("non-finite deployment output")
            error = np.abs(actual - expected)
            errors.append(float(np.max(error)))
            scale = max(float(np.max(np.abs(expected))), 1.0)
            relative_errors.append(float(np.max(error)) / scale)
            actual_norm = np.linalg.norm(actual, axis=1)
            expected_norm = np.linalg.norm(expected, axis=1)
            denominator = actual_norm * expected_norm
            cosine = np.divide(
                np.sum(actual * expected, axis=1), denominator,
                out=np.zeros_like(denominator), where=denominator > 1e-12,
            )
            # Two all-zero logit vectors are exactly equivalent; cosine itself
            # is undefined for this synthetic probe.
            cosine[(actual_norm <= 1e-12) & (expected_norm <= 1e-12)] = 1.0
            cosine_similarities.extend(cosine.tolist())
            agreements.extend((actual.argmax(1) == expected.argmax(1)).tolist())
            if precision == "FP32":
                check_parity(actual, expected, precision)
    top1_agreement = float(np.mean(agreements))
    min_cosine_similarity = float(np.min(cosine_similarities))
    max_relative_error = max(relative_errors)
    if precision == "FP16":
        # TensorRT selects FP16 tactics per layer, so a long transformer can
        # accumulate larger element-wise logit drift than a plain FP16 ONNX
        # conversion. Keep the hardware gate semantic and bounded: logits must
        # remain directionally equivalent, predictions stable, and worst-case
        # drift below 5% of the reference logit scale.
        if max_relative_error > 5e-2:
            raise AssertionError(f"FP16 engine relative logit drift {max_relative_error:.4f} exceeds 0.05")
        if min_cosine_similarity < 0.995:
            raise AssertionError(f"FP16 engine cosine similarity {min_cosine_similarity:.6f} is below 0.995")
        if top1_agreement < 0.95:
            raise AssertionError(f"FP16 engine top-1 agreement {top1_agreement:.4f} is below 0.95")
    return {"parity_passed": True, "validation_kind": "synthetic_numerical_smoke",
            "max_abs_error": max(errors), "top1_agreement": float(np.mean(agreements)),
            "max_relative_error": max_relative_error, "min_cosine_similarity": min_cosine_similarity,
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

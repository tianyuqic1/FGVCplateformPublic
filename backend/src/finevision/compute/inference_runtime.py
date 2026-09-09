from __future__ import annotations

import json
import os
from concurrent import futures
from pathlib import Path
import grpc
import numpy as np
from google.protobuf.struct_pb2 import Struct

from finevision.artifact_store import (
    ArtifactDescriptor,
    ArtifactIntegrityError,
    S3ArtifactStore,
)
from finevision.compute.v1 import inference_runtime_pb2, inference_runtime_pb2_grpc
from finevision.compute.pretrained_weights import MANAGED_WEIGHTS, prepare_managed_weight
from finevision.ml_toolkit.features import build_extractor_from_config
from finevision.ml_toolkit.inference import run_image_inference
from finevision.schemas.artifacts import ModelArtifact, ThresholdStrategy
from finevision.compute.artifacts import (
    VerifiedArtifactReader,
    descriptor_from_proto as _descriptor_from_proto,
    descriptor_from_dict as _descriptor_from_dict,
    create_s3_client as _create_s3_client,
)


class InferenceRuntimeService(inference_runtime_pb2_grpc.InferenceRuntimeServicer):
    def __init__(self, cache_root: str | Path, s3_client: object | None = None) -> None:
        self.cache_root = Path(cache_root).resolve()
        self.s3_client = s3_client

    def Health(self, request, context):  # noqa: N802 - generated gRPC contract
        return inference_runtime_pb2.HealthResponse(status="ok")

    def EvictCache(self, request, context):  # noqa: N802
        sha256 = str(request.sha256).lower()
        if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
            if context is not None:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, "sha256 must be lowercase hexadecimal")
            return inference_runtime_pb2.EvictCacheResponse(evicted=False)
        path = self.cache_root / "sha256" / sha256
        existed = path.is_file()
        path.unlink(missing_ok=True)
        return inference_runtime_pb2.EvictCacheResponse(evicted=existed)

    def Predict(self, request, context):  # noqa: N802
        try:
            bundle_descriptor = _descriptor_from_proto(request.model_bundle)
            bundle_path = self._materialize(bundle_descriptor)
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            if bundle.get("model_format") == "image_classifier_v2":
                return self._predict_image_model(request, bundle_descriptor, bundle)
            model_descriptor = _descriptor_from_dict(bundle["model"])
            features_descriptor = _descriptor_from_dict(bundle["features"])
            _validate_bundle_scope(bundle_descriptor, model_descriptor, features_descriptor, bundle)
            model_path = self._materialize(model_descriptor)
            features_path = self._materialize(features_descriptor)
            input_path = self._materialize(_descriptor_from_proto(request.input_image))

            model_data = np.load(model_path, allow_pickle=False)
            model_state = {key: model_data[key] for key in model_data.files}
            model_artifact = ModelArtifact(**bundle["model_artifact"])
            strategy = ThresholdStrategy(**bundle["threshold_strategy"])
            feature_data = np.load(features_path, allow_pickle=False)
            extractor_config = bundle["extractor_config"]
            backbone_key = str(extractor_config.get("backbone_key") or extractor_config.get("preset") or "")
            if backbone_key in MANAGED_WEIGHTS:
                prepare_managed_weight(
                    S3ArtifactStore(
                        client=self.s3_client or _create_s3_client(),
                        bucket=os.environ.get("FINEVISION_ARTIFACT_BUCKET", "finevision-artifacts"),
                    ),
                    self.cache_root,
                    backbone_key,
                )
            extractor = build_extractor_from_config(extractor_config)
            policy = dict(request.policy) if request.policy is not None else {}
            result = run_image_inference(
                image_path=str(input_path),
                extractor=extractor,
                model_artifact=model_artifact,
                model_state=model_state,
                threshold_strategy=strategy,
                reference_features=feature_data["features"].astype(np.float32),
                reference_sample_ids=[str(value) for value in feature_data["sample_ids"].tolist()],
                reference_labels=[str(value) for value in feature_data["labels"].tolist()],
                ood_distance_threshold=_optional_float(policy.get("ood_distance_threshold")),
                top_k=int(policy.get("top_k", 3)),
                evidence_k=int(policy.get("evidence_k", 3)),
            )
        except ArtifactIntegrityError as error:
            context.abort(grpc.StatusCode.DATA_LOSS, str(error))
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as error:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"invalid verified model bundle: {error}")

        evidence = Struct()
        evidence.update({"nearest_neighbors": result.nearest_neighbors})
        return inference_runtime_pb2.Prediction(
            top_k=[inference_runtime_pb2.LabelScore(label=str(item["label"]), score=float(item["score"])) for item in result.top_k],
            decision=result.decision.decision,
            confidence=result.decision.confidence,
            margin=result.decision.margin,
            ood_score=result.decision.ood_score or 0.0,
            reasons=result.decision.reasons,
            evidence=evidence,
        )

    def _materialize(self, descriptor: ArtifactDescriptor) -> Path:
        return VerifiedArtifactReader(self.cache_root, self.s3_client).materialize(descriptor)

    def _predict_image_model(self, request, bundle_descriptor, bundle):
        import torch
        from PIL import Image
        from finevision.ml_toolkit.image_training import load_classifier, image_transform
        from finevision.ml_toolkit.metrics import softmax
        descriptor = _descriptor_from_dict(bundle["model"])
        _validate_bundle_scope(bundle_descriptor, descriptor, descriptor, bundle)
        model, checkpoint = load_classifier(self._materialize(descriptor))
        if checkpoint["classes"] != bundle["model_artifact"]["classes"]:
            raise ValueError("checkpoint class mapping differs from bundle")
        image_path = self._materialize(_descriptor_from_proto(request.input_image))
        with Image.open(image_path) as image:
            inputs = image_transform(checkpoint["preprocessing"])(image.convert("RGB")).unsqueeze(0)
        with torch.inference_mode():
            logits = model(inputs).numpy()
        strategy = bundle["threshold_strategy"]
        probabilities = softmax(logits, temperature=float(strategy["temperature"]))[0]
        order = np.argsort(probabilities)[::-1]
        confidence = float(probabilities[order[0]])
        margin = confidence - (float(probabilities[order[1]]) if len(order) > 1 else 0)
        reasons = []
        if confidence + 1e-6 < strategy["accept_threshold"]:
            reasons.append("confidence_below_threshold")
        if margin + 1e-6 < strategy["margin_threshold"]:
            reasons.append("top1_top2_margin_below_threshold")
        policy = dict(request.policy)
        if policy.get("ood_distance_threshold") is not None:
            raise ValueError("embedding-distance OOD is unavailable for image classifiers")
        top_k = max(1, min(int(policy.get("top_k", 3)), len(order)))
        evidence = Struct()
        evidence.update({"nearest_neighbors": [], "retrieval_available": False})
        return inference_runtime_pb2.Prediction(
            top_k=[inference_runtime_pb2.LabelScore(label=checkpoint["classes"][i], score=float(probabilities[i])) for i in order[:top_k]],
            decision="abstain" if reasons else "accept", confidence=confidence, margin=margin,
            reasons=reasons or ["meets_acceptance_thresholds"], evidence=evidence)




def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


def _validate_bundle_scope(
    bundle: ArtifactDescriptor,
    model: ArtifactDescriptor,
    features: ArtifactDescriptor,
    payload: dict[str, object],
) -> None:
    if not bundle.dataset_version_id or not bundle.training_run_id:
        raise ValueError("model bundle must carry Dataset Version and Training Run lineage")
    for descriptor in (model, features):
        if descriptor.dataset_version_id != bundle.dataset_version_id:
            raise ValueError("model bundle contains an Artifact from a different Dataset Version")
        if descriptor.training_run_id != bundle.training_run_id:
            raise ValueError("model bundle contains an Artifact from a different Training Run")
    model_artifact = payload.get("model_artifact")
    if isinstance(model_artifact, dict) and model_artifact.get("dataset_version_id") != bundle.dataset_version_id:
        raise ValueError("model metadata Dataset Version does not match the verified bundle")
    strategy = payload.get("threshold_strategy")
    if isinstance(strategy, dict) and strategy.get("dataset_version_id") != bundle.dataset_version_id:
        raise ValueError("threshold strategy Dataset Version does not match the verified bundle")




def main() -> None:
    s3_client = _create_s3_client()
    cache_root = os.environ.get("FINEVISION_ARTIFACT_CACHE_DIR", "/data/cache")
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=int(os.environ.get("FINEVISION_INFERENCE_WORKERS", "4"))))
    inference_runtime_pb2_grpc.add_InferenceRuntimeServicer_to_server(
        InferenceRuntimeService(cache_root, s3_client=s3_client),
        server,
    )
    server.add_insecure_port(os.environ.get("FINEVISION_INFERENCE_GRPC_ADDRESS", "[::]:9100"))
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    main()

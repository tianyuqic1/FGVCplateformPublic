from __future__ import annotations

import json
import os
from concurrent import futures
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.config import Config
import grpc
import numpy as np
from google.protobuf.struct_pb2 import Struct

from finevision.artifact_store import (
    ArtifactDescriptor,
    ArtifactIntegrityError,
    LocalFilesystemArtifactStore,
    S3ArtifactStore,
)
from finevision.compute.v1 import inference_runtime_pb2, inference_runtime_pb2_grpc
from finevision.compute.pretrained_weights import MANAGED_WEIGHTS, prepare_managed_weight
from finevision.ml_toolkit.features import build_extractor_from_config
from finevision.ml_toolkit.inference import run_image_inference
from finevision.schemas.artifacts import ModelArtifact, ThresholdStrategy


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
        parsed = urlparse(descriptor.uri)
        if parsed.scheme == "file":
            store = LocalFilesystemArtifactStore(self.cache_root / "local-source")
        elif parsed.scheme == "s3":
            store = S3ArtifactStore(client=self.s3_client or _create_s3_client(), bucket=parsed.netloc)
        else:
            raise ValueError("artifact URI must use file:// or s3://")
        return store.materialize_verified(descriptor, self.cache_root)


def _descriptor_from_proto(message) -> ArtifactDescriptor:
    return ArtifactDescriptor(
        artifact_id=message.artifact_id,
        artifact_type=message.artifact_type,
        uri=message.uri,
        sha256=message.sha256,
        size_bytes=message.size_bytes,
        content_type=message.content_type,
        storage_version=message.storage_version,
        producer=message.producer,
        dataset_version_id=message.dataset_version_id or None,
        training_run_id=message.training_run_id or None,
        attempt_id=message.attempt_id or None,
        schema_version=message.schema_version,
        metadata=dict(message.metadata) if message.HasField("metadata") else {},
    )


def _descriptor_from_dict(value: dict[str, object]) -> ArtifactDescriptor:
    fields = {
        "artifact_id": value["artifact_id"],
        "artifact_type": value["artifact_type"],
        "uri": value["uri"],
        "sha256": value["sha256"],
        "size_bytes": value["size_bytes"],
        "content_type": value["content_type"],
        "storage_version": value["storage_version"],
        "producer": value["producer"],
        "dataset_version_id": value.get("dataset_version_id"),
        "training_run_id": value.get("training_run_id"),
        "attempt_id": value.get("attempt_id"),
        "schema_version": value.get("schema_version", 1),
        "metadata": value.get("metadata", {}),
    }
    return ArtifactDescriptor(**fields)  # type: ignore[arg-type]


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


def _create_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["FINEVISION_S3_ENDPOINT"],
        region_name=os.environ.get("FINEVISION_S3_REGION", "us-east-1"),
        aws_access_key_id=os.environ["FINEVISION_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["FINEVISION_S3_SECRET_KEY"],
        config=Config(s3={"addressing_style": "path"}),
    )


def main() -> None:
    s3_client = _create_s3_client()
    cache_root = os.environ.get("FINEVISION_ARTIFACT_CACHE_DIR", "/data/cache")
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=int(os.environ.get("FINEVISION_INFERENCE_WORKERS", "4"))))
    from finevision.compute.dataset_compute import DatasetComputeService
    from finevision.compute.v1 import dataset_compute_pb2_grpc
    dataset_compute_pb2_grpc.add_DatasetComputeServicer_to_server(
        DatasetComputeService(InferenceRuntimeService(cache_root, s3_client=s3_client)), server,
    )
    inference_runtime_pb2_grpc.add_InferenceRuntimeServicer_to_server(
        InferenceRuntimeService(cache_root, s3_client=s3_client),
        server,
    )
    server.add_insecure_port(os.environ.get("FINEVISION_INFERENCE_GRPC_ADDRESS", "[::]:9100"))
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    main()

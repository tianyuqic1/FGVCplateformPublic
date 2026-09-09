from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.config import Config

from finevision.artifact_store import ArtifactDescriptor, LocalFilesystemArtifactStore, S3ArtifactStore


def descriptor_from_proto(message) -> ArtifactDescriptor:
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


def descriptor_from_dict(value: dict[str, object]) -> ArtifactDescriptor:
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

def create_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["FINEVISION_S3_ENDPOINT"],
        region_name=os.environ.get("FINEVISION_S3_REGION", "us-east-1"),
        aws_access_key_id=os.environ["FINEVISION_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["FINEVISION_S3_SECRET_KEY"],
        config=Config(s3={"addressing_style": "path"}),
    )


class VerifiedArtifactReader:
    """Materialize immutable artifacts without depending on a compute RPC service."""

    def __init__(self, cache_root: str | Path, s3_client=None):
        self.cache_root = Path(cache_root).resolve()
        self.s3_client = s3_client

    def materialize(self, descriptor: ArtifactDescriptor) -> Path:
        parsed = urlparse(descriptor.uri)
        if parsed.scheme == "file":
            store = LocalFilesystemArtifactStore(self.cache_root / "local-source")
        elif parsed.scheme == "s3":
            store = S3ArtifactStore(client=self.s3_client or create_s3_client(), bucket=parsed.netloc)
        else:
            raise ValueError("artifact URI must use file:// or s3://")
        return store.materialize_verified(descriptor, self.cache_root)


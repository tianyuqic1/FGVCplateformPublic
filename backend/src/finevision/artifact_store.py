from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Protocol


class ArtifactIntegrityError(RuntimeError):
    """Raised when stored bytes do not match their canonical descriptor."""


@dataclass(frozen=True)
class ArtifactDescriptor:
    artifact_id: str
    artifact_type: str
    uri: str
    sha256: str
    size_bytes: int
    content_type: str
    storage_version: str
    producer: str
    dataset_version_id: str | None = None
    training_run_id: str | None = None
    attempt_id: str | None = None
    schema_version: int = 1
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    verified_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ArtifactStore(Protocol):
    def put_file(
        self,
        source: str | Path,
        *,
        artifact_id: str,
        artifact_type: str,
        content_type: str,
        producer: str,
        dataset_version_id: str | None = None,
        training_run_id: str | None = None,
        attempt_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactDescriptor: ...

    def materialize_verified(self, descriptor: ArtifactDescriptor, cache_root: str | Path) -> Path: ...


def _digest_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _copy_and_digest(source: BinaryIO, destination: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(1024 * 1024):
        destination.write(chunk)
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


class _VerifiedMaterializer:
    def _open(self, descriptor: ArtifactDescriptor) -> BinaryIO:
        raise NotImplementedError

    def materialize_verified(self, descriptor: ArtifactDescriptor, cache_root: str | Path) -> Path:
        cache_path = Path(cache_root).resolve() / "sha256" / descriptor.sha256
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.exists():
            actual_sha, actual_size = _digest_file(cache_path)
            if actual_sha == descriptor.sha256 and actual_size == descriptor.size_bytes:
                return cache_path
            cache_path.unlink()

        descriptor_sha = descriptor.sha256.lower()
        if len(descriptor_sha) != 64 or any(char not in "0123456789abcdef" for char in descriptor_sha):
            raise ValueError("artifact descriptor sha256 must be a lowercase hexadecimal SHA-256")

        fd, temporary_name = tempfile.mkstemp(prefix=f".{descriptor.sha256}.", dir=cache_path.parent)
        try:
            with os.fdopen(fd, "wb") as destination, self._open(descriptor) as source:
                actual_sha, actual_size = _copy_and_digest(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            if actual_sha != descriptor.sha256 or actual_size != descriptor.size_bytes:
                raise ArtifactIntegrityError(
                    "artifact integrity mismatch: "
                    f"expected sha256={descriptor.sha256} size={descriptor.size_bytes}, "
                    f"actual sha256={actual_sha} size={actual_size}"
                )
            os.replace(temporary_name, cache_path)
            return cache_path
        finally:
            Path(temporary_name).unlink(missing_ok=True)


class LocalFilesystemArtifactStore(_VerifiedMaterializer):
    """Offline adapter with the same immutable, content-addressed semantics as S3."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def put_file(
        self,
        source: str | Path,
        *,
        artifact_id: str,
        artifact_type: str,
        content_type: str,
        producer: str,
        dataset_version_id: str | None = None,
        training_run_id: str | None = None,
        attempt_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactDescriptor:
        source_path = Path(source).resolve()
        sha256, size_bytes = _digest_file(source_path)
        destination = self.root / artifact_type / sha256[:2] / sha256
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            existing_sha, existing_size = _digest_file(destination)
            if existing_sha != sha256 or existing_size != size_bytes:
                raise ArtifactIntegrityError("artifact integrity mismatch at immutable destination")
        else:
            temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
            shutil.copyfile(source_path, temporary)
            os.replace(temporary, destination)
        return ArtifactDescriptor(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            uri=destination.as_uri(),
            sha256=sha256,
            size_bytes=size_bytes,
            content_type=content_type,
            storage_version="local-v1",
            producer=producer,
            dataset_version_id=dataset_version_id,
            training_run_id=training_run_id,
            attempt_id=attempt_id,
            verified_at=datetime.now(UTC).isoformat(),
            metadata=dict(metadata or {}),
        )

    def _open(self, descriptor: ArtifactDescriptor) -> BinaryIO:
        if not descriptor.uri.startswith("file://"):
            raise ValueError("local artifact URI must use file://")
        return Path(descriptor.uri.removeprefix("file://")).open("rb")


class S3Client(Protocol):
    def put_object(self, **kwargs: Any) -> Any: ...
    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...


class S3ArtifactStore(_VerifiedMaterializer):
    """S3-compatible adapter used with MinIO-compatible deployments."""

    def __init__(self, *, client: S3Client, bucket: str, prefix: str = "") -> None:
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    def put_file(
        self,
        source: str | Path,
        *,
        artifact_id: str,
        artifact_type: str,
        content_type: str,
        producer: str,
        dataset_version_id: str | None = None,
        training_run_id: str | None = None,
        attempt_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactDescriptor:
        source_path = Path(source).resolve()
        sha256, size_bytes = _digest_file(source_path)
        key_parts = [part for part in (self.prefix, artifact_type, sha256[:2], sha256) if part]
        key = "/".join(key_parts)
        with source_path.open("rb") as body:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                Metadata={"sha256": sha256},
            )
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        if int(head["ContentLength"]) != size_bytes:
            raise ArtifactIntegrityError("artifact integrity mismatch after S3 upload")
        descriptor = ArtifactDescriptor(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            uri=f"s3://{self.bucket}/{key}",
            sha256=sha256,
            size_bytes=size_bytes,
            content_type=content_type,
            storage_version="s3-v1",
            producer=producer,
            dataset_version_id=dataset_version_id,
            training_run_id=training_run_id,
            attempt_id=attempt_id,
            metadata=dict(metadata or {}),
        )
        with self._open(descriptor) as remote:
            remote_sha, remote_size = _copy_and_digest(remote, _NullWriter())
        if remote_sha != sha256 or remote_size != size_bytes:
            raise ArtifactIntegrityError("artifact integrity mismatch after S3 read-back")
        return ArtifactDescriptor(**{**descriptor.__dict__, "verified_at": datetime.now(UTC).isoformat()})

    def _open(self, descriptor: ArtifactDescriptor) -> BinaryIO:
        prefix = f"s3://{self.bucket}/"
        if not descriptor.uri.startswith(prefix):
            raise ValueError("artifact URI does not belong to configured S3 bucket")
        response = self.client.get_object(Bucket=self.bucket, Key=descriptor.uri.removeprefix(prefix))
        return response["Body"]


class _NullWriter:
    def write(self, data: bytes) -> int:
        return len(data)

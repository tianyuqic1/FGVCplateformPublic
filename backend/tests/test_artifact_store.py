from __future__ import annotations

import io
from pathlib import Path

import pytest

from finevision.artifact_store import (
    ArtifactIntegrityError,
    LocalFilesystemArtifactStore,
    S3ArtifactStore,
)


class MemoryS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: object, **_: object) -> None:
        payload = Body.read() if hasattr(Body, "read") else Body
        self.objects[(Bucket, Key)] = bytes(payload)

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, int]:
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, io.BytesIO]:
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


def stores(tmp_path: Path):
    yield LocalFilesystemArtifactStore(tmp_path / "objects")
    yield S3ArtifactStore(
        client=MemoryS3Client(),
        bucket="finevision-artifacts",
        prefix="tests",
    )


@pytest.mark.parametrize("payload", [b"model bytes", b"\x00\x01feature bytes"])
def test_artifact_stores_publish_and_materialize_verified_bytes(tmp_path: Path, payload: bytes) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(payload)

    for store in stores(tmp_path):
        descriptor = store.put_file(
            source,
            artifact_id="artifact-1",
            artifact_type="model",
            content_type="application/octet-stream",
            producer="pytest",
            training_run_id="run-1",
            attempt_id="attempt-1",
        )

        assert descriptor.sha256 == __import__("hashlib").sha256(payload).hexdigest()
        assert descriptor.size_bytes == len(payload)
        assert descriptor.uri.startswith(("file://", "s3://finevision-artifacts/"))
        assert descriptor.schema_version == 1

        materialized = store.materialize_verified(descriptor, tmp_path / "cache")
        assert materialized.read_bytes() == payload
        assert materialized.name == descriptor.sha256


def test_materialize_fails_closed_when_object_bytes_are_corrupted(tmp_path: Path) -> None:
    source = tmp_path / "model.bin"
    source.write_bytes(b"expected")
    store = LocalFilesystemArtifactStore(tmp_path / "objects")
    descriptor = store.put_file(
        source,
        artifact_id="artifact-1",
        artifact_type="model",
        content_type="application/octet-stream",
        producer="pytest",
    )
    Path(descriptor.uri.removeprefix("file://")).write_bytes(b"corrupted")

    with pytest.raises(ArtifactIntegrityError, match="integrity mismatch"):
        store.materialize_verified(descriptor, tmp_path / "cache")

    assert not (tmp_path / "cache" / "sha256" / descriptor.sha256).exists()


def test_corrupted_sha_cache_is_replaced_from_verified_object(tmp_path: Path) -> None:
    source = tmp_path / "model.bin"
    source.write_bytes(b"canonical")
    store = LocalFilesystemArtifactStore(tmp_path / "objects")
    descriptor = store.put_file(
        source,
        artifact_id="artifact-1",
        artifact_type="model",
        content_type="application/octet-stream",
        producer="pytest",
    )
    cached = tmp_path / "cache" / "sha256" / descriptor.sha256
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"tampered")

    result = store.materialize_verified(descriptor, tmp_path / "cache")

    assert result.read_bytes() == b"canonical"

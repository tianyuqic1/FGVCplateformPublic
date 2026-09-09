import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from google.protobuf.json_format import MessageToDict

from finevision.compute.dataset_compute import DatasetComputeService, unpack_dataset
from finevision.compute.artifacts import VerifiedArtifactReader
from finevision.compute.v1.dataset_compute_pb2 import ScanDatasetRequest
from finevision.compute.v1.artifact_pb2 import ArtifactDescriptor
from finevision.artifact_store import LocalFilesystemArtifactStore


def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize("name", ["../escape.png", "/escape.png", "a/../../escape.png", "a\\escape.png", "image.png"])
def test_unpack_rejects_unsafe_paths(tmp_path, name):
    archive = tmp_path / "upload.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr(name, image_bytes())
    with pytest.raises(ValueError):
        unpack_dataset(archive, tmp_path / "out")


def test_unpack_rejects_corrupt_image(tmp_path):
    archive = tmp_path / "upload.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("class/image.png", b"not an image")
    with pytest.raises(OSError):
        unpack_dataset(archive, tmp_path / "out")


def test_scan_uses_verified_artifact_and_returns_portable_manifest(tmp_path):
    import hashlib
    archive = tmp_path / "upload.zip"
    with zipfile.ZipFile(archive, "w") as target:
        for label in ["red", "blue"]:
            for index in range(4):
                target.writestr(f"{label}/{index}.png", image_bytes())
    descriptor = ArtifactDescriptor(
        artifact_id="archive", artifact_type="dataset_archive", uri=archive.as_uri(),
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), size_bytes=archive.stat().st_size,
        content_type="application/zip", schema_version=1, dataset_version_id="version",
    )
    runtime = VerifiedArtifactReader(tmp_path / "cache")
    class Context:
        def abort(self, code, message):
            raise RuntimeError(message)
    result = DatasetComputeService(runtime).Scan(ScanDatasetRequest(
        archive=descriptor, dataset_id="dataset", dataset_version_id="version",
    ), Context())
    manifest = MessageToDict(result)
    assert manifest["readiness"]["sample_count"] == 8
    assert manifest["readiness"]["ready"] is True
    assert manifest["classes"] == ["blue", "red"]
    assert manifest["root"] == archive.as_uri()
    assert all(not Path(sample["path"]).is_absolute() for sample in manifest["samples"])
    descriptor.sha256 = "0" * 64
    with pytest.raises(RuntimeError, match="ArtifactIntegrityError"):
        DatasetComputeService(runtime).Scan(ScanDatasetRequest(archive=descriptor), Context())

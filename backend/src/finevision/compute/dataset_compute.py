from __future__ import annotations

from pathlib import Path, PurePosixPath
from dataclasses import asdict
import shutil
import tempfile
import zipfile

import grpc
from google.protobuf.struct_pb2 import Struct
from PIL import Image

from finevision.compute.v1 import dataset_compute_pb2_grpc
from finevision.compute.artifacts import descriptor_from_proto
from finevision.ml_toolkit.datasets import scan_imagefolder, IMAGE_EXTENSIONS


def unpack_dataset(archive: Path, destination: Path) -> None:
    """Extract bounded ImageFolder bytes; never trust archive member paths."""
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if not members or len(members) > 100_000 or sum(m.file_size for m in members) > 5 * 1024 * 1024 * 1024:
            raise ValueError("ImageFolder exceeds 100000 files or 5 GB")
        seen = set()
        for member in members:
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in member.filename or len(path.parts) < 2:
                raise ValueError("Invalid ImageFolder relative path")
            if member.filename in seen or member.is_dir() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                raise ValueError("Invalid or duplicate image entry")
            seen.add(member.filename)
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as incoming, target.open("wb") as output:
                shutil.copyfileobj(incoming, output)
            with Image.open(target) as image:
                image.verify()


class DatasetComputeService(dataset_compute_pb2_grpc.DatasetComputeServicer):
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def Scan(self, request, context):
        try:
            archive = self.artifacts.materialize(descriptor_from_proto(request.archive))
            with tempfile.TemporaryDirectory(prefix="finevision-dataset-") as directory:
                root = Path(directory)
                unpack_dataset(archive, root)
                manifest = scan_imagefolder(root, request.dataset_id, request.dataset_version_id)
                data = asdict(manifest)
                data["root"] = request.archive.uri
                for sample in data["samples"]:
                    sample["path"] = Path(sample["path"]).relative_to(root).as_posix()
                result = Struct()
                result.update(data)
                return result
        except Exception as error:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"ImageFolder scan failed: {type(error).__name__}")

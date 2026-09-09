import json
from dataclasses import asdict
from uuid import uuid4

import numpy as np
import pytest

from finevision.artifact_store import LocalFilesystemArtifactStore
from finevision.compute.artifacts import VerifiedArtifactReader
from finevision.compute.model_export import export_linear_head, ModelExportService
from finevision.compute.v1.model_export_pb2 import ExportHeadRequest
from finevision.compute.v1.artifact_pb2 import ArtifactDescriptor
from google.protobuf.json_format import MessageToDict


def write_head(path):
    rng = np.random.default_rng(7)
    np.savez(path, weights=rng.normal(size=(384, 3)).astype(np.float32), bias=np.zeros(3,np.float32),
             feature_mean=np.ones(384,np.float32), feature_std=np.zeros(384,np.float32))


def test_vits_head_export_roundtrip_and_class_mapping(tmp_path):
    source = tmp_path / "head.npz"
    write_head(source)
    pt, onnx, report = export_linear_head(source,tmp_path / "export",["猫","鸟","狗"],str(uuid4()))
    assert pt.stat().st_size > 0 and onnx.stat().st_size > 0
    assert report["parity_passed"] and report["feature_dim"] == 384
    assert report["classes"] == ["猫","鸟","狗"]
    with pytest.raises(ValueError,match="class mapping"):
        export_linear_head(source,tmp_path / "invalid",["cat"],str(uuid4()))


class Context:
    def is_active(self): return True
    def abort(self,code,message): raise RuntimeError(message)


def test_export_rpc_uses_verified_sources_and_preserves_lineage(tmp_path):
    store=LocalFilesystemArtifactStore(tmp_path / "objects")
    reader=VerifiedArtifactReader(tmp_path / "cache")
    dv,tr,mv=[str(uuid4()) for _ in range(3)]
    source=tmp_path / "head.npz"
    write_head(source)
    head=store.put_file(source,artifact_id=str(uuid4()),artifact_type="model",content_type="application/octet-stream",producer="test",dataset_version_id=dv,training_run_id=tr)
    bundle_path=tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps({"model":asdict(head),"model_artifact":{"dataset_version_id":dv,"classes":["a","b","c"]}}))
    bundle=store.put_file(bundle_path,artifact_id=str(uuid4()),artifact_type="model_bundle",content_type="application/json",producer="test",dataset_version_id=dv,training_run_id=tr)
    request=ExportHeadRequest(model_version_id=mv,dataset_version_id=dv,training_run_id=tr,source_bundle=ArtifactDescriptor(
        artifact_id=bundle.artifact_id,artifact_type=bundle.artifact_type,uri=bundle.uri,sha256=bundle.sha256,size_bytes=bundle.size_bytes,
        content_type=bundle.content_type,dataset_version_id=dv,training_run_id=tr))
    service=ModelExportService(reader,store)
    result=MessageToDict(service.ExportHead(request,Context()))
    assert {a["artifact_type"] for a in result["artifacts"]}=={"head_pt","head_onnx"}
    assert all(a["metadata"]["model_version_id"]==mv and a["dataset_version_id"]==dv for a in result["artifacts"])
    request.source_bundle.sha256="0"*64
    with pytest.raises(RuntimeError,match="ArtifactIntegrityError"):
        service.ExportHead(request,Context())
    request.source_bundle.dataset_version_id=str(uuid4())
    with pytest.raises(RuntimeError,match="ValueError"):
        service.ExportHead(request,Context())

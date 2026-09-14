import json
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from finevision.artifact_store import LocalFilesystemArtifactStore, ArtifactIntegrityError
from finevision.compute.deployment_build import validate_engine
from finevision.compute.deployment_sessions import Sessions
from finevision.compute.deployment_worker import Worker


def test_real_onnx_cpu_session_cache_and_eviction(tmp_path):
    import onnx
    from onnx import helper, TensorProto
    graph = helper.make_graph([helper.make_node("ReduceMean", ["images"], ["logits"], axes=[2, 3], keepdims=0)], "tiny", [helper.make_tensor_value_info("images", TensorProto.FLOAT, ["N",3,4,4])], [helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["N",3])])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("",17)]); model.ir_version=9
    source = tmp_path / "tiny.onnx"; onnx.save(model, source)
    store = LocalFilesystemArtifactStore(tmp_path / "objects")
    descriptor = store.put_file(source, artifact_id="cpu",artifact_type="full_onnx",content_type="application/octet-stream",producer="test")
    cache = Sessions(maximum=1)
    def reader(d):
        return store.materialize_verified(d,tmp_path / "cache")
    inputs = np.ones((1,3,4,4),np.float32)
    np.testing.assert_allclose(cache.run(descriptor,reader,inputs),[[1,1,1]])
    first = cache.sessions[descriptor.sha256]
    cache.run(descriptor,reader,inputs); assert cache.sessions[descriptor.sha256] is first
    from pathlib import Path
    Path(descriptor.uri.removeprefix("file://")).write_bytes(b"bad")
    (tmp_path / "cache" / "sha256" / descriptor.sha256).write_bytes(b"bad")
    with pytest.raises(ArtifactIntegrityError): cache.run(descriptor,reader,inputs)
    assert cache.evict(descriptor.sha256)
    assert not cache.evict(descriptor.sha256)


@pytest.mark.parametrize("precision", ["FP32", "FP16"])
def test_numeric_gate_and_rejection(precision):
    ref=SimpleNamespace(get_inputs=lambda:[SimpleNamespace(name="images")],run=lambda _,d:[d["images"].mean((2,3))])
    engine=SimpleNamespace(run=lambda x:x.mean((2,3)))
    report=validate_engine(ref,engine,[3,4,4],precision,3)
    assert report["parity_passed"] and not report["real_dataset_evaluated"]
    assert report["top1_agreement"]==1
    engine.run=lambda x:x.mean((2,3))+100
    with pytest.raises(AssertionError):validate_engine(ref,engine,[3,4,4],precision,1)
    engine.run=lambda x:np.full((len(x),3),np.nan)
    with pytest.raises(ValueError):validate_engine(ref,engine,[3,4,4],precision,1)


def test_fp16_hardware_gate_accepts_bounded_tactic_drift():
    ref = SimpleNamespace(
        get_inputs=lambda: [SimpleNamespace(name="images")],
        run=lambda _, data: [np.tile(np.array([[7.0, 3.0, -2.0]], np.float32), (len(data["images"]), 1))],
    )
    engine = SimpleNamespace(
        run=lambda images: np.tile(np.array([[6.8, 3.1, -1.9]], np.float32), (len(images), 1))
    )
    report = validate_engine(ref, engine, [3, 4, 4], "FP16", 2)
    assert report["parity_passed"]
    assert report["top1_agreement"] == 1
    assert report["max_abs_error"] == pytest.approx(0.2)


def test_fp16_hardware_gate_rejects_material_drift_even_when_top1_matches():
    ref = SimpleNamespace(
        get_inputs=lambda: [SimpleNamespace(name="images")],
        run=lambda _, data: [np.tile(np.array([[7.0, 3.0, -2.0]], np.float32), (len(data["images"]), 1))],
    )
    engine = SimpleNamespace(
        run=lambda images: np.tile(np.array([[6.0, 3.0, -2.0]], np.float32), (len(images), 1))
    )
    with pytest.raises(AssertionError, match="relative logit drift"):
        validate_engine(ref, engine, [3, 4, 4], "FP16", 1)


def test_compiled_fail_closed_before_loading(monkeypatch):
    from finevision.compute import deployment_backends as backends
    monkeypatch.setattr(backends,"fingerprint",lambda runtime:{"device":"test"})
    monkeypatch.setenv("FINEVISION_TARGET_PROFILE","sm89")
    descriptor=SimpleNamespace(metadata={"runtime":"tensorrt","runtime_fingerprint":{"device":"other"}})
    deployment={"runtime":"tensorrt","target_profile":"sm89","max_batch":1}
    read=lambda d:pytest.fail("should reject before artifact loading")
    with pytest.raises(ValueError,match="incompatible"):Sessions("tensorrt").run(descriptor,read,np.zeros((1,3,4,4)),deployment)
    with pytest.raises(ValueError,match="wrong runtime"):Sessions("ascend_acl").run(descriptor,read,np.zeros((1,3,4,4)),deployment)
    with pytest.raises(ValueError,match="requires a compiled"):Sessions("tensorrt").run(descriptor,read,np.zeros((1,3,4,4)))
    descriptor.metadata["runtime_fingerprint"]={"device":"test"}
    with pytest.raises(ValueError,match="batch"):Sessions("tensorrt").run(descriptor,read,np.zeros((2,3,4,4)),deployment)


def test_worker_dead_letters_bad_message():
    calls=[]; worker=object.__new__(Worker)
    channel=SimpleNamespace(basic_reject=lambda *a,**k:calls.append(k))
    for body in [b"bad",b"{}",b"[]",b'{"deployment_id":"wrong"}']:
        worker.consume(None,channel,SimpleNamespace(delivery_tag=1),body)
    assert len(calls)==4 and all(v=={"requeue":False} for v in calls)


def test_worker_permanent_registration_failure_becomes_failed():
    import urllib.error
    calls=[];worker=object.__new__(Worker)
    def callback(message,action,**body):
        calls.append(body)
        if "artifact" in body:raise urllib.error.HTTPError("http://test",422,"invalid artifact",{},None)
    worker.callback=callback
    worker.finish({}, {"artifact": {"bad": True}})
    assert len(calls)==2 and calls[1].get("error")
    def fenced(*a,**kw):raise urllib.error.HTTPError("http://test",409,"old generation",{},None)
    worker.callback=fenced
    worker.finish({}, {"artifact": {}})


def test_worker_replays_completed_result_without_compiling(tmp_path,monkeypatch):
    monkeypatch.setenv("FINEVISION_BUILD_ROOT",str(tmp_path))
    message={"deployment_id":str(uuid4()),"build_token":str(uuid4())}
    root=tmp_path/message["deployment_id"]/message["build_token"];root.mkdir(parents=True)
    (root/"result.json").write_text(json.dumps({"artifact":{"id":"persisted"},"validation":{}}))
    calls=[];worker=object.__new__(Worker);worker.runtime="tensorrt";worker.profile="sm89"
    def callback(msg,action,**kw):
        calls.append((action,kw));return {"deployment":{}}
    worker.callback=callback
    channel=SimpleNamespace(basic_ack=lambda tag:calls.append(("ack",tag)))
    monkeypatch.setattr("subprocess.Popen",lambda *a,**k:pytest.fail("compiled twice"))
    worker.consume(None,channel,SimpleNamespace(delivery_tag=1),json.dumps(message))
    assert [x[0] for x in calls]==["claim","complete","ack"]
    assert calls[1][1]["artifact"]["id"]=="persisted"


def test_build_pipeline_persists_verified_artifact_and_report(tmp_path,monkeypatch):
    """Real ONNX reference and object storage; only the unavailable GPU compiler is faked."""
    import onnx
    import onnxruntime as ort
    from dataclasses import asdict
    from onnx import helper, TensorProto
    from finevision.compute import deployment_build as builder
    from finevision.compute.artifacts import VerifiedArtifactReader
    graph=helper.make_graph([helper.make_node("ReduceMean",["images"],["logits"],axes=[2,3],keepdims=0)],"build_probe",[helper.make_tensor_value_info("images",TensorProto.FLOAT,["N",3,4,4])],[helper.make_tensor_value_info("logits",TensorProto.FLOAT,["N",3])])
    model=helper.make_model(graph,opset_imports=[helper.make_opsetid("",17)]);model.ir_version=9
    source=tmp_path/"source.onnx";onnx.save(model,source)
    store=LocalFilesystemArtifactStore(tmp_path/"objects")
    descriptor=store.put_file(source,artifact_id="source",artifact_type="full_onnx",content_type="application/octet-stream",producer="test",dataset_version_id="dataset",training_run_id="run",metadata={"preprocessing":{"input_size":[3,4,4]},"classes":["a","b","c"]})
    def compile(source,output,*args):
        import shutil
        shutil.copyfile(source,output)
    class FakeEngine:
        def __init__(self,path):
            opts=ort.SessionOptions();opts.intra_op_num_threads=1
            self.session=ort.InferenceSession(str(path),opts,providers=["CPUExecutionProvider"])
        def run(self,images):return self.session.run(None,{"images":images})[0]
        def close(self):pass
    monkeypatch.setattr(builder,"build_tensorrt",compile)
    monkeypatch.setattr(builder,"TensorRTEngine",FakeEngine)
    monkeypatch.setattr(builder,"fingerprint",lambda _: {"test":"fake-gpu"})
    job={"id":"deployment","source":asdict(descriptor),"runtime":"tensorrt","precision":"FP16","max_batch":2,"target_profile":"test"}
    result=builder.build(job,tmp_path,VerifiedArtifactReader(tmp_path/"cache"),store)
    assert result["artifact"]["artifact_type"]=="tensorrt_engine"
    assert result["artifact"]["metadata"]["source_onnx_sha256"]==descriptor.sha256
    assert result["validation"]["parity_passed"] and not result["validation"]["real_dataset_evaluated"]
    assert result["artifact"]["dataset_version_id"]=="dataset"

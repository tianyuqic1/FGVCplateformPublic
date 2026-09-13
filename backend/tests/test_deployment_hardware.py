"""Opt-in real hardware gates. Never allocate GPU/NPU during normal unit tests."""
import os
import numpy as np
import pytest

@pytest.mark.parametrize("runtime", ["tensorrt", "ascend_acl"])
@pytest.mark.parametrize("precision", ["FP32", "FP16"])
def test_real_hardware_compile_and_infer(tmp_path,runtime,precision):
    if os.environ.get("FINEVISION_TEST_HARDWARE") != runtime:
        pytest.skip(f"set FINEVISION_TEST_HARDWARE={runtime} on the target host")
    import onnx
    import onnxruntime as ort
    from onnx import helper, TensorProto
    from finevision.compute.deployment_backends import build_tensorrt, build_ascend, TensorRTEngine, AscendEngine
    from finevision.compute.deployment_build import validate_engine
    graph=helper.make_graph([helper.make_node("ReduceMean",["images"],["logits"],axes=[2,3],keepdims=0)],"hardware_probe",[helper.make_tensor_value_info("images",TensorProto.FLOAT,["N",3,32,32])],[helper.make_tensor_value_info("logits",TensorProto.FLOAT,["N",3])])
    model=helper.make_model(graph,opset_imports=[helper.make_opsetid("",17)]);model.ir_version=9
    source=tmp_path/"probe.onnx";onnx.save(model,source)
    opts=ort.SessionOptions();opts.intra_op_num_threads=2
    reference=ort.InferenceSession(str(source),opts,providers=["CPUExecutionProvider"])
    if runtime=="tensorrt":
        output=tmp_path/"probe.plan";batch=2
        build_tensorrt(source,output,[3,32,32],precision,batch);engine=TensorRTEngine(output)
    else:
        output=tmp_path/"probe.om";batch=1
        build_ascend(source,output,[3,32,32],precision,batch,tmp_path/"atc.log");engine=AscendEngine(output)
    try:
        assert validate_engine(reference,engine,[3,32,32],precision,batch)["parity_passed"]
        assert engine.run(np.ones((1,3,32,32),np.float32)).shape==(1,3)
    finally:engine.close()

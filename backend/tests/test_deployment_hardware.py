"""Opt-in real hardware gates. Never allocate GPU/NPU during normal unit tests."""
import os
import numpy as np
import pytest


def test_tensorrt_109_none_shape_result_is_not_treated_as_failure(tmp_path, monkeypatch):
    """Regression for TensorRT 10.9's successful set_shape(None) binding."""
    from finevision.compute import deployment_backends as backends
    source, output = tmp_path / "model.onnx", tmp_path / "model.plan"
    source.write_bytes(b"onnx")
    calls = []

    class Profile:
        def set_shape(self, *args):
            calls.append(args)
            return None

    class Config:
        def set_memory_pool_limit(self, *args): pass
        def clear_flag(self, *args): pass
        def add_optimization_profile(self, profile): pass

    class Network:
        num_inputs = num_outputs = 1
        def get_input(self, index): return type("Input", (), {"name": "images"})()

    class Builder:
        platform_has_fast_fp16 = True
        def __init__(self, logger): pass
        def create_network(self, flags): return Network()
        def create_builder_config(self): return Config()
        def create_optimization_profile(self): return Profile()
        def build_serialized_network(self, network, config): return b"engine"

    class Parser:
        num_errors = 0
        def __init__(self, network, logger): pass
        def parse(self, data): return True

    fake = type("TRT", (), {
        "__version__": "10.9.0.34", "Logger": type("Logger", (), {"WARNING": 1, "__init__": lambda self, level: None}),
        "Builder": Builder, "OnnxParser": Parser,
        "NetworkDefinitionCreationFlag": type("N", (), {"EXPLICIT_BATCH": 0}),
        "MemoryPoolType": type("M", (), {"WORKSPACE": 0}),
        "BuilderFlag": type("F", (), {"TF32": 0, "FP16": 1}),
    })
    fake_torch = type("Torch", (), {"cuda": type("Cuda", (), {"device": lambda index: __import__("contextlib").nullcontext()})})
    monkeypatch.setitem(__import__("sys").modules, "tensorrt", fake)
    monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)
    backends.build_tensorrt(source, output, [3, 224, 224], "FP32", 1)
    assert output.read_bytes() == b"engine"
    assert calls == [("images", (1, 3, 224, 224), (1, 3, 224, 224), (1, 3, 224, 224))]


def test_tensorrt_fp16_keeps_transformer_sensitive_layers_in_fp32(tmp_path, monkeypatch):
    from finevision.compute import deployment_backends as backends
    source, output = tmp_path / "model.onnx", tmp_path / "model.plan"
    source.write_bytes(b"onnx")

    class Layer:
        def __init__(self, kind):
            self.type, self.num_outputs, self.precision, self.output_types = kind, 1, None, []
        def set_output_type(self, index, dtype): self.output_types.append((index, dtype))

    kinds = type("LayerType", (), {"NORMALIZATION": 1, "SOFTMAX": 2, "REDUCE": 3, "MATRIX_MULTIPLY": 4})
    layers = [Layer(kinds.NORMALIZATION), Layer(kinds.SOFTMAX), Layer(kinds.MATRIX_MULTIPLY)]

    class Profile:
        def set_shape(self, *args): return True
    class Config:
        def set_memory_pool_limit(self, *args): pass
        def clear_flag(self, *args): pass
        def set_flag(self, flag): pass
        def add_optimization_profile(self, profile): pass
    class Network:
        num_inputs, num_outputs, num_layers = 1, 1, len(layers)
        def get_input(self, index): return type("Input", (), {"name": "images"})()
        def get_layer(self, index): return layers[index]
    class Builder:
        platform_has_fast_fp16 = True
        def __init__(self, logger): pass
        def create_network(self, flags): return Network()
        def create_builder_config(self): return Config()
        def create_optimization_profile(self): return Profile()
        def build_serialized_network(self, network, config): return b"engine"
    class Parser:
        num_errors = 0
        def __init__(self, network, logger): pass
        def parse(self, data): return True

    fake = type("TRT", (), {
        "__version__": "10.9.0.34", "float32": "fp32", "LayerType": kinds,
        "Logger": type("Logger", (), {"WARNING": 1, "__init__": lambda self, level: None}),
        "Builder": Builder, "OnnxParser": Parser,
        "NetworkDefinitionCreationFlag": type("N", (), {"EXPLICIT_BATCH": 0}),
        "MemoryPoolType": type("M", (), {"WORKSPACE": 0}),
        "BuilderFlag": type("F", (), {"TF32": 0, "FP16": 1, "OBEY_PRECISION_CONSTRAINTS": 2}),
    })
    fake_torch = type("Torch", (), {"cuda": type("Cuda", (), {"device": lambda index: __import__("contextlib").nullcontext()})})
    monkeypatch.setitem(__import__("sys").modules, "tensorrt", fake)
    monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)
    backends.build_tensorrt(source, output, [3, 224, 224], "FP16", 1)
    assert layers[0].precision == layers[1].precision == "fp32"
    assert layers[0].output_types == layers[1].output_types == [(0, "fp32")]
    assert layers[2].precision is None

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

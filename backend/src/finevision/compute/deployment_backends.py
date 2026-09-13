"""Hardware adapters with float32 NCHW input and float32 logits output.

Heavy vendor imports are lazy: the CPU process never imports CUDA or AscendCL.
An engine instance must be serialized by its caller (one execution context).
"""
import os
import platform
from pathlib import Path

import numpy as np


def fingerprint(runtime):
    base = {"runtime": runtime, "machine": platform.machine(), "system": platform.system()}
    if runtime == "tensorrt":
        import tensorrt as trt
        import torch
        device = int(os.environ.get("FINEVISION_DEVICE_ID", "0"))
        if not torch.cuda.is_available():
            raise RuntimeError("NVIDIA CUDA device is unavailable")
        base.update(version=trt.__version__, cuda=torch.version.cuda,
                    gpu=torch.cuda.get_device_name(device), sm=list(torch.cuda.get_device_capability(device)))
    elif runtime == "ascend_acl":
        import acl
        version = acl.get_version()
        base.update(version=list(version), soc=acl.get_soc_name())
    else:
        raise ValueError("unknown compiled runtime")
    return base


class TensorRTEngine:
    def __init__(self, path):
        import tensorrt as trt
        import torch
        self.trt, self.torch = trt, torch
        self.device = int(os.environ.get("FINEVISION_DEVICE_ID", "0"))
        self.logger = trt.Logger(trt.Logger.WARNING)
        with torch.cuda.device(self.device):
            self.runtime = trt.Runtime(self.logger)
            self.engine = self.runtime.deserialize_cuda_engine(Path(path).read_bytes())
            if self.engine is None:
                raise ValueError("TensorRT engine cannot be loaded on this hardware/runtime")
            self.context = self.engine.create_execution_context()
            if self.context is None:
                raise RuntimeError("TensorRT execution context allocation failed")
            self.stream = torch.cuda.Stream(device=self.device)
        inputs, outputs = [], []
        for i in range(self.engine.num_io_tensors):
            name = self.engine.get_tensor_name(i)
            (inputs if self.engine.get_tensor_mode(name) == trt.TensorIOMode.INPUT else outputs).append(name)
        if len(inputs) != 1 or len(outputs) != 1:
            raise ValueError("only single-input single-output classifiers are supported")
        self.input, self.output = inputs[0], outputs[0]

    def run(self, images):
        torch, trt = self.torch, self.trt
        if images.dtype != np.float32 or images.ndim != 4:
            raise ValueError("expected float32 NCHW input")
        with torch.cuda.device(self.device), torch.cuda.stream(self.stream):
            input_dtype = np.dtype(trt.nptype(self.engine.get_tensor_dtype(self.input)))
            array = np.ascontiguousarray(images, dtype=input_dtype)
            tensor = torch.from_numpy(array).to(device=f"cuda:{self.device}")
            if not self.context.set_input_shape(self.input, tuple(images.shape)):
                raise ValueError("input does not fit TensorRT optimization profile")
            output_shape = tuple(self.context.get_tensor_shape(self.output))
            if len(output_shape) != 2 or min(output_shape) <= 0:
                raise ValueError("invalid TensorRT logits shape")
            output_dtype = torch.from_numpy(np.empty((), dtype=trt.nptype(self.engine.get_tensor_dtype(self.output)))).dtype
            output = torch.empty(output_shape, dtype=output_dtype, device=f"cuda:{self.device}")
            for name, value in ((self.input, tensor), (self.output, output)):
                if not self.context.set_tensor_address(name, value.data_ptr()):
                    raise RuntimeError("TensorRT tensor binding failed")
            if not self.context.execute_async_v3(self.stream.cuda_stream):
                raise RuntimeError("TensorRT execution failed")
            self.stream.synchronize()
            return output.float().cpu().numpy()

    def close(self):
        self.stream.synchronize()
        self.context = None
        self.engine = None
        self.runtime = None


def build_tensorrt(source, output, shape, precision, max_batch):
    import tensorrt as trt
    import torch
    if not trt.__version__.startswith("10."):
        raise ValueError("this adapter requires TensorRT 10.x APIs; use the pinned image")
    device = int(os.environ.get("FINEVISION_DEVICE_ID", "0"))
    with torch.cuda.device(device):
        logger = trt.Logger(trt.Logger.WARNING)
        builder = trt.Builder(logger)
        network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
        parser = trt.OnnxParser(network, logger)
        if not parser.parse(Path(source).read_bytes()):
            raise ValueError("ONNX parser: " + "; ".join(str(parser.get_error(i)) for i in range(min(parser.num_errors, 8))))
        if network.num_inputs != 1 or network.num_outputs != 1:
            raise ValueError("only full image classifiers are supported")
        config = builder.create_builder_config()
        config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, int(os.environ.get("FINEVISION_TRT_WORKSPACE_MB", "512")) << 20)
        config.clear_flag(trt.BuilderFlag.TF32)
        if precision == "FP16":
            if not builder.platform_has_fast_fp16:
                raise ValueError("GPU has no fast FP16 support")
            config.set_flag(trt.BuilderFlag.FP16)
        profile = builder.create_optimization_profile()
        if not profile.set_shape(network.get_input(0).name, (1, *shape), (1, *shape), (max_batch, *shape)):
            raise ValueError("invalid TensorRT shape profile")
        config.add_optimization_profile(profile)
        serialized = builder.build_serialized_network(network, config)
        if serialized is None:
            raise RuntimeError("TensorRT build failed; inspect worker build log")
        Path(output).write_bytes(bytes(serialized))


_ACL_INITIALIZED = False


class AscendEngine:
    """Static batch=1 OM adapter using PyACL; no torch_npu dependency."""
    def __init__(self, path):
        import acl
        global _ACL_INITIALIZED
        self.acl = acl
        if not _ACL_INITIALIZED:
            self.check(acl.init(), "init")
            _ACL_INITIALIZED = True
        self.device = int(os.environ.get("FINEVISION_DEVICE_ID", "0"))
        self.context = self.model_id = self.desc = None
        self.buffers = []
        self.datasets = []
        try:
            self.check(acl.rt.set_device(self.device), "set_device")
            self.context, ret = acl.rt.create_context(self.device)
            self.check(ret, "create_context")
            self.model_id, ret = acl.mdl.load_from_file(str(path))
            self.check(ret, "load_model")
            self.desc = acl.mdl.create_desc()
            self.check(acl.mdl.get_desc(self.desc, self.model_id), "get_desc")
            if acl.mdl.get_num_inputs(self.desc) != 1 or acl.mdl.get_num_outputs(self.desc) != 1:
                raise ValueError("Ascend adapter requires one input and one logits output")
            self.input_shape = self.dims("input")
            self.output_shape = self.dims("output")
            self.input_dtype = self.dtype(acl.mdl.get_input_data_type(self.desc, 0))
            self.output_dtype = self.dtype(acl.mdl.get_output_data_type(self.desc, 0))
            for kind in ("input", "output"):
                dataset = acl.mdl.create_dataset()
                self.datasets.append(dataset)
                size = getattr(acl.mdl, f"get_{kind}_size_by_index")(self.desc, 0)
                ptr, ret = acl.rt.malloc(size, 2)  # ACL_MEM_MALLOC_NORMAL_ONLY
                self.check(ret, "malloc")
                buffer = acl.create_data_buffer(ptr, size)
                self.buffers.append((ptr, size, buffer))
                _, ret = acl.mdl.add_dataset_buffer(dataset, buffer)
                self.check(ret, "add_dataset_buffer")
        except Exception:
            self.close()
            raise

    @staticmethod
    def check(ret, operation):
        if ret != 0:
            raise RuntimeError(f"AscendCL {operation} failed: {ret}")

    @staticmethod
    def dtype(value):
        if value not in (0, 1):  # ACL_FLOAT, ACL_FLOAT16
            raise ValueError("unsupported OM input/output dtype")
        return np.float32 if value == 0 else np.float16

    def dims(self, kind):
        result, ret = getattr(self.acl.mdl, f"get_{kind}_dims")(self.desc, 0)
        self.check(ret, "get_dims")
        shape = tuple(result["dims"])
        if not shape or min(shape) <= 0:
            raise ValueError("dynamic OM shapes are not supported in this release")
        return shape

    def run(self, images):
        acl = self.acl
        self.check(acl.rt.set_context(self.context), "set_context")
        if tuple(images.shape) != self.input_shape:
            raise ValueError("image shape does not match compiled OM")
        inputs = np.ascontiguousarray(images, dtype=self.input_dtype)
        outputs = np.empty(self.output_shape, dtype=self.output_dtype)
        inp, out = self.buffers
        if inputs.nbytes != inp[1] or outputs.nbytes != out[1]:
            raise ValueError("OM tensor byte size mismatch")
        self.check(acl.rt.memcpy(inp[0], inp[1], inputs.ctypes.data, inputs.nbytes, 1), "copy_input")
        self.check(acl.mdl.execute(self.model_id, self.datasets[0], self.datasets[1]), "execute")
        self.check(acl.rt.memcpy(outputs.ctypes.data, outputs.nbytes, out[0], out[1], 2), "copy_output")
        return outputs.astype(np.float32)

    def close(self):
        acl = self.acl
        if self.context is not None:
            acl.rt.set_context(self.context)
        for ptr, _, buffer in self.buffers:
            if buffer is not None:
                acl.destroy_data_buffer(buffer)
            acl.rt.free(ptr)
        for dataset in self.datasets:
            acl.mdl.destroy_dataset(dataset)
        self.buffers, self.datasets = [], []
        if self.desc is not None:
            acl.mdl.destroy_desc(self.desc)
            self.desc = None
        if self.model_id is not None:
            acl.mdl.unload(self.model_id)
            self.model_id = None
        if self.context is not None:
            acl.rt.destroy_context(self.context)
            self.context = None


def build_ascend(source, output, shape, precision, max_batch, log_path):
    import subprocess
    if max_batch != 1:
        raise ValueError("Ascend first release supports static batch=1")
    import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    reference = ort.InferenceSession(str(source), opts, providers=["CPUExecutionProvider"])
    name = reference.get_inputs()[0].name
    soc = fingerprint("ascend_acl")["soc"]
    command = ["atc", f"--model={source}", "--framework=5", f"--output={Path(output).with_suffix('')}",
               f"--soc_version={soc}", "--input_format=NCHW", f"--input_shape={name}:1,{','.join(map(str, shape))}",
               "--precision_mode=" + ("force_fp16" if precision == "FP16" else "must_keep_origin_dtype")]
    with Path(log_path).open("a") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1800)
    if not Path(output).is_file():
        raise RuntimeError("ATC did not generate an OM file")

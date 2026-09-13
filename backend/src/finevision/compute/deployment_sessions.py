"""Bounded session cache; calls and eviction share a lock to protect vendor contexts."""
from collections import OrderedDict
import os
import threading

import numpy as np


class Sessions:
    def __init__(self, runtime="onnx_cpu", maximum=2):
        self.runtime = runtime
        self.maximum = maximum
        self.sessions = OrderedDict()
        self.lock = threading.RLock()

    def evict(self, sha):
        with self.lock:
            engine = self.sessions.pop(sha, None)
            if engine is not None and hasattr(engine, "close"):
                engine.close()
            return engine is not None

    def run(self, descriptor, reader, images, deployment=None):
        with self.lock:
            if deployment is not None:
                from finevision.compute.deployment_backends import fingerprint
                meta = descriptor.metadata
                if self.runtime != deployment["runtime"] or meta.get("runtime") != self.runtime:
                    raise ValueError("deployment sent to the wrong runtime")
                if deployment["target_profile"] != os.environ.get("FINEVISION_TARGET_PROFILE"):
                    raise ValueError("worker target profile does not match deployment")
                if meta.get("runtime_fingerprint") != fingerprint(self.runtime):
                    raise ValueError("compiled artifact is incompatible with worker hardware/runtime")
                if images.shape[0] > int(deployment["max_batch"]):
                    raise ValueError("input batch exceeds compiled profile")
            elif self.runtime != "onnx_cpu":
                raise ValueError("this worker requires a compiled deployment")
            # Verify even cache hits: cached models must not mask corruption of the local artifact.
            path = reader(descriptor)
            key = descriptor.sha256
            engine = self.sessions.get(key)
            if engine is None:
                if len(self.sessions) >= self.maximum:
                    self.evict(next(iter(self.sessions)))
                if self.runtime == "onnx_cpu":
                    import onnxruntime as ort
                    options = ort.SessionOptions()
                    options.intra_op_num_threads = 2
                    engine = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
                else:
                    from finevision.compute.deployment_backends import TensorRTEngine, AscendEngine
                    engine = (TensorRTEngine if self.runtime == "tensorrt" else AscendEngine)(path)
                self.sessions[key] = engine
            self.sessions.move_to_end(key)
            if self.runtime == "onnx_cpu":
                return engine.run(None, {engine.get_inputs()[0].name: images.astype(np.float32)})[0]
            return engine.run(images.astype(np.float32))

"""Deployment precision conversion with stable float32 input/output contracts."""
import numpy as np


def validate_precision(precision):
    if precision not in ("FP32", "FP16"):
        raise ValueError("precision must be FP32 or FP16")
    return precision


def convert_graph(graph, precision):
    validate_precision(precision)
    if precision == "FP16":
        from onnxruntime.transformers.float16 import convert_float_to_float16
        graph = convert_float_to_float16(graph, keep_io_types=True)
        # The converter appends boundary Cast nodes. Restore dependency order
        # before ONNX checker (ORT itself would otherwise silently reorder).
        from onnxruntime.transformers.onnx_model import OnnxModel
        OnnxModel(graph).topological_sort()
    return graph


def deployment_state(state, precision):
    validate_precision(precision)
    import torch
    result = {key: value.half() if precision == "FP16" and value.is_floating_point() else value
              for key, value in state.items()}
    if any(value.is_floating_point() and not torch.isfinite(value).all() for value in result.values()):
        raise ValueError("parameters are not finite in requested deployment precision")
    return result


def check_parity(actual, expected, precision):
    # Absolute floor covers near-zero logits; relative budget covers magnitude.
    # A long transformer graph accumulates half-precision rounding. Around
    # near-zero logits, a strict 1e-2 absolute floor rejects otherwise sound
    # exports; 2e-2 remains tight enough to reject material output drift.
    rtol, atol = (2e-2, 2e-2) if precision == "FP16" else (1e-3, 1e-4)
    if not np.isfinite(actual).all():
        raise ValueError("non-finite deployment output")
    np.testing.assert_allclose(actual, expected, rtol=rtol, atol=atol)

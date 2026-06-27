from __future__ import annotations

from pathlib import Path

import numpy as np

from finevision.ml_toolkit.artifacts import write_json
from finevision.ml_toolkit.metrics import softmax
from finevision.schemas.artifacts import CalibrationBin, CalibrationReport, ModelArtifact


def _nll(probabilities: np.ndarray, y_true: np.ndarray) -> float:
    picked = probabilities[np.arange(len(y_true)), y_true]
    return float(-np.log(np.clip(picked, 1e-12, 1.0)).mean()) if len(y_true) else 0.0


def _brier(probabilities: np.ndarray, y_true: np.ndarray) -> float:
    target = np.zeros_like(probabilities)
    target[np.arange(len(y_true)), y_true] = 1.0
    return float(np.mean(np.sum((probabilities - target) ** 2, axis=1))) if len(y_true) else 0.0


def _bins(probabilities: np.ndarray, y_true: np.ndarray, bin_count: int) -> tuple[float, list[CalibrationBin]]:
    confidence = probabilities.max(axis=1)
    predicted = probabilities.argmax(axis=1)
    correct = predicted == y_true
    bins: list[CalibrationBin] = []
    ece = 0.0
    for idx in range(bin_count):
        lower = idx / bin_count
        upper = (idx + 1) / bin_count
        if idx == bin_count - 1:
            mask = (confidence >= lower) & (confidence <= upper)
        else:
            mask = (confidence >= lower) & (confidence < upper)
        count = int(mask.sum())
        if count:
            accuracy = float(correct[mask].mean())
            avg_confidence = float(confidence[mask].mean())
            ece += (count / len(confidence)) * abs(accuracy - avg_confidence)
        else:
            accuracy = 0.0
            avg_confidence = 0.0
        bins.append(
            CalibrationBin(
                lower=lower,
                upper=upper,
                count=count,
                accuracy=accuracy,
                confidence=avg_confidence,
            )
        )
    return float(ece), bins


def calibration_metrics(probabilities: np.ndarray, y_true: np.ndarray, bin_count: int = 10) -> tuple[dict[str, float], list[CalibrationBin]]:
    ece, bins = _bins(probabilities, y_true, bin_count)
    return {"ece": ece, "nll": _nll(probabilities, y_true), "brier": _brier(probabilities, y_true)}, bins


def fit_temperature_scaling(
    model_artifact: ModelArtifact,
    logits: np.ndarray,
    labels: list[str],
    splits: list[str],
    artifact_root: str | Path | None = None,
    split: str = "val",
    candidates: np.ndarray | None = None,
    bin_count: int = 10,
) -> CalibrationReport:
    classes = np.array(model_artifact.classes)
    y = np.array([int(np.where(classes == label)[0][0]) for label in labels], dtype=np.int64)
    split_values = np.array(splits)
    actual_split = split
    split_mask = split_values == split
    if not split_mask.any():
        actual_split = "val_test"
        split_mask = np.isin(split_values, ["val", "test"])
    if not split_mask.any():
        actual_split = "all"
        split_mask = np.ones(len(labels), dtype=bool)

    eval_logits = logits[split_mask]
    eval_y = y[split_mask]
    candidates = candidates if candidates is not None else np.geomspace(0.05, 10.0, 96)
    best_temperature = 1.0
    best_nll = float("inf")
    for temperature in candidates:
        probs = softmax(eval_logits, temperature=float(temperature))
        nll = _nll(probs, eval_y)
        if nll < best_nll:
            best_nll = nll
            best_temperature = float(temperature)

    before, _ = calibration_metrics(softmax(eval_logits), eval_y, bin_count=bin_count)
    after_probs = softmax(eval_logits, temperature=best_temperature)
    after, bins = calibration_metrics(after_probs, eval_y, bin_count=bin_count)
    report = CalibrationReport(
        artifact_id=f"{model_artifact.artifact_id}-temperature-scaling",
        dataset_id=model_artifact.dataset_id,
        dataset_version_id=model_artifact.dataset_version_id,
        model_artifact_id=model_artifact.artifact_id,
        method="temperature_scaling",
        split=actual_split,
        temperature=best_temperature,
        before=before,
        after=after,
        bins=bins,
    )
    if artifact_root is not None:
        write_json(Path(artifact_root) / model_artifact.artifact_id / "calibration_report.json", report)
    return report

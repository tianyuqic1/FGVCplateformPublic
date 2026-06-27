from __future__ import annotations

import numpy as np

from finevision.schemas.artifacts import EvaluationReport


def softmax(logits: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    scaled = logits / max(temperature, 1e-6)
    shifted = scaled - scaled.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)


def classification_report(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    classes: list[str],
    run_config: dict[str, object],
) -> EvaluationReport:
    class_count = len(classes)
    confusion = np.zeros((class_count, class_count), dtype=int)
    for true, pred in zip(y_true, y_pred, strict=False):
        confusion[int(true), int(pred)] += 1

    per_class: dict[str, dict[str, float]] = {}
    f1_values: list[float] = []
    for idx, label in enumerate(classes):
        tp = float(confusion[idx, idx])
        fp = float(confusion[:, idx].sum() - tp)
        fn = float(confusion[idx, :].sum() - tp)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        support = int(confusion[idx, :].sum())
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": float(support),
        }
        f1_values.append(f1)

    accuracy = float((y_true == y_pred).mean()) if len(y_true) else 0.0
    macro_f1 = float(np.mean(f1_values)) if f1_values else 0.0
    return EvaluationReport(
        accuracy=accuracy,
        macro_f1=macro_f1,
        per_class=per_class,
        confusion_matrix=confusion.tolist(),
        run_config=run_config,
    )

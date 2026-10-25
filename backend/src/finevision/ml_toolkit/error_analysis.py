"""Bounded final-checkpoint test evidence. Does not tune the model on test data."""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from finevision.ml_toolkit.metrics import softmax


def build_test_error_evidence(
    *, sample_ids: list[str], labels: list[str], splits: list[str],
    logits: np.ndarray, classes: list[str],
) -> dict[str, Any]:
    scores = np.asarray(logits)
    if (len(sample_ids) != len(labels) or len(labels) != len(splits)
            or scores.shape != (len(labels), len(classes)) or not classes
            or len(set(classes)) != len(classes) or len(set(sample_ids)) != len(sample_ids)
            or any(not isinstance(x, str) or not x for x in sample_ids)
            or not np.isfinite(scores).all()):
        raise ValueError("invalid sample/logit alignment for test evidence")
    index = {label: i for i, label in enumerate(classes)}
    if any(label not in index for label in labels):
        raise ValueError("unknown ground-truth class")
    selected = np.flatnonzero(np.asarray(splits) == "test")
    train_counts = Counter(label for label, split in zip(labels, splits, strict=True) if split == "train")
    truth = np.array([index[labels[i]] for i in selected], dtype=int)
    probs = softmax(scores[selected].astype(np.float64)) if len(selected) else np.empty((0, len(classes)))
    predicted = probs.argmax(1) if len(selected) else np.array([], dtype=int)
    wrong = np.flatnonzero(truth != predicted)
    confidences = probs[np.arange(len(selected)), predicted] if len(selected) else np.array([])
    if len(classes) > 1 and len(selected):
        best = np.partition(probs, -2, axis=1)[:, -2:]
        margins = best.max(1) - best.min(1)
    else:
        margins = np.ones(len(selected))
    class_statistics = []
    f1_values = []
    for i, label in enumerate(classes):
        total = int((truth == i).sum())
        errors = int(((truth == i) & (predicted != i)).sum())
        true_positives = total - errors
        predicted_count = int((predicted == i).sum())
        f1_values.append(2 * true_positives / (total + predicted_count) if total + predicted_count else 0.0)
        class_statistics.append({"label": label, "test_count": total, "error_count": errors,
                                 "error_rate": errors / total if total else None,
                                 "train_count": train_counts[label]})
    class_statistics.sort(key=lambda item: (-item["error_count"], item["label"]))
    pairs = Counter((int(truth[i]), int(predicted[i])) for i in wrong)
    confusion_pairs = [
        {"true_label": classes[a], "predicted_label": classes[b], "count": count,
         "rate_within_true_class": count / int((truth == a).sum())}
        for (a, b), count in sorted(pairs.items(), key=lambda item: (-item[1], classes[item[0][0]], classes[item[0][1]]))[:200]
    ]
    retained = sorted(wrong, key=lambda i: (-float(confidences[i]), sample_ids[int(selected[i])]))[:50]
    examples = []
    for i in retained:
        top = np.argsort(-probs[i], kind="stable")[:3]
        examples.append({"sample_id": sample_ids[int(selected[i])], "true_label": classes[int(truth[i])],
                         "predicted_label": classes[int(predicted[i])], "predicted_probability": float(confidences[i]),
                         "true_probability": float(probs[i, truth[i]]), "margin": float(margins[i]),
                         "top_predictions": [{"label": classes[int(j)], "probability": float(probs[i, j])} for j in top]})
    return {"schema_version": 1, "evaluation_split": "test", "selection_basis": "final_checkpoint_argmax",
            "sample_count": len(selected), "error_count": len(wrong),
            "accuracy": float((truth == predicted).mean()) if len(selected) else None,
            "macro_f1": float(np.mean(f1_values)) if len(selected) else None,
            "confidence_basis": "uncalibrated_softmax", "high_confidence_threshold": 0.8,
            "high_confidence_error_count": int((confidences[wrong] >= 0.8).sum()),
            "low_margin_threshold": 0.1, "low_margin_error_count": int((margins[wrong] <= 0.1).sum()),
            "class_statistics": class_statistics, "confusion_pairs": confusion_pairs,
            "confusion_pairs_total": len(pairs), "examples": examples, "examples_retained": len(examples),
            "examples_policy": "highest_wrong_probability_then_sample_id"}

import numpy as np
import pytest

from finevision.ml_toolkit.error_analysis import build_test_error_evidence


def evidence(**changes):
    inputs = dict(sample_ids=["train-a", "validation-only", "test-wrong", "test-right", "test-close"],
                  labels=["a", "a", "a", "b", "b"], splits=["train", "val", "test", "test", "test"],
                  logits=np.array([[0, 10], [0, 10], [0, 10], [0, 4], [0.1, 0]], dtype=float), classes=["a", "b"])
    inputs.update(changes)
    return build_test_error_evidence(**inputs)


def test_only_final_test_rows_are_counted_and_examples_are_aligned():
    result = evidence()
    assert result["evaluation_split"] == "test"
    assert result["sample_count"] == 3 and result["error_count"] == 2
    assert result["accuracy"] == pytest.approx(1 / 3)
    assert result["macro_f1"] == pytest.approx(1 / 4)
    assert result["high_confidence_error_count"] == 1
    assert result["low_margin_error_count"] == 1
    assert result["confidence_basis"] == "uncalibrated_softmax"
    assert result["examples"][0]["sample_id"] == "test-wrong"
    assert {x["sample_id"] for x in result["examples"]} == {"test-wrong", "test-close"}
    assert result["confusion_pairs"] == [
        {"true_label": "a", "predicted_label": "b", "count": 1, "rate_within_true_class": 1},
        {"true_label": "b", "predicted_label": "a", "count": 1, "rate_within_true_class": 0.5},
    ]
    assert {x["label"]: x["train_count"] for x in result["class_statistics"]} == {"a": 1, "b": 0}


def test_no_test_samples_never_falls_back_to_validation():
    result = evidence(splits=["train", "val", "val", "val", "val"])
    assert result["sample_count"] == 0 and result["accuracy"] is None and result["macro_f1"] is None
    assert result["examples"] == []


def test_examples_are_bounded_and_ties_deterministic():
    args = dict(sample_ids=[f"test-{i:03d}" for i in reversed(range(80))], labels=["a"] * 80,
                splits=["test"] * 80, logits=np.tile([0, 10], (80, 1)), classes=["a", "b"])
    result = build_test_error_evidence(**args)
    assert result["error_count"] == 80 and result["examples_retained"] == 50
    assert result["examples"][0]["sample_id"] == "test-000"
    assert result["examples"][-1]["sample_id"] == "test-049"


@pytest.mark.parametrize("changes", [
    {"sample_ids": ["x"]}, {"classes": ["a", "a"]},
    {"sample_ids": ["x"] * 5}, {"logits": np.full((5, 2), np.nan)},
    {"labels": ["other"] * 5},
])
def test_invalid_prediction_alignment_is_rejected(changes):
    with pytest.raises(ValueError):
        evidence(**changes)

from __future__ import annotations

from finevision.api.vlm_review_store import ClaimedVLMReview
from finevision.vlm.service import _build_prompt, _parse_candidate
from finevision.vlm.worker import evaluate_auto_submit_gate


def _claim(*, mode: str = "auto", decision: str = "abstain", risk_acknowledged: bool = True) -> ClaimedVLMReview:
    return ClaimedVLMReview(
        result_id="vlm-result-1",
        run_id="vlm-run-1",
        review_item_id="review-1",
        mode=mode,
        candidate_labels=["red_winged_blackbird", "rusty_blackbird", "brewer_blackbird"],
        context={
            "decision": {"decision": decision},
            "top_k": [
                {"label": "rusty_blackbird", "score": 0.44},
                {"label": "brewer_blackbird", "score": 0.42},
            ],
            "nearest_neighbors": [
                {"label": "rusty_blackbird"},
                {"label": "rusty_blackbird"},
                {"label": "brewer_blackbird"},
            ],
        },
        input_ref="/tmp/image.jpg",
        sample_id=None,
        dataset_version_id="dataset@cub-001",
        model_id="Fine-R1-3B",
        model_revision="revision-1",
        prompt_version="finevision-finer1-v1.1",
        config={"risk_acknowledged": risk_acknowledged},
    )


def test_parse_candidate_requires_unique_candidate_mapping() -> None:
    candidates = ["Black footed Albatross", "Laysan Albatross"]

    assert _parse_candidate("<think>compare bills</think><answer>Laysan Albatross</answer>", candidates) == "Laysan Albatross"
    assert _parse_candidate("<answer>laysan_albatross</answer>", candidates) == "Laysan Albatross"
    assert _parse_candidate("<answer>Unknown bird</answer>", candidates) is None


def test_prompt_hides_classifier_rank_and_constrains_output() -> None:
    prompt = _build_prompt("A bird species dataset.", ["class-b", "class-a"])

    assert "Candidate order is randomized and conveys no rank" in prompt
    assert "Do not invent a label outside the candidate list" in prompt
    assert "at most 180 English words" in prompt
    assert "confidence" not in prompt.lower()
    assert "top-1" not in prompt.lower()


def test_auto_gate_accepts_consistent_candidate_with_independent_agreement() -> None:
    report = evaluate_auto_submit_gate(_claim(), ["rusty_blackbird", "rusty_blackbird"])

    assert report["eligible"] is True
    assert report["checks"]["two_pass_consistent"] is True
    assert report["checks"]["classifier_agreement"] is True


def test_auto_gate_rejects_disagreement_or_missing_acknowledgement() -> None:
    inconsistent = evaluate_auto_submit_gate(_claim(), ["rusty_blackbird", "brewer_blackbird"])
    unacknowledged = evaluate_auto_submit_gate(
        _claim(risk_acknowledged=False),
        ["rusty_blackbird", "rusty_blackbird"],
    )

    assert inconsistent["eligible"] is False
    assert unacknowledged["eligible"] is False


def test_auto_gate_never_accepts_reject_ood() -> None:
    report = evaluate_auto_submit_gate(
        _claim(decision="reject_ood"),
        ["rusty_blackbird", "rusty_blackbird"],
    )

    assert report["eligible"] is False
    assert report["checks"]["reject_ood_forbidden"] is False

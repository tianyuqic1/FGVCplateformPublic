from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
import mimetypes
import os
from pathlib import Path
import random
import time
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from finevision.api.review_store import DatabaseReviewStore
from finevision.api.store import create_stores
from finevision.api.vlm_review_store import ClaimedVLMReview, DatabaseVLMReviewStore
from finevision.vlm.client import rerank_candidates


DEFAULT_SERVICE_URL = "http://fine-r1-service:8010"


def run_next_vlm_review() -> str | None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required by the VLM review worker")
    engine = create_engine(database_url, poolclass=NullPool)
    task_store = DatabaseVLMReviewStore(engine)
    review_store = DatabaseReviewStore(engine)
    metadata_store, _ = create_stores(database_url=engine)
    claimed = task_store.claim_next_result(
        stale_after_seconds=int(os.environ.get("FINEVISION_FINER1_LEASE_SECONDS", "900")),
        max_attempts=int(os.environ.get("FINEVISION_FINER1_MAX_ATTEMPTS", "3")),
    )
    if claimed is None:
        return None

    image_sha256: str | None = None
    responses: list[dict[str, Any]] = []
    try:
        image_path = _resolve_image_path(claimed, metadata_store)
        image_bytes = image_path.read_bytes()
        image_sha256 = hashlib.sha256(image_bytes).hexdigest()
        image_data_url = _image_data_url(image_path, image_bytes)
        dataset_summary = _dataset_summary(claimed, metadata_store)
        passes = 2 if claimed.mode == "auto" else 1
        for pass_index in range(passes):
            candidates = _shuffled_candidates(
                claimed.candidate_labels,
                seed=f"{claimed.result_id}:{pass_index}",
            )
            responses.append(
                rerank_candidates(
                    service_url=os.environ.get("FINEVISION_FINER1_SERVICE_URL", DEFAULT_SERVICE_URL),
                    image_data_url=image_data_url,
                    candidates=candidates,
                    dataset_summary=dataset_summary,
                    request_id=f"{claimed.result_id}:{pass_index}",
                    timeout_seconds=float(os.environ.get("FINEVISION_FINER1_TIMEOUT_SECONDS", "180")),
                )
            )
            response_prompt_version = str(responses[-1].get("prompt_version") or "")
            if response_prompt_version != claimed.prompt_version:
                raise RuntimeError(
                    "Fine-R1 prompt version mismatch: "
                    f"expected {claimed.prompt_version}, got {response_prompt_version or 'missing'}"
                )

        labels = [str(response["suggested_label"]) for response in responses]
        suggested_label = labels[0]
        pass_metrics = [
            {
                "latency_seconds": float(response.get("latency_seconds") or 0.0),
                "input_tokens": int(response.get("input_tokens") or 0),
                "generated_tokens": int(response.get("generated_tokens") or 0),
            }
            for response in responses
        ]
        total_latency_seconds = sum(metric["latency_seconds"] for metric in pass_metrics)
        total_input_tokens = sum(metric["input_tokens"] for metric in pass_metrics)
        total_generated_tokens = sum(metric["generated_tokens"] for metric in pass_metrics)
        gate_report = evaluate_auto_submit_gate(claimed, labels)
        auto_submit_eligible = claimed.mode == "auto" and bool(gate_report["eligible"])
        assistance = {
            "source": "fine-r1",
            "run_id": claimed.run_id,
            "result_id": claimed.result_id,
            "model_id": claimed.model_id,
            "model_revision": responses[0].get("model_revision") or claimed.model_revision,
            "prompt_version": claimed.prompt_version,
            "suggested_label": suggested_label,
            "reasoning": responses[0].get("reasoning") or "",
            "candidate_labels": claimed.candidate_labels,
            "image_sha256": image_sha256,
            "gate_report": gate_report,
            "auto_submitted": auto_submit_eligible,
            "pass_metrics": pass_metrics,
        }
        if not task_store.is_result_active(claimed.result_id):
            return claimed.result_id
        review_store.update_assistance_metadata(
            review_id=claimed.review_item_id,
            key="vlm_assistance",
            value=assistance,
        )
        if auto_submit_eligible:
            top1 = _top1_label(claimed.context)
            review_store.complete_review(
                review_id=claimed.review_item_id,
                final_outcome="confirmed_label" if suggested_label == top1 else "corrected_label",
                destination="training_candidate",
                final_label=suggested_label,
                reviewer_note=f"Fine-R1 auto review; gate={gate_report}",
                reviewer=f"vlm:{claimed.model_id}",
                feedback_source="vlm_auto",
                feedback_metadata={
                    "vlm_run_id": claimed.run_id,
                    "vlm_result_id": claimed.result_id,
                    "model_id": claimed.model_id,
                    "model_revision": responses[0].get("model_revision") or claimed.model_revision,
                    "prompt_version": claimed.prompt_version,
                    "gate_policy": gate_report["policy"],
                },
                vlm_result_id=claimed.result_id,
            )
        task_store.complete_result(
            claimed.result_id,
            suggested_label=suggested_label,
            reasoning=str(responses[0].get("reasoning") or ""),
            raw_output=str(responses[0].get("raw_output") or ""),
            image_sha256=image_sha256,
            model_revision=str(responses[0].get("model_revision") or claimed.model_revision or "") or None,
            latency_seconds=total_latency_seconds,
            input_tokens=total_input_tokens,
            generated_tokens=total_generated_tokens,
            auto_submit_eligible=auto_submit_eligible,
            gate_report=gate_report,
            fallback_to_human=claimed.mode == "auto" and not auto_submit_eligible,
        )
    except Exception as exc:
        task_store.fail_result(
            claimed.result_id,
            str(exc),
            image_sha256=image_sha256,
            model_revision=str((responses[0].get("model_revision") if responses else None) or claimed.model_revision or "")
            or None,
            latency_seconds=sum(float(item.get("latency_seconds") or 0.0) for item in responses) or None,
            input_tokens=sum(int(item.get("input_tokens") or 0) for item in responses) or None,
            generated_tokens=sum(int(item.get("generated_tokens") or 0) for item in responses) or None,
        )
    return claimed.result_id


def run_worker_loop(*, poll_interval_seconds: float = 2.0, max_results: int | None = None) -> int:
    processed = 0
    while max_results is None or processed < max_results:
        result_id = run_next_vlm_review()
        if result_id is None:
            time.sleep(poll_interval_seconds)
            continue
        processed += 1
    return processed


def evaluate_auto_submit_gate(claimed: ClaimedVLMReview, labels: list[str]) -> dict[str, Any]:
    decision = str((claimed.context.get("decision") or {}).get("decision") or "")
    consistent = bool(labels) and len(set(labels)) == 1
    suggested_label = labels[0] if labels else None
    candidate_valid = suggested_label in claimed.candidate_labels
    classifier_agreement = suggested_label == _top1_label(claimed.context)
    neighbor_agreement = suggested_label == _neighbor_majority_label(claimed.context)
    independent_agreement = classifier_agreement or neighbor_agreement
    risk_acknowledged = claimed.config.get("risk_acknowledged") is True
    checks = {
        "mode_is_auto": claimed.mode == "auto",
        "risk_acknowledged": risk_acknowledged,
        "decision_is_abstain": decision == "abstain",
        "candidate_is_valid": candidate_valid,
        "two_pass_consistent": consistent and len(labels) >= 2,
        "classifier_agreement": classifier_agreement,
        "neighbor_agreement": neighbor_agreement,
        "independent_signal_agrees": independent_agreement,
        "reject_ood_forbidden": decision != "reject_ood",
    }
    eligible = (
        checks["mode_is_auto"]
        and checks["risk_acknowledged"]
        and checks["decision_is_abstain"]
        and checks["candidate_is_valid"]
        and checks["two_pass_consistent"]
        and checks["independent_signal_agrees"]
        and checks["reject_ood_forbidden"]
    )
    return {
        "eligible": eligible,
        "checks": checks,
        "pass_labels": labels,
        "policy": "finevision-finer1-auto-gate-v1",
    }


def _resolve_image_path(claimed: ClaimedVLMReview, metadata_store: Any) -> Path:
    input_payload = dict(claimed.context.get("input") or {})
    candidates = [
        claimed.input_ref,
        input_payload.get("uploaded_image_path"),
        input_payload.get("image_path"),
    ]
    for value in candidates:
        if value and Path(str(value)).is_file():
            return Path(str(value))

    manifest = metadata_store.get_dataset_version(claimed.dataset_version_id)
    if manifest is not None and claimed.sample_id:
        sample = next((item for item in manifest.samples if item.sample_id == claimed.sample_id), None)
        if sample is not None:
            sample_path = Path(sample.path)
            if not sample_path.is_absolute():
                sample_path = Path(manifest.root) / sample_path
            if sample_path.is_file():
                return sample_path
    raise FileNotFoundError(f"Image pixels are unavailable for review item {claimed.review_item_id}")


def _dataset_summary(claimed: ClaimedVLMReview, metadata_store: Any) -> str:
    dataset_id = str(claimed.context.get("dataset_id") or "")
    detail = metadata_store.dataset_detail(dataset_id) if dataset_id else None
    card = dict((detail or {}).get("dataset_card") or {})
    summary = str(card.get("summary") or "").strip()
    domain = str(card.get("domain") or "").strip()
    return " ".join(part for part in [f"Domain: {domain}." if domain else "", summary] if part)


def _image_data_url(path: Path, content: bytes) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"


def _shuffled_candidates(candidates: list[str], *, seed: str) -> list[str]:
    shuffled = list(candidates)
    random.Random(seed).shuffle(shuffled)
    return shuffled


def _top1_label(context: dict[str, Any]) -> str | None:
    top_k = context.get("top_k") or context.get("topK") or []
    if not top_k:
        return None
    first = top_k[0]
    return str(first.get("label")) if isinstance(first, dict) and first.get("label") else None


def _neighbor_majority_label(context: dict[str, Any]) -> str | None:
    neighbors = context.get("nearest_neighbors") or context.get("nearestNeighbors") or []
    labels = [str(item.get("label")) for item in neighbors if isinstance(item, dict) and item.get("label")]
    if not labels:
        return None
    counts = Counter(labels)
    ordered = counts.most_common()
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        return None
    return ordered[0][0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    parser.add_argument("--max-results", type=int)
    args = parser.parse_args()
    run_worker_loop(
        poll_interval_seconds=args.poll_interval_seconds,
        max_results=args.max_results,
    )


if __name__ == "__main__":
    main()

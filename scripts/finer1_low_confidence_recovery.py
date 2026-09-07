#!/usr/bin/env python3
"""Evaluate Fine-R1 recovery on samples rejected by the production threshold policy."""

from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import json
import mimetypes
from pathlib import Path
import random
import urllib.error
import urllib.request
from typing import Any

import numpy as np


DECISION_EPSILON = 1e-6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--features", type=Path, required=True)
    prepare.add_argument("--head", type=Path, required=True)
    prepare.add_argument("--database-url", required=True)
    prepare.add_argument("--dataset-version-key", required=True)
    prepare.add_argument("--temperature", type=float, required=True)
    prepare.add_argument("--accept-threshold", type=float, required=True)
    prepare.add_argument("--margin-threshold", type=float, required=True)
    prepare.add_argument("--candidate-k", type=int, default=3)
    prepare.add_argument("--neighbor-k", type=int, default=3)
    prepare.add_argument("--sample-count", type=int, default=40)
    prepare.add_argument("--seed", type=int, default=20260726)
    prepare.add_argument("--remote-dataset-root", type=Path, required=True)
    prepare.add_argument("--dataset-summary", default="")
    prepare.add_argument("--output", type=Path, required=True)

    run_http = subparsers.add_parser("run-http")
    run_http.add_argument("--manifest", type=Path, required=True)
    run_http.add_argument("--service-url", default="http://127.0.0.1:8010")
    run_http.add_argument("--timeout-seconds", type=float, default=240.0)
    run_http.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "prepare":
        if args.candidate_k < 2:
            parser.error("--candidate-k must be at least 2")
        if args.neighbor_k < 1:
            parser.error("--neighbor-k must be at least 1")
        if args.sample_count < 1:
            parser.error("--sample-count must be at least 1")
    return args


def softmax(logits: np.ndarray, temperature: float) -> np.ndarray:
    scaled = logits / temperature
    scaled = scaled - scaled.max(axis=1, keepdims=True)
    values = np.exp(scaled)
    return values / values.sum(axis=1, keepdims=True)


def load_manifest_samples(
    database_url: str,
    dataset_version_key: str,
) -> tuple[dict[str, dict[str, Any]], str]:
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url)
    with engine.connect() as connection:
        artifact = connection.execute(
            text(
                """
                SELECT artifact_metadata
                FROM artifacts
                WHERE artifact_type = 'dataset_manifest'
                  AND dataset_version_id = (
                    SELECT id FROM dataset_versions WHERE version_key = :version_key
                  )
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"version_key": dataset_version_key},
        ).scalar_one()
        card = connection.execute(
            text(
                """
                SELECT artifact_metadata
                FROM artifacts
                WHERE artifact_type = 'dataset_card'
                  AND dataset_version_id = (
                    SELECT id FROM dataset_versions WHERE version_key = :version_key
                  )
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"version_key": dataset_version_key},
        ).scalar_one_or_none()

    manifest = dict(artifact["manifest"])
    samples = {str(item["sample_id"]): dict(item) for item in manifest["samples"]}
    dataset_card = dict((card or {}).get("dataset_card") or {})
    summary = str(dataset_card.get("summary") or "").strip()
    domain = str(dataset_card.get("domain") or "").strip()
    combined_summary = " ".join(
        part for part in [f"Domain: {domain}." if domain else "", summary] if part
    )
    return samples, combined_summary


def majority_label(labels: list[str]) -> str | None:
    if not labels:
        return None
    ordered = Counter(labels).most_common()
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        return None
    return ordered[0][0]


def nearest_neighbors(
    *,
    test_features: np.ndarray,
    train_features: np.ndarray,
    train_labels: np.ndarray,
    train_sample_ids: np.ndarray,
    k: int,
    chunk_size: int = 256,
) -> list[list[dict[str, Any]]]:
    results: list[list[dict[str, Any]]] = []
    for start in range(0, len(test_features), chunk_size):
        queries = test_features[start : start + chunk_size]
        squared_distances = (
            (queries**2).sum(axis=1, keepdims=True)
            + (train_features**2).sum(axis=1).reshape(1, -1)
            - 2.0 * queries @ train_features.T
        )
        nearest = np.argpartition(squared_distances, kth=k - 1, axis=1)[:, :k]
        for row_index, indices in enumerate(nearest):
            ordered = indices[np.argsort(squared_distances[row_index, indices])]
            results.append(
                [
                    {
                        "sample_id": str(train_sample_ids[index]),
                        "label": str(train_labels[index]),
                        "distance": float(max(squared_distances[row_index, index], 0.0) ** 0.5),
                    }
                    for index in ordered
                ]
            )
    return results


def prepare_manifest(args: argparse.Namespace) -> None:
    feature_state = np.load(args.features, allow_pickle=False)
    model_state = np.load(args.head, allow_pickle=False)

    features = feature_state["features"].astype(np.float32)
    labels = feature_state["labels"].astype(str)
    sample_ids = feature_state["sample_ids"].astype(str)
    splits = feature_state["splits"].astype(str)
    classes = model_state["classes"].astype(str)

    standardized = (
        features - model_state["feature_mean"].astype(np.float32)
    ) / model_state["feature_std"].astype(np.float32)
    logits = standardized @ model_state["weights"] + model_state["bias"]
    probabilities = softmax(logits, args.temperature)
    order = np.argsort(probabilities, axis=1)[:, ::-1]
    top1_indices = order[:, 0]
    top2_indices = order[:, 1]
    confidence = probabilities[np.arange(len(probabilities)), top1_indices]
    margin = confidence - probabilities[np.arange(len(probabilities)), top2_indices]
    predicted_labels = classes[top1_indices]

    test_indices = np.flatnonzero(splits == "test")
    train_indices = np.flatnonzero(splits == "train")
    abstain_mask = (
        (confidence[test_indices] + DECISION_EPSILON < args.accept_threshold)
        | (margin[test_indices] + DECISION_EPSILON < args.margin_threshold)
    )
    abstain_indices = test_indices[abstain_mask]
    accept_indices = test_indices[~abstain_mask]

    neighbor_rows = nearest_neighbors(
        test_features=features[abstain_indices],
        train_features=features[train_indices],
        train_labels=labels[train_indices],
        train_sample_ids=sample_ids[train_indices],
        k=args.neighbor_k,
    )
    manifest_samples, stored_dataset_summary = load_manifest_samples(
        args.database_url,
        args.dataset_version_key,
    )
    dataset_summary = args.dataset_summary.strip() or stored_dataset_summary

    pool_rows: list[dict[str, Any]] = []
    for pool_position, (index, neighbors) in enumerate(
        zip(abstain_indices, neighbor_rows, strict=True)
    ):
        sample = manifest_samples[str(sample_ids[index])]
        candidates = [str(classes[item]) for item in order[index, : args.candidate_k]]
        source_path = Path(str(sample["path"]))
        relative_parts = source_path.parts
        try:
            class_position = relative_parts.index(str(sample["label"]))
        except ValueError as exc:
            raise ValueError(f"Cannot map sample path to remote CUB root: {source_path}") from exc
        remote_path = args.remote_dataset_root.joinpath(*relative_parts[class_position:])
        pool_rows.append(
            {
                "pool_position": pool_position,
                "sample_id": str(sample_ids[index]),
                "image_path": str(remote_path),
                "ground_truth": str(labels[index]),
                "dino_top1": str(predicted_labels[index]),
                "dino_correct": bool(predicted_labels[index] == labels[index]),
                "confidence": float(confidence[index]),
                "margin": float(margin[index]),
                "candidates": candidates,
                "candidate_contains_truth": bool(str(labels[index]) in candidates),
                "nearest_neighbors": neighbors,
                "neighbor_majority": majority_label(
                    [str(item["label"]) for item in neighbors]
                ),
                "dataset_summary": dataset_summary,
            }
        )

    rng = random.Random(args.seed)
    selected_positions = sorted(
        rng.sample(range(len(pool_rows)), k=min(args.sample_count, len(pool_rows)))
    )
    selected_rows = [pool_rows[position] for position in selected_positions]

    test_correct = predicted_labels[test_indices] == labels[test_indices]
    accept_correct = predicted_labels[accept_indices] == labels[accept_indices]
    abstain_correct = predicted_labels[abstain_indices] == labels[abstain_indices]
    candidate_recall = {
        str(k): float(
            np.mean(
                [
                    labels[index] in classes[order[index, :k]]
                    for index in abstain_indices
                ]
            )
        )
        for k in sorted({1, 3, 5, args.candidate_k})
    }
    payload = {
        "schema_version": "finevision-low-confidence-recovery-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment": {
            "dataset_version_key": args.dataset_version_key,
            "sample_seed": args.seed,
            "sample_count": len(selected_rows),
            "sample_method": "deterministic_uniform_random_from_all_abstained_test_samples",
            "candidate_k": args.candidate_k,
            "neighbor_k": args.neighbor_k,
            "temperature": args.temperature,
            "accept_threshold": args.accept_threshold,
            "margin_threshold": args.margin_threshold,
            "dataset_summary": dataset_summary,
        },
        "pool_summary": {
            "test_samples": int(len(test_indices)),
            "baseline_correct": int(test_correct.sum()),
            "baseline_accuracy": float(test_correct.mean()),
            "accepted_samples": int(len(accept_indices)),
            "accepted_correct": int(accept_correct.sum()),
            "accepted_selective_risk": float(1.0 - accept_correct.mean()),
            "coverage": float(len(accept_indices) / len(test_indices)),
            "abstained_samples": int(len(abstain_indices)),
            "abstention_rate": float(len(abstain_indices) / len(test_indices)),
            "abstained_baseline_correct": int(abstain_correct.sum()),
            "abstained_baseline_accuracy": float(abstain_correct.mean()),
            "abstained_baseline_errors": int((~abstain_correct).sum()),
            "candidate_recall_at_k": candidate_recall,
        },
        "samples": selected_rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload["pool_summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {len(selected_rows)} selected samples to {args.output}")


def image_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def post_json(url: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def run_http_benchmark(args: argparse.Namespace) -> None:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    endpoint = f"{args.service_url.rstrip('/')}/v1/rerank"

    for index, sample in enumerate(manifest["samples"], start=1):
        pass_results: list[dict[str, Any]] = []
        error: str | None = None
        for pass_index in range(2):
            candidates = list(sample["candidates"])
            random.Random(f"{sample['sample_id']}:{pass_index}").shuffle(candidates)
            try:
                response = post_json(
                    endpoint,
                    {
                        "image_data_url": image_data_url(Path(sample["image_path"])),
                        "candidates": candidates,
                        "dataset_summary": sample["dataset_summary"],
                        "request_id": f"low-confidence:{sample['sample_id']}:{pass_index}",
                    },
                    args.timeout_seconds,
                )
                pass_results.append(response)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                error = f"{exc}; response={detail}"
                break
            except (OSError, ValueError) as exc:
                error = str(exc)
                break

        pass_labels = [
            str(item["suggested_label"])
            for item in pass_results
            if item.get("suggested_label")
        ]
        two_pass_consistent = len(pass_labels) == 2 and len(set(pass_labels)) == 1
        suggested_label = pass_labels[0] if pass_labels else None
        classifier_agreement = suggested_label == sample["dino_top1"]
        neighbor_agreement = suggested_label == sample["neighbor_majority"]
        strict_gate_eligible = bool(
            error is None
            and two_pass_consistent
            and suggested_label in sample["candidates"]
            and (classifier_agreement or neighbor_agreement)
        )
        result = {
            **sample,
            "pass_labels": pass_labels,
            "two_pass_consistent": two_pass_consistent,
            "suggested_label": suggested_label,
            "vlm_correct": suggested_label == sample["ground_truth"],
            "classifier_agreement": classifier_agreement,
            "neighbor_agreement": neighbor_agreement,
            "strict_gate_eligible": strict_gate_eligible,
            "strict_gate_correct": bool(
                strict_gate_eligible and suggested_label == sample["ground_truth"]
            ),
            "corrected_dino_error": bool(
                strict_gate_eligible
                and not sample["dino_correct"]
                and suggested_label == sample["ground_truth"]
            ),
            "confirmed_dino_correct": bool(
                strict_gate_eligible
                and sample["dino_correct"]
                and suggested_label == sample["ground_truth"]
            ),
            "error": error,
            "passes": pass_results,
        }
        results.append(result)
        print(
            f"[{index}/{len(manifest['samples'])}] "
            f"dino={sample['dino_top1']} gt={sample['ground_truth']} "
            f"vlm={suggested_label} consistent={two_pass_consistent} "
            f"gate={strict_gate_eligible} correct={result['strict_gate_correct']} "
            f"error={error or '-'}",
            flush=True,
        )

    completed = [item for item in results if item["error"] is None]
    gate_eligible = [item for item in completed if item["strict_gate_eligible"]]
    dino_wrong = [item for item in completed if not item["dino_correct"]]
    dino_correct = [item for item in completed if item["dino_correct"]]
    summary = {
        "requested_samples": len(results),
        "completed_samples": len(completed),
        "failed_samples": len(results) - len(completed),
        "sample_dino_accuracy": ratio(dino_correct, completed),
        "candidate_recall": ratio(
            [item for item in completed if item["candidate_contains_truth"]],
            completed,
        ),
        "vlm_first_pass_accuracy": ratio(
            [item for item in completed if item["vlm_correct"]],
            completed,
        ),
        "two_pass_consistency_rate": ratio(
            [item for item in completed if item["two_pass_consistent"]],
            completed,
        ),
        "strict_gate_coverage": ratio(gate_eligible, completed),
        "strict_gate_precision": ratio(
            [item for item in gate_eligible if item["strict_gate_correct"]],
            gate_eligible,
        ),
        "strict_gate_wrong": len(
            [item for item in gate_eligible if not item["strict_gate_correct"]]
        ),
        "strict_gate_correct_recovered": len(
            [item for item in gate_eligible if item["strict_gate_correct"]]
        ),
        "dino_errors_in_sample": len(dino_wrong),
        "dino_errors_corrected_by_strict_gate": len(
            [item for item in dino_wrong if item["corrected_dino_error"]]
        ),
        "dino_error_correction_rate": ratio(
            [item for item in dino_wrong if item["corrected_dino_error"]],
            dino_wrong,
        ),
        "dino_correct_abstentions_in_sample": len(dino_correct),
        "dino_correct_abstentions_confirmed_by_strict_gate": len(
            [item for item in dino_correct if item["confirmed_dino_correct"]]
        ),
        "dino_correct_confirmation_rate": ratio(
            [item for item in dino_correct if item["confirmed_dino_correct"]],
            dino_correct,
        ),
        "mean_two_pass_latency_seconds": mean(
            [
                sum(float(run.get("latency_seconds") or 0.0) for run in item["passes"])
                for item in completed
            ]
        ),
    }
    output = {
        "schema_version": "finevision-low-confidence-recovery-result-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest": str(args.manifest),
        "service_url": args.service_url,
        "experiment": manifest["experiment"],
        "pool_summary": manifest["pool_summary"],
        "summary": summary,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Wrote benchmark result to {args.output}")


def ratio(numerator: list[Any], denominator: list[Any]) -> float | None:
    return float(len(numerator) / len(denominator)) if denominator else None


def mean(values: list[float]) -> float | None:
    return float(sum(values) / len(values)) if values else None


def main() -> None:
    args = parse_args()
    if args.command == "prepare":
        prepare_manifest(args)
    else:
        run_http_benchmark(args)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from finevision.api.inference_store import _inference_context_select, _threshold_strategy_from_row
from finevision.db.schema import dataset_versions, model_versions
from finevision.ml_toolkit.artifacts import load_feature_artifact, load_model_artifact
from finevision.ml_toolkit.features import build_extractor_from_config
from finevision.ml_toolkit.metrics import softmax
from finevision.ml_toolkit.training import apply_linear_head


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@dataclass(frozen=True)
class Sample:
    path: Path
    label: str


@dataclass(frozen=True)
class PolicyResult:
    method: str
    tau_conf: float
    tau_margin: float
    accepted: int
    errors: int
    coverage: float
    review_rate: float
    accepted_accuracy: float | None
    selective_risk: float | None


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate FineVision abstention thresholds on a labeled image folder.")
    parser.add_argument("--database-url", default="postgresql+psycopg://finevision:finevision@localhost:5432/finevision")
    parser.add_argument("--dataset-version-id", required=True)
    parser.add_argument("--model-version-id", required=True)
    parser.add_argument("--image-root", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--target-risk", type=float, default=0.05)
    parser.add_argument("--artifact-prefix", default="/data/artifacts")
    parser.add_argument("--artifact-root", default="/var/lib/docker/volumes/finevision_finevision-artifacts/_data", type=Path)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()

    samples = collect_balanced_samples(args.image_root, args.samples)
    context = load_context(
        database_url=args.database_url,
        dataset_version_id=args.dataset_version_id,
        model_version_id=args.model_version_id,
        artifact_prefix=args.artifact_prefix,
        artifact_root=args.artifact_root,
    )
    model_artifact, model_state, feature_artifact, reference_features, threshold_strategy = context
    extractor = build_extractor_from_config(
        feature_artifact.extractor_config,
        overrides={"device": args.device, "batch_size": args.batch_size},
    )

    image_paths = [str(sample.path) for sample in samples]
    labels = np.array([sample.label for sample in samples], dtype=object)
    query_features = extractor.extract_paths(image_paths)
    logits = apply_linear_head(
        query_features,
        model_state["weights"],
        model_state["bias"],
        model_state["feature_mean"],
        model_state["feature_std"],
    )
    probabilities = softmax(logits, temperature=threshold_strategy.temperature)
    order = np.argsort(probabilities, axis=1)[:, ::-1]
    top1_idx = order[:, 0]
    top2_idx = order[:, 1]
    classes = np.array(model_artifact.classes, dtype=object)
    top1_labels = classes[top1_idx]
    confidence = probabilities[np.arange(len(samples)), top1_idx]
    margin = confidence - probabilities[np.arange(len(samples)), top2_idx]
    correct = top1_labels == labels

    results = [
        evaluate_policy("no_abstention_raw_model", correct, confidence, margin, tau_conf=0.0, tau_margin=0.0),
        evaluate_policy(
            "current_saved_thresholds",
            correct,
            confidence,
            margin,
            tau_conf=float(threshold_strategy.accept_threshold),
            tau_margin=float(threshold_strategy.margin_threshold),
        ),
        search_confidence_only(correct, confidence, margin, target_risk=args.target_risk),
        search_joint_thresholds(correct, confidence, margin, target_risk=args.target_risk),
    ]

    payload = {
        "dataset_version_id": args.dataset_version_id,
        "model_version_id": args.model_version_id,
        "image_root": str(args.image_root),
        "sample_count": len(samples),
        "class_count": len(set(labels.tolist())),
        "target_selective_risk": args.target_risk,
        "raw_accuracy": float(np.mean(correct)),
        "results": [result.__dict__ for result in results],
        "notes": [
            "This run uses in-domain CIFAR-100 labels only; OOD precision/recall is not estimated.",
            "Review rate here means abstention rate on the 200-image replay set.",
        ],
    }
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print_report(payload)


def collect_balanced_samples(root: Path, sample_count: int) -> list[Sample]:
    split_root = root.expanduser()
    class_dirs = sorted(path for path in split_root.iterdir() if path.is_dir())
    if not class_dirs:
        raise ValueError(f"No class directories found under {split_root}")
    per_class = max(1, sample_count // len(class_dirs))
    selected: list[Sample] = []
    for class_dir in class_dirs:
        images = sorted(path for path in class_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)
        selected.extend(Sample(path=path, label=class_dir.name) for path in images[:per_class])
    if len(selected) < sample_count:
        used = {sample.path for sample in selected}
        extras = [
            Sample(path=path, label=path.parent.name)
            for class_dir in class_dirs
            for path in sorted(class_dir.iterdir())
            if path.suffix.lower() in IMAGE_SUFFIXES and path not in used
        ]
        selected.extend(extras[: sample_count - len(selected)])
    return selected[:sample_count]


def load_context(
    *,
    database_url: str,
    dataset_version_id: str,
    model_version_id: str,
    artifact_prefix: str,
    artifact_root: Path,
) -> tuple[Any, dict[str, np.ndarray], Any, np.ndarray, Any]:
    engine = sa.create_engine(database_url)
    with engine.begin() as conn:
        row = conn.execute(
            _inference_context_select().where(
                dataset_versions.c.version_key == dataset_version_id,
                model_versions.c.model_key == model_version_id,
            )
        ).mappings().first()
    if row is None:
        raise ValueError(f"Model context not found: {dataset_version_id} / {model_version_id}")

    model_uri = rewrite_artifact_path(str(row["model_uri"]), artifact_prefix=artifact_prefix, artifact_root=artifact_root)
    feature_uri = rewrite_artifact_path(str(row["feature_uri"]), artifact_prefix=artifact_prefix, artifact_root=artifact_root)
    model_artifact, model_state = load_model_artifact(model_uri.parent)
    feature_artifact, features = load_feature_artifact(feature_uri.parent)
    threshold_strategy = _threshold_strategy_from_row(row)
    return model_artifact, model_state, feature_artifact, features, threshold_strategy


def rewrite_artifact_path(uri: str, *, artifact_prefix: str, artifact_root: Path) -> Path:
    if uri.startswith(artifact_prefix):
        return artifact_root / Path(uri).relative_to(artifact_prefix)
    return Path(uri)


def evaluate_policy(
    method: str,
    correct: np.ndarray,
    confidence: np.ndarray,
    margin: np.ndarray,
    *,
    tau_conf: float,
    tau_margin: float,
) -> PolicyResult:
    accepted_mask = (confidence >= tau_conf) & (margin >= tau_margin)
    accepted = int(np.sum(accepted_mask))
    errors = int(np.sum(accepted_mask & ~correct))
    total = int(correct.shape[0])
    accepted_accuracy = float(np.mean(correct[accepted_mask])) if accepted else None
    selective_risk = float(errors / accepted) if accepted else None
    return PolicyResult(
        method=method,
        tau_conf=float(tau_conf),
        tau_margin=float(tau_margin),
        accepted=accepted,
        errors=errors,
        coverage=float(accepted / total),
        review_rate=float(1.0 - accepted / total),
        accepted_accuracy=accepted_accuracy,
        selective_risk=selective_risk,
    )


def search_confidence_only(
    correct: np.ndarray,
    confidence: np.ndarray,
    margin: np.ndarray,
    *,
    target_risk: float,
) -> PolicyResult:
    candidates = sorted(set(np.quantile(confidence, np.linspace(0.0, 1.0, 101)).tolist() + [0.0, 1.0]))
    return best_under_risk(
        [
            evaluate_policy("confidence_only_risk_constrained", correct, confidence, margin, tau_conf=tau, tau_margin=0.0)
            for tau in candidates
        ],
        target_risk=target_risk,
    )


def search_joint_thresholds(
    correct: np.ndarray,
    confidence: np.ndarray,
    margin: np.ndarray,
    *,
    target_risk: float,
) -> PolicyResult:
    conf_candidates = sorted(set(np.quantile(confidence, np.linspace(0.0, 1.0, 51)).tolist() + [0.0, 1.0]))
    margin_candidates = sorted(set(np.quantile(margin, np.linspace(0.0, 1.0, 51)).tolist() + [0.0]))
    results = [
        evaluate_policy("joint_confidence_margin_risk_constrained", correct, confidence, margin, tau_conf=tau_conf, tau_margin=tau_margin)
        for tau_conf in conf_candidates
        for tau_margin in margin_candidates
    ]
    return best_under_risk(results, target_risk=target_risk)


def best_under_risk(results: list[PolicyResult], *, target_risk: float) -> PolicyResult:
    feasible = [
        result
        for result in results
        if result.accepted > 0 and result.selective_risk is not None and result.selective_risk <= target_risk
    ]
    if feasible:
        return max(feasible, key=lambda result: (result.coverage, result.accepted_accuracy or 0.0))
    non_empty = [result for result in results if result.accepted > 0]
    return min(non_empty, key=lambda result: (result.selective_risk or 1.0, -result.coverage))


def print_report(payload: dict[str, Any]) -> None:
    print(f"Dataset: {payload['dataset_version_id']}")
    print(f"Model: {payload['model_version_id']}")
    print(f"Samples: {payload['sample_count']} images / {payload['class_count']} classes")
    print(f"Raw top-1 accuracy: {payload['raw_accuracy']:.2%}")
    print()
    print("| Method | tau_conf | tau_margin | Accepted Acc | Selective Risk | Coverage | Review Rate | Accepted | Errors |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in payload["results"]:
        print(
            "| {method} | {tau_conf:.4f} | {tau_margin:.4f} | {acc} | {risk} | {coverage:.2%} | {review_rate:.2%} | {accepted} | {errors} |".format(
                method=row["method"],
                tau_conf=row["tau_conf"],
                tau_margin=row["tau_margin"],
                acc=format_optional_percent(row["accepted_accuracy"]),
                risk=format_optional_percent(row["selective_risk"]),
                coverage=row["coverage"],
                review_rate=row["review_rate"],
                accepted=row["accepted"],
                errors=row["errors"],
            )
        )


def format_optional_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


if __name__ == "__main__":
    main()

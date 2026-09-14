from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from finevision.persistence.inference_store import _inference_context_select, _threshold_strategy_from_row
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
    tau_ood: float | None
    accepted: int
    rejected_ood: int
    reviewed: int
    errors: int
    coverage: float
    auto_coverage: float
    review_rate: float
    accepted_accuracy: float | None
    selective_risk: float | None
    ood_precision: float | None = None
    ood_recall: float | None = None
    ood_false_positive_rate: float | None = None
    ood_accept_rate: float | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate FineVision abstention thresholds on a labeled image folder.")
    parser.add_argument("--database-url", default="postgresql+psycopg://finevision:finevision@localhost:5432/finevision")
    parser.add_argument("--dataset-version-id", required=True)
    parser.add_argument("--model-version-id", required=True)
    parser.add_argument("--image-root", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--ood-root", type=Path)
    parser.add_argument("--ood-samples", type=int, default=200)
    parser.add_argument("--target-risk", type=float, default=0.05)
    parser.add_argument("--max-id-ood-reject-rate", type=float, default=0.05)
    parser.add_argument("--artifact-prefix", default="/data/artifacts")
    parser.add_argument("--artifact-root", default="/var/lib/docker/volumes/finevision_finevision-artifacts/_data", type=Path)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()

    samples = collect_balanced_samples(args.image_root, args.samples)
    ood_samples = collect_balanced_samples(args.ood_root, args.ood_samples) if args.ood_root else []
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

    all_samples = [*samples, *ood_samples]
    image_paths = [str(sample.path) for sample in all_samples]
    labels = np.array([sample.label for sample in all_samples], dtype=object)
    is_ood = np.array([False] * len(samples) + [True] * len(ood_samples), dtype=bool)
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
    row_index = np.arange(len(all_samples))
    confidence = probabilities[row_index, top1_idx]
    margin = confidence - probabilities[row_index, top2_idx]
    ood_score = nearest_neighbor_distances(query_features, reference_features)
    correct = (~is_ood) & (top1_labels == labels)

    results = [
        evaluate_policy("no_abstention_raw_model", correct, is_ood, confidence, margin, ood_score, tau_conf=0.0, tau_margin=0.0),
        evaluate_policy(
            "current_saved_thresholds",
            correct,
            is_ood,
            confidence,
            margin,
            ood_score,
            tau_conf=float(threshold_strategy.accept_threshold),
            tau_margin=float(threshold_strategy.margin_threshold),
        ),
        search_confidence_only(correct, is_ood, confidence, margin, ood_score, target_risk=args.target_risk),
        search_joint_thresholds(correct, is_ood, confidence, margin, ood_score, target_risk=args.target_risk),
    ]
    if len(ood_samples) > 0:
        results.append(
            search_joint_thresholds(
                correct,
                is_ood,
                confidence,
                margin,
                ood_score,
                target_risk=args.target_risk,
                include_ood=True,
                max_id_ood_reject_rate=args.max_id_ood_reject_rate,
            )
        )

    payload = {
        "dataset_version_id": args.dataset_version_id,
        "model_version_id": args.model_version_id,
        "image_root": str(args.image_root),
        "ood_root": str(args.ood_root) if args.ood_root else None,
        "sample_count": len(samples),
        "ood_sample_count": len(ood_samples),
        "class_count": len(set(labels[~is_ood].tolist())),
        "target_selective_risk": args.target_risk,
        "max_id_ood_reject_rate": args.max_id_ood_reject_rate if len(ood_samples) > 0 else None,
        "raw_accuracy": float(np.mean(correct[~is_ood])),
        "results": [result.__dict__ for result in results],
        "notes": [
            "Selective risk treats accepted OOD samples as incorrect accepted predictions.",
            "OOD precision/recall are estimated only when --ood-root is provided.",
            "Review rate means abstention/manual-review rate on this replay set.",
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


def nearest_neighbor_distances(query_features: np.ndarray, reference_features: np.ndarray, *, chunk_size: int = 64) -> np.ndarray:
    distances: list[np.ndarray] = []
    reference_norm = np.sum(reference_features.astype(np.float32) ** 2, axis=1).reshape(1, -1)
    reference_t = reference_features.astype(np.float32).T
    for start in range(0, query_features.shape[0], chunk_size):
        query = query_features[start : start + chunk_size].astype(np.float32)
        query_norm = np.sum(query**2, axis=1).reshape(-1, 1)
        squared = np.maximum(query_norm + reference_norm - 2.0 * query @ reference_t, 0.0)
        distances.append(np.sqrt(np.min(squared, axis=1)))
    return np.concatenate(distances).astype(np.float32)


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
    is_ood: np.ndarray,
    confidence: np.ndarray,
    margin: np.ndarray,
    ood_score: np.ndarray,
    *,
    tau_conf: float,
    tau_margin: float,
    tau_ood: float | None = None,
) -> PolicyResult:
    rejected_ood_mask = np.zeros_like(is_ood, dtype=bool)
    if tau_ood is not None:
        rejected_ood_mask = ood_score > tau_ood
    accepted_mask = (~rejected_ood_mask) & (confidence >= tau_conf) & (margin >= tau_margin)
    reviewed_mask = ~(accepted_mask | rejected_ood_mask)
    accepted = int(np.sum(accepted_mask))
    rejected_ood = int(np.sum(rejected_ood_mask))
    reviewed = int(np.sum(reviewed_mask))
    errors = int(np.sum(accepted_mask & ~correct))
    total = int(correct.shape[0])
    accepted_accuracy = float(np.mean(correct[accepted_mask])) if accepted else None
    selective_risk = float(errors / accepted) if accepted else None
    true_ood = int(np.sum(is_ood))
    id_count = int(np.sum(~is_ood))
    rejected_true_ood = int(np.sum(rejected_ood_mask & is_ood))
    rejected_id = int(np.sum(rejected_ood_mask & ~is_ood))
    accepted_ood = int(np.sum(accepted_mask & is_ood))
    ood_precision = float(rejected_true_ood / rejected_ood) if rejected_ood else None
    ood_recall = float(rejected_true_ood / true_ood) if true_ood else None
    ood_false_positive_rate = float(rejected_id / id_count) if id_count else None
    ood_accept_rate = float(accepted_ood / true_ood) if true_ood else None
    return PolicyResult(
        method=method,
        tau_conf=float(tau_conf),
        tau_margin=float(tau_margin),
        tau_ood=float(tau_ood) if tau_ood is not None else None,
        accepted=accepted,
        rejected_ood=rejected_ood,
        reviewed=reviewed,
        errors=errors,
        coverage=float(accepted / total),
        auto_coverage=float((accepted + rejected_ood) / total),
        # Manual review excludes both accepted and automatic OOD rejection.
        # Keep coverage above as accepted coverage for backward compatibility.
        # auto_coverage is the operational automation rate when OOD is enabled.
        review_rate=float(reviewed / total),
        accepted_accuracy=accepted_accuracy,
        selective_risk=selective_risk,
        ood_precision=ood_precision,
        ood_recall=ood_recall,
        ood_false_positive_rate=ood_false_positive_rate,
        ood_accept_rate=ood_accept_rate,
    )


def search_confidence_only(
    correct: np.ndarray,
    is_ood: np.ndarray,
    confidence: np.ndarray,
    margin: np.ndarray,
    ood_score: np.ndarray,
    *,
    target_risk: float,
) -> PolicyResult:
    candidates = sorted(set(np.quantile(confidence, np.linspace(0.0, 1.0, 101)).tolist() + [0.0, 1.0]))
    return best_under_risk(
        [
            evaluate_policy("confidence_only_risk_constrained", correct, is_ood, confidence, margin, ood_score, tau_conf=tau, tau_margin=0.0)
            for tau in candidates
        ],
        target_risk=target_risk,
    )


def search_joint_thresholds(
    correct: np.ndarray,
    is_ood: np.ndarray,
    confidence: np.ndarray,
    margin: np.ndarray,
    ood_score: np.ndarray,
    *,
    target_risk: float,
    include_ood: bool = False,
    max_id_ood_reject_rate: float = 0.05,
) -> PolicyResult:
    conf_candidates = sorted(set(np.quantile(confidence, np.linspace(0.0, 1.0, 51)).tolist() + [0.0, 1.0]))
    margin_candidates = sorted(set(np.quantile(margin, np.linspace(0.0, 1.0, 51)).tolist() + [0.0]))
    ood_candidates: list[float | None] = [None]
    method = "joint_confidence_margin_risk_constrained"
    if include_ood:
        ood_candidates = sorted(set(np.quantile(ood_score, np.linspace(0.0, 1.0, 51)).tolist()))
        method = "joint_confidence_margin_ood_risk_constrained"
    results = [
        evaluate_policy(method, correct, is_ood, confidence, margin, ood_score, tau_conf=tau_conf, tau_margin=tau_margin, tau_ood=tau_ood)
        for tau_conf in conf_candidates
        for tau_margin in margin_candidates
        for tau_ood in ood_candidates
    ]
    return best_under_risk(results, target_risk=target_risk, max_id_ood_reject_rate=max_id_ood_reject_rate if include_ood else None)


def best_under_risk(
    results: list[PolicyResult],
    *,
    target_risk: float,
    max_id_ood_reject_rate: float | None = None,
) -> PolicyResult:
    feasible = [
        result
        for result in results
        if result.accepted > 0 and result.selective_risk is not None and result.selective_risk <= target_risk
        and (max_id_ood_reject_rate is None or (result.ood_false_positive_rate or 0.0) <= max_id_ood_reject_rate)
    ]
    if feasible:
        return max(feasible, key=lambda result: (result.auto_coverage, result.ood_recall or 0.0, result.accepted_accuracy or 0.0))
    non_empty = [result for result in results if result.accepted > 0]
    return min(non_empty, key=lambda result: (result.selective_risk or 1.0, -result.coverage))


def print_report(payload: dict[str, Any]) -> None:
    print(f"Dataset: {payload['dataset_version_id']}")
    print(f"Model: {payload['model_version_id']}")
    print(f"Samples: {payload['sample_count']} images / {payload['class_count']} classes")
    print(f"Raw top-1 accuracy: {payload['raw_accuracy']:.2%}")
    print()
    has_ood = bool(payload.get("ood_sample_count"))
    if has_ood:
        print(f"OOD samples: {payload['ood_sample_count']}")
        print()
        print("| Method | tau_conf | tau_margin | tau_ood | Accepted Acc | Selective Risk | Accept Coverage | Auto Coverage | Review Rate | OOD Precision | OOD Recall | ID OOD FPR | OOD Accept Rate |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    else:
        print("| Method | tau_conf | tau_margin | Accepted Acc | Selective Risk | Coverage | Review Rate | Accepted | Errors |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in payload["results"]:
        if has_ood:
            print(
                "| {method} | {tau_conf:.4f} | {tau_margin:.4f} | {tau_ood} | {acc} | {risk} | {coverage:.2%} | {auto_coverage:.2%} | {review_rate:.2%} | {ood_precision} | {ood_recall} | {ood_fpr} | {ood_accept} |".format(
                    method=row["method"],
                    tau_conf=row["tau_conf"],
                    tau_margin=row["tau_margin"],
                    tau_ood=format_optional_float(row["tau_ood"]),
                    acc=format_optional_percent(row["accepted_accuracy"]),
                    risk=format_optional_percent(row["selective_risk"]),
                    coverage=row["coverage"],
                    auto_coverage=row["auto_coverage"],
                    review_rate=row["review_rate"],
                    ood_precision=format_optional_percent(row["ood_precision"]),
                    ood_recall=format_optional_percent(row["ood_recall"]),
                    ood_fpr=format_optional_percent(row["ood_false_positive_rate"]),
                    ood_accept=format_optional_percent(row["ood_accept_rate"]),
                )
            )
        else:
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


def format_optional_float(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


if __name__ == "__main__":
    main()

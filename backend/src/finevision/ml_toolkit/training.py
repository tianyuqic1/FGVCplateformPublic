from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from finevision.ml_toolkit.artifacts import write_json, write_model_artifact
from finevision.ml_toolkit.metrics import classification_report
from finevision.schemas.artifacts import FeatureArtifact, ModelArtifact, TrainingRunReport


def _encode_labels(labels: list[str], classes: list[str]) -> np.ndarray:
    index = {label: pos for pos, label in enumerate(classes)}
    return np.array([index[label] for label in labels], dtype=np.int64)


def _one_hot(y: np.ndarray, class_count: int) -> np.ndarray:
    matrix = np.zeros((len(y), class_count), dtype=np.float32)
    matrix[np.arange(len(y)), y] = 1.0
    return matrix


def _standardize_train(features: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = features.mean(axis=0)
    std = features.std(axis=0)
    std[std < 1e-6] = 1.0
    return (features - mean) / std, mean, std


def apply_linear_head(features: np.ndarray, weights: np.ndarray, bias: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    safe_std = np.where(std < 1e-6, 1.0, std)
    normalized = (features - mean) / safe_std
    return normalized @ weights + bias


def _train_linear_head_numpy(
    features: np.ndarray,
    y: np.ndarray,
    train_mask: np.ndarray,
    class_count: int,
    ridge_lambda: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    x_train, mean, std = _standardize_train(features[train_mask])
    y_train = y[train_mask]
    targets = _one_hot(y_train, class_count)

    xtx = x_train.T @ x_train
    regularizer = ridge_lambda * np.eye(xtx.shape[0], dtype=np.float32)
    weights = np.linalg.solve(xtx + regularizer, x_train.T @ targets)
    bias = targets.mean(axis=0) - x_train.mean(axis=0) @ weights
    logits = apply_linear_head(features, weights, bias, mean, std)
    return weights, bias, mean, std, logits


def _train_linear_head_torch(
    features: np.ndarray,
    y: np.ndarray,
    train_mask: np.ndarray,
    class_count: int,
    ridge_lambda: float,
    device: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    try:
        import torch
        import torch.nn.functional as functional
    except ImportError as exc:
        raise RuntimeError("GPU linear head training requires torch to be installed.") from exc

    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("FINEVISION_LINEAR_HEAD_DEVICE is cuda, but torch.cuda.is_available() is false.")

    torch_device = torch.device(device)
    x_all = torch.as_tensor(features, dtype=torch.float32, device=torch_device)
    y_all = torch.as_tensor(y, dtype=torch.long, device=torch_device)
    train_mask_tensor = torch.as_tensor(train_mask, dtype=torch.bool, device=torch_device)
    x_train_raw = x_all[train_mask_tensor]
    y_train = y_all[train_mask_tensor]

    mean = x_train_raw.mean(dim=0)
    std = x_train_raw.std(dim=0, unbiased=False)
    std = torch.where(std < 1e-6, torch.ones_like(std), std)
    x_train = (x_train_raw - mean) / std
    targets = functional.one_hot(y_train, num_classes=class_count).to(dtype=torch.float32)

    xtx = x_train.T @ x_train
    identity = torch.eye(xtx.shape[0], dtype=torch.float32, device=torch_device)
    weights = torch.linalg.solve(xtx + ridge_lambda * identity, x_train.T @ targets)
    bias = targets.mean(dim=0) - x_train.mean(dim=0) @ weights
    logits = ((x_all - mean) / std) @ weights + bias

    return (
        weights.detach().cpu().numpy().astype(np.float32),
        bias.detach().cpu().numpy().astype(np.float32),
        mean.detach().cpu().numpy().astype(np.float32),
        std.detach().cpu().numpy().astype(np.float32),
        logits.detach().cpu().numpy().astype(np.float32),
    )


def _train_linear_head_adam_torch(
    features: np.ndarray,
    y: np.ndarray,
    train_mask: np.ndarray,
    eval_mask: np.ndarray,
    class_count: int,
    *,
    learning_rate: float,
    epochs: int,
    batch_size: int,
    weight_decay: float,
    device: str,
    progress_callback: Callable[[int, int, dict[str, float]], None] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[dict[str, float]]]:
    try:
        import torch
        import torch.nn.functional as functional
    except ImportError as exc:
        raise RuntimeError("Adam linear head training requires torch to be installed.") from exc

    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("FINEVISION_LINEAR_HEAD_DEVICE is cuda, but torch.cuda.is_available() is false.")

    torch_device = torch.device(device)
    x_all = torch.as_tensor(features, dtype=torch.float32, device=torch_device)
    y_all = torch.as_tensor(y, dtype=torch.long, device=torch_device)
    train_mask_tensor = torch.as_tensor(train_mask, dtype=torch.bool, device=torch_device)
    eval_mask_tensor = torch.as_tensor(eval_mask, dtype=torch.bool, device=torch_device)

    x_train_raw = x_all[train_mask_tensor]
    y_train = y_all[train_mask_tensor]
    mean = x_train_raw.mean(dim=0)
    std = x_train_raw.std(dim=0, unbiased=False)
    std = torch.where(std < 1e-6, torch.ones_like(std), std)
    x_train = (x_train_raw - mean) / std
    x_eval = (x_all[eval_mask_tensor] - mean) / std
    y_eval = y_all[eval_mask_tensor]

    head = torch.nn.Linear(x_train.shape[1], class_count).to(torch_device)
    optimizer = torch.optim.Adam(head.parameters(), lr=learning_rate, weight_decay=weight_decay)
    history: list[dict[str, float]] = []
    sample_count = x_train.shape[0]
    effective_batch_size = max(1, min(batch_size, sample_count))

    for epoch in range(1, epochs + 1):
        permutation = torch.randperm(sample_count, device=torch_device)
        total_loss = 0.0
        total_seen = 0
        head.train()
        for start in range(0, sample_count, effective_batch_size):
            indices = permutation[start : start + effective_batch_size]
            batch_x = x_train[indices]
            batch_y = y_train[indices]
            optimizer.zero_grad(set_to_none=True)
            loss = functional.cross_entropy(head(batch_x), batch_y)
            loss.backward()
            optimizer.step()
            batch_size_seen = int(batch_y.shape[0])
            total_loss += float(loss.detach().cpu()) * batch_size_seen
            total_seen += batch_size_seen

        head.eval()
        with torch.inference_mode():
            eval_logits = head(x_eval)
            eval_pred = eval_logits.argmax(dim=1)
            eval_accuracy = float((eval_pred == y_eval).float().mean().detach().cpu()) if y_eval.numel() else 0.0

        epoch_metrics = {
            "epoch": float(epoch),
            "train_loss": total_loss / max(total_seen, 1),
            "eval_accuracy": eval_accuracy,
        }
        history.append(epoch_metrics)
        if progress_callback is not None:
            progress_callback(epoch, epochs, epoch_metrics)

    with torch.inference_mode():
        logits = head((x_all - mean) / std)
        weights = head.weight.detach().T
        bias = head.bias.detach()

    return (
        weights.cpu().numpy().astype(np.float32),
        bias.cpu().numpy().astype(np.float32),
        mean.cpu().numpy().astype(np.float32),
        std.cpu().numpy().astype(np.float32),
        logits.cpu().numpy().astype(np.float32),
        history,
    )


def train_linear_head(
    feature_artifact: FeatureArtifact,
    features: np.ndarray,
    artifact_root: str | Path,
    run_id: str = "run-smoke",
    ridge_lambda: float = 1e-2,
    artifact_id: str | None = None,
    device: str = "cpu",
    head_type: str = "torch_linear_adam",
    learning_rate: float = 1e-3,
    epochs: int = 50,
    batch_size: int = 256,
    weight_decay: float = 1e-4,
    progress_callback: Callable[[int, int, dict[str, float]], None] | None = None,
) -> tuple[ModelArtifact, TrainingRunReport, np.ndarray]:
    labels = list(feature_artifact.labels)
    classes = sorted(set(labels))
    y = _encode_labels(labels, classes)
    splits = np.array(feature_artifact.splits)
    train_mask = splits == "train"
    eval_mask = np.isin(splits, ["val", "test"])
    if not train_mask.any():
        raise ValueError("Feature artifact has no train split samples")
    if not eval_mask.any():
        eval_mask = ~train_mask

    optimizer_history: list[dict[str, float]] = []
    if head_type == "torch_linear_adam":
        weights, bias, mean, std, logits, optimizer_history = _train_linear_head_adam_torch(
            features,
            y,
            train_mask,
            eval_mask,
            len(classes),
            learning_rate=learning_rate,
            epochs=epochs,
            batch_size=batch_size,
            weight_decay=weight_decay,
            device=device,
            progress_callback=progress_callback,
        )
        solver = "torch_adam"
    elif head_type == "ridge_linear" and device == "cpu":
        weights, bias, mean, std, logits = _train_linear_head_numpy(features, y, train_mask, len(classes), ridge_lambda)
        solver = "numpy"
    elif head_type == "ridge_linear":
        weights, bias, mean, std, logits = _train_linear_head_torch(features, y, train_mask, len(classes), ridge_lambda, device)
        solver = "torch"
    else:
        raise ValueError(f"Unsupported head_type: {head_type}")

    y_pred = logits[eval_mask].argmax(axis=1)
    run_config = {
        "run_id": run_id,
        "head_type": head_type,
        "ridge_lambda": ridge_lambda,
        "learning_rate": learning_rate,
        "epochs": epochs,
        "batch_size": batch_size,
        "weight_decay": weight_decay,
        "feature_artifact_id": feature_artifact.artifact_id,
        "head_device": device,
        "head_solver": solver,
        "optimizer_history": optimizer_history,
    }
    report = classification_report(
        y_true=y[eval_mask],
        y_pred=y_pred,
        classes=classes,
        run_config=run_config,
    )

    artifact_id = artifact_id or f"{feature_artifact.dataset_version_id}-linear-head"
    artifact_dir = Path(artifact_root) / artifact_id
    model_artifact = ModelArtifact(
        artifact_id=artifact_id,
        dataset_id=feature_artifact.dataset_id,
        dataset_version_id=feature_artifact.dataset_version_id,
        feature_artifact_id=feature_artifact.artifact_id,
        model_path=str(artifact_dir / "linear_head.npz"),
        classes=classes,
        head_type=head_type,
        feature_dim=feature_artifact.feature_dim,
        training_config={
            "run_id": run_id,
            "head_type": head_type,
            "ridge_lambda": ridge_lambda,
            "learning_rate": learning_rate,
            "epochs": epochs,
            "batch_size": batch_size,
            "weight_decay": weight_decay,
            "head_device": device,
            "head_solver": solver,
            "optimizer_history": optimizer_history,
        },
    )
    model_artifact = write_model_artifact(artifact_dir, model_artifact, weights, bias, mean, std)
    run_report = TrainingRunReport(
        run_id=run_id,
        dataset_id=feature_artifact.dataset_id,
        dataset_version_id=feature_artifact.dataset_version_id,
        feature_artifact_id=feature_artifact.artifact_id,
        model_artifact_id=model_artifact.artifact_id,
        run_config=run_config,
        evaluation=report,
    )
    write_json(artifact_dir / "training_report.json", run_report)
    return model_artifact, run_report, logits

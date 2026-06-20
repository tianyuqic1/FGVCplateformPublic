from __future__ import annotations

import hashlib
import random
from collections import Counter, defaultdict
from pathlib import Path

from finevision.schemas.artifacts import DatasetManifest, SampleRecord

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
EXPLICIT_SPLITS = ("train", "val", "test")


def _image_files(path: Path) -> list[Path]:
    return sorted(item for item in path.rglob("*") if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS)


def _stable_sample_id(dataset_version_id: str, path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    digest = hashlib.sha1(f"{dataset_version_id}:{rel}".encode("utf-8")).hexdigest()[:12]
    return f"sample-{digest}"


def _split_items(items: list[Path], seed: int, ratios: tuple[float, float, float]) -> dict[str, list[Path]]:
    shuffled = list(items)
    random.Random(seed).shuffle(shuffled)
    total = len(shuffled)
    if total == 0:
        return {"train": [], "val": [], "test": []}
    train_count = max(1, int(round(total * ratios[0])))
    val_count = int(round(total * ratios[1]))
    if total >= 3:
        val_count = max(1, val_count)
    if train_count + val_count >= total:
        train_count = max(1, total - 2) if total >= 3 else max(1, total - 1)
        val_count = 1 if total >= 3 else 0
    return {
        "train": shuffled[:train_count],
        "val": shuffled[train_count : train_count + val_count],
        "test": shuffled[train_count + val_count :],
    }


def scan_imagefolder(
    root: str | Path,
    dataset_id: str,
    dataset_version_id: str,
    split_ratios: tuple[float, float, float] = (0.7, 0.15, 0.15),
    seed: int = 13,
) -> DatasetManifest:
    root_path = Path(root).resolve()
    if not root_path.exists():
        raise FileNotFoundError(root_path)

    top_level_dirs = sorted(item for item in root_path.iterdir() if item.is_dir())
    split_dirs = [item for item in top_level_dirs if item.name in EXPLICIT_SPLITS]
    non_split_dirs = [item for item in top_level_dirs if item.name not in EXPLICIT_SPLITS]
    has_explicit_splits = bool(split_dirs) and not non_split_dirs
    samples: list[SampleRecord] = []
    classes: set[str] = set()

    if has_explicit_splits:
        for split in EXPLICIT_SPLITS:
            split_root = root_path / split
            if not split_root.exists():
                continue
            for class_dir in sorted(item for item in split_root.iterdir() if item.is_dir()):
                classes.add(class_dir.name)
                for image_path in _image_files(class_dir):
                    samples.append(
                        SampleRecord(
                            sample_id=_stable_sample_id(dataset_version_id, image_path, root_path),
                            path=str(image_path),
                            label=class_dir.name,
                            split=split,
                        )
                    )
    else:
        class_dirs = top_level_dirs
        for class_dir in class_dirs:
            images = _image_files(class_dir)
            if not images:
                continue
            classes.add(class_dir.name)
            split_map = _split_items(images, seed=seed, ratios=split_ratios)
            for split, split_images in split_map.items():
                for image_path in split_images:
                    samples.append(
                        SampleRecord(
                            sample_id=_stable_sample_id(dataset_version_id, image_path, root_path),
                            path=str(image_path),
                            label=class_dir.name,
                            split=split,  # type: ignore[arg-type]
                        )
                    )

    split_counts: dict[str, dict[str, int]] = {split: {} for split in EXPLICIT_SPLITS}
    by_split_class: dict[str, Counter[str]] = defaultdict(Counter)
    for sample in samples:
        by_split_class[sample.split][sample.label] += 1
    for split in EXPLICIT_SPLITS:
        split_counts[split] = dict(sorted(by_split_class[split].items()))

    per_class = Counter(sample.label for sample in samples)
    low_sample_classes = [label for label, count in sorted(per_class.items()) if count < 3]
    missing_split_classes = [
        label
        for label in sorted(classes)
        if any(by_split_class[split][label] == 0 for split in EXPLICIT_SPLITS)
    ]
    readiness = {
        "ready": bool(samples) and not low_sample_classes and not missing_split_classes,
        "provided_splits": has_explicit_splits,
        "sample_count": len(samples),
        "class_count": len(classes),
        "low_sample_classes": low_sample_classes,
        "missing_split_classes": missing_split_classes,
    }

    return DatasetManifest(
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        root=str(root_path),
        classes=sorted(classes),
        samples=sorted(samples, key=lambda item: (item.split, item.label, item.path)),
        split_counts=split_counts,
        readiness=readiness,
    )

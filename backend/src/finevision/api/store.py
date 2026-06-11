from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from finevision.ml_toolkit.artifacts import read_dataset_manifest, write_dataset_manifest
from finevision.schemas.artifacts import DatasetManifest


@dataclass(frozen=True)
class DatasetSummary:
    dataset_id: str
    latest_version_id: str
    dataset_version_id: str
    version_count: int
    classes: list[str]
    class_count: int
    sample_count: int
    status: str
    readiness: dict[str, Any]


class MetadataStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def save_dataset_manifest(self, manifest: DatasetManifest) -> Path:
        return write_dataset_manifest(self._manifest_path(manifest.dataset_id, manifest.dataset_version_id), manifest)

    def get_dataset_manifest(self, dataset_id: str, dataset_version_id: str) -> DatasetManifest | None:
        path = self._manifest_path(dataset_id, dataset_version_id)
        if not path.exists():
            return None
        return read_dataset_manifest(path)

    def get_dataset_version(self, dataset_version_id: str) -> DatasetManifest | None:
        matches = sorted(self.root.glob(f"datasets/*/versions/{dataset_version_id}/manifest.json"))
        if not matches:
            return None
        return read_dataset_manifest(matches[0])

    def list_dataset_manifests(self, dataset_id: str | None = None) -> list[DatasetManifest]:
        pattern = f"datasets/{dataset_id}/versions/*/manifest.json" if dataset_id else "datasets/*/versions/*/manifest.json"
        return [read_dataset_manifest(path) for path in sorted(self.root.glob(pattern))]

    def list_datasets(self) -> list[DatasetSummary]:
        grouped: dict[str, list[DatasetManifest]] = {}
        for manifest in self.list_dataset_manifests():
            grouped.setdefault(manifest.dataset_id, []).append(manifest)

        summaries: list[DatasetSummary] = []
        for dataset_id, manifests in sorted(grouped.items()):
            ordered = sorted(manifests, key=lambda item: item.dataset_version_id)
            latest = ordered[-1]
            summaries.append(
                DatasetSummary(
                    dataset_id=dataset_id,
                    latest_version_id=latest.dataset_version_id,
                    dataset_version_id=latest.dataset_version_id,
                    version_count=len(ordered),
                    classes=latest.classes,
                    class_count=len(latest.classes),
                    sample_count=len(latest.samples),
                    status=self.readiness_status(latest),
                    readiness=latest.readiness,
                )
            )
        return summaries

    def dataset_detail(self, dataset_id: str) -> dict[str, Any] | None:
        manifests = self.list_dataset_manifests(dataset_id)
        if not manifests:
            return None

        ordered = sorted(manifests, key=lambda item: item.dataset_version_id)
        latest = ordered[-1]
        return {
            "dataset_id": dataset_id,
            "latest_version_id": latest.dataset_version_id,
            "dataset_version_id": latest.dataset_version_id,
            "versions": [self.version_summary(manifest) for manifest in ordered],
            "classes": latest.classes,
            "class_count": len(latest.classes),
            "sample_count": len(latest.samples),
            "split_counts": latest.split_counts,
            "status": self.readiness_status(latest),
            "readiness": latest.readiness,
        }

    @staticmethod
    def readiness_status(manifest: DatasetManifest) -> str:
        return "ready" if manifest.readiness.get("ready") is True else "needs_attention"

    @staticmethod
    def version_summary(manifest: DatasetManifest) -> dict[str, Any]:
        split_totals = Counter(sample.split for sample in manifest.samples)
        return {
            "dataset_version_id": manifest.dataset_version_id,
            "root": manifest.root,
            "classes": manifest.classes,
            "class_count": len(manifest.classes),
            "sample_count": len(manifest.samples),
            "split_totals": dict(sorted(split_totals.items())),
            "split_counts": manifest.split_counts,
            "status": MetadataStore.readiness_status(manifest),
            "readiness": manifest.readiness,
            "artifact_refs": manifest.artifact_refs,
        }

    def _manifest_path(self, dataset_id: str, dataset_version_id: str) -> Path:
        return self.root / "datasets" / dataset_id / "versions" / dataset_version_id / "manifest.json"

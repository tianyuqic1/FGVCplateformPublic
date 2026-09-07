from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finevision.api import create_app


@dataclass
class _FakeRun:
    run_id: str = "vlm-run-test"

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "mode": "assisted",
            "status": "queued",
            "total_count": 2,
        }


class _FakeVLMStore:
    def create_run(self, **kwargs):
        assert kwargs["mode"] == "assisted"
        assert kwargs["config"]["reject_ood_allowed"] is False
        return _FakeRun()

    def list_runs(self, *, limit: int):
        return [_FakeRun()]

    def get_run(self, run_id: str):
        return _FakeRun(run_id=run_id)

    def list_results(self, run_id: str):
        return []


def test_create_vlm_review_run_requires_explicit_auto_risk_ack(tmp_path: Path) -> None:
    app = create_app(metadata_dir=tmp_path / "metadata")
    app.state.vlm_review_store = _FakeVLMStore()
    client = TestClient(app)

    response = client.post(
        "/api/vlm-review-runs",
        json={"mode": "auto", "limit": 10, "risk_acknowledged": False},
    )

    assert response.status_code == 422
    assert "explicit risk acknowledgement" in response.json()["detail"]


def test_vlm_review_run_api_contract(tmp_path: Path) -> None:
    app = create_app(metadata_dir=tmp_path / "metadata")
    app.state.vlm_review_store = _FakeVLMStore()
    client = TestClient(app)

    created = client.post(
        "/api/vlm-review-runs",
        json={"mode": "assisted", "limit": 2},
    )
    listed = client.get("/api/vlm-review-runs")
    detail = client.get("/api/vlm-review-runs/vlm-run-test")

    assert created.status_code == 202
    assert created.json()["vlm_review_run"]["run_id"] == "vlm-run-test"
    assert listed.json()["vlm_review_runs"][0]["status"] == "queued"
    assert detail.json()["results"] == []


def test_auto_mode_is_shadow_only_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINEVISION_FINER1_AUTO_ENABLED", raising=False)
    app = create_app(metadata_dir=tmp_path / "metadata")
    app.state.vlm_review_store = _FakeVLMStore()
    client = TestClient(app)

    response = client.post(
        "/api/vlm-review-runs",
        json={"mode": "auto", "limit": 10, "risk_acknowledged": True},
    )
    capabilities = client.get("/api/vlm-review-capabilities")

    assert response.status_code == 409
    assert "shadow benchmark" in response.json()["detail"]
    assert capabilities.json()["auto_enabled"] is False

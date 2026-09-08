from pathlib import Path

from fastapi.testclient import TestClient

from finevision.api.app import create_app


def test_finer1_routes_are_not_part_of_the_public_interface(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "metadata"))

    assert client.get("/api/vlm-review-capabilities").status_code == 404
    assert client.get("/api/vlm-review-runs").status_code == 404
    assert client.post("/api/vlm-review-runs", json={"mode": "assisted"}).status_code == 404

"""Opt-in smoke against a control plane using an isolated database clone.

FINEVISION_TEST_API_URL=http://localhost:18001 .venv/bin/python -m pytest ...
Never point this suite at the normal application: it writes review outcomes.
"""
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

BASE = os.environ.get("FINEVISION_TEST_API_URL", "")
pytestmark = pytest.mark.skipif(BASE != "http://localhost:18001", reason="requires isolated acceptance control plane on port 18001")


def request(path, body=None, method=None, content_type="application/json"):
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    req = Request(BASE + path, data=body, method=method, headers={"Content-Type": content_type})
    try:
        with urlopen(req, timeout=120) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw and "json" in response.headers.get("Content-Type", "") else raw
    except HTTPError as error:
        return error.code, error.read().decode()


def test_real_onnx_review_feedback_and_image_roundtrip():
    status, registry = request("/api/model-versions")
    assert status == 200
    models = registry.get("model_versions", registry.get("items", []))
    model = next(m for m in models if m.get("status") == "production" and any(a["artifact_type"] == "full_onnx" for a in m.get("artifacts", [])))
    model_id = model.get("model_version_id", model.get("id"))
    dataset = model["dataset_version_id"]
    image = Path("data/examples/toy-shapes-imagefolder/red_square/red_square_000.png").read_bytes()
    fields = {"dataset_version_id": dataset, "model_version_id": model_id, "accept_threshold": "1"}
    boundary = "acceptance-boundary-fgvc"
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode() for key, value in fields.items()]
    parts += [f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="test.png"\r\nContent-Type: image/png\r\n\r\n'.encode(), image, f'\r\n--{boundary}--\r\n'.encode()]
    status, payload = request("/api/inference/upload", b"".join(parts), content_type=f"multipart/form-data; boundary={boundary}")
    assert status == 200, payload
    result = payload["inference_result"]
    assert result["result"]["decision"]["decision"] == "abstain"
    assert result["input"]["input_ref"].startswith("s3://")
    status, downloaded = request(result["input"]["image_url"])
    assert status == 200 and downloaded == image
    review_id = result["review_item_id"]
    status, detail = request(f"/api/review-items/{review_id}")
    assert status == 200 and detail["review_item"]["status"] == "pending"
    assert detail["review_item"]["image_url"] == result["input"]["image_url"]
    submission = {"final_outcome": "corrected_label", "destination": "training_candidate", "final_label": "red_square", "reviewer": "isolated-acceptance"}
    status, submitted = request(f"/api/review-items/{review_id}/submit", submission)
    assert status == 200, submitted
    assert submitted["review_item"]["status"] == "feedbacked"
    assert request(f"/api/review-items/{review_id}/submit", submission)[0] == 409
    status, feedback = request("/api/feedback-items?destination=training_candidate&limit=300")
    assert status == 200
    saved = next(f for f in feedback["feedback_items"] if f["review_item_id"] == review_id)
    assert saved["final_label"] == "red_square" and saved["image_url"] == result["input"]["image_url"]
    status, candidates = request(f"/api/datasets/{result['dataset_id']}/training-candidates")
    assert status == 200, candidates
    status, previews = request(f"/api/dataset-versions/{dataset}/sample-previews?limit=1")
    assert status == 200 and previews["samples"]
    status, sample_result = request("/api/inference", {"model_version_id": model_id, "dataset_version_id": dataset, "sample_id": previews["samples"][0]["sample_id"]})
    assert status == 200, sample_result
    # One valid image and one corrupt file must produce an honest partial result.
    folder_parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode() for key, value in fields.items()]
    for name, data in [("valid.png", image), ("broken.png", b"not an image")]:
        folder_parts += [f'--{boundary}\r\nContent-Disposition: form-data; name="images"; filename="{name}"\r\nContent-Type: image/png\r\n\r\n'.encode(), data, b"\r\n"]
    folder_parts += [f'--{boundary}--\r\n'.encode()]
    status, batch = request("/api/inference/upload-folder", b"".join(folder_parts), content_type=f"multipart/form-data; boundary={boundary}")
    assert status == 200, batch
    assert batch["batch"]["succeeded"] == 1 and batch["batch"]["failed"] == 1
    assert batch["batch"]["review_item_count"] == 1
    assert request("/api/inference", {"model_version_id": model_id, "dataset_version_id": "other", "sample_id": "x"})[0] == 422
    candidate = next(m for m in models if m.get("status") == "candidate")
    assert request("/api/inference", {"model_version_id": candidate.get("model_version_id", candidate.get("id")), "dataset_version_id": candidate["dataset_version_id"], "sample_id": "x"})[0] == 409


def test_missing_route_regressions_and_validation():
    for path in ["/api/feedback-items?limit=6", "/api/review-items?status=pending&limit=6", "/api/abstention-policies"]:
        assert request(path)[0] == 200
    for path in ["/api/feedback-items?limit=0", "/api/review-items?status=invalid", "/api/abstention-policies?status=invalid"]:
        assert request(path)[0] == 422
    assert request("/api/review-items/not-found")[0] == 404
    assert request("/api/training-runs/not-found", method="DELETE")[0] == 404
    assert request("/api/uploads/not-a-valid-image")[0] == 404

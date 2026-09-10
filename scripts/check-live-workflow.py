"""Opt-in local acceptance: real folder upload -> queue -> ViT-S -> registry.

Creates labelled acceptance records; does not delete datasets or models.
Run with the project Python environment after docker compose up.
"""
import argparse
from contextlib import ExitStack
from pathlib import Path
import time
import uuid

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:5173")
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[1] / "data/examples/toy-shapes-imagefolder")
    parser.add_argument("--train", action="store_true")
    args = parser.parse_args()
    key = "acceptance-" + uuid.uuid4().hex[:10]
    with httpx.Client(base_url=args.url, timeout=120) as client, ExitStack() as files:
        inputs = [("files", (f"{args.dataset.name}/{path.relative_to(args.dataset).as_posix()}", files.enter_context(path.open("rb")), "image/png")) for path in sorted(args.dataset.rglob("*.png"))]
        assert inputs, "fixture contains no PNG images"
        response = client.post("/api/datasets/upload-imagefolder", data={"name": key, "request_id": str(uuid.uuid4())}, files=inputs)
        assert response.status_code == 202, response.text
        job_id = response.json()["job"]["id"]
        print(f"IMPORT QUEUED job={job_id}", flush=True)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            response = client.get("/api/dataset-imports")
            assert response.status_code == 200, response.text
            job = next((item for item in response.json()["jobs"] if item["id"] == job_id), None)
            assert job is not None, "accepted import missing from queue"
            if job["status"] in ("succeeded", "failed"):
                assert job["status"] == "succeeded", job
                imported = job["result"]
                break
            time.sleep(1)
        else:
            raise AssertionError(f"Import timed out; inspect job {job_id}")
        key = imported["dataset"]["dataset_id"]
        version = imported["version"]["dataset_version_id"]
        assert imported["upload"]["image_count"] == len(inputs), imported
        print(f"UPLOAD PASS dataset={key} version={version}", flush=True)
        detail = client.get(f"/api/datasets/{key}")
        assert detail.status_code == 200, detail.text
        print("DATASET DETAIL PASS", flush=True)
        if not args.train:
            return
        response = client.post("/api/training-runs", json={"dataset_version_id": version, "backbone_key": "dinov3_vits16_lvd1689m", "head_config": {"head_type": "torch_linear_adam", "epochs": 2}})
        assert response.status_code == 202, response.text
        run = response.json()["training_run"]
        run_id = run.get("id") or run["training_run_id"]
        print(f"TRAIN CREATED run={run_id}", flush=True)
        deadline = time.monotonic() + 900
        previous = None
        while time.monotonic() < deadline:
            response = client.get(f"/api/training-runs/{run_id}")
            assert response.status_code == 200, response.text
            run = response.json()["training_run"]
            if run["status"] != previous:
                print(f"TRAIN {run['status']}", flush=True)
                previous = run["status"]
            if run["status"] in ("succeeded", "failed", "cancelled"):
                assert run["status"] == "succeeded", run
                break
            time.sleep(2)
        else:
            raise AssertionError(f"Training timed out; inspect run {run_id}")
        metrics = client.get(f"/api/training-runs/{run_id}/metrics")
        assert metrics.status_code == 200, metrics.text
        points = metrics.json().get("metric_points", [])
        assert len(points) >= 4, metrics.text
        model_id = run["model_version_id"]
        model = client.get(f"/api/model-versions/{model_id}")
        assert model.status_code == 200, model.text
        print(f"METRICS + MODEL REGISTRY PASS model={model_id} points={len(points)}", flush=True)


if __name__ == "__main__":
    main()

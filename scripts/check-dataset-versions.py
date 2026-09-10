"""Opt-in HTTP acceptance using generated images; creates a labelled test dataset.

Run against an isolated, migrated control plane connected to DatasetCompute.
"""
import argparse
import io
import uuid

import httpx
from PIL import Image


def png(color):
    output = io.BytesIO()
    Image.new("RGB", (12, 12), color).save(output, format="PNG")
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    files = []
    for index, split in enumerate(["train", "val", "test"]):
        for label, color in [("bird", (index * 40, 20, 200)), ("cat", (200, index * 40, 20))]:
            files.append(("files", (f"fixture/{split}/{label}/original.png", png(color), "image/png")))
    with httpx.Client(base_url=args.url, timeout=120) as client:
        fields = {"name": "版本管理验收 · 训练集扩充", "request_id": str(uuid.uuid4())}
        first = client.post("/api/datasets/upload-imagefolder", data=fields, files=files)
        assert first.status_code == 201, first.text
        first = first.json()["version"]
        dataset_id, v1 = first["dataset_id"], first["dataset_version_id"]
        assert first["version_number"] == 1 and dataset_id != v1
        assert first["name"] == fields["name"]
        replay = client.post("/api/datasets/upload-imagefolder", data=fields, files=files)
        assert replay.status_code == 201 and replay.json()["version"]["dataset_version_id"] == v1, replay.text
        path = f"/api/datasets/{dataset_id}/versions"
        fields = {"base_version_id": v1, "request_id": str(uuid.uuid4())}
        additions = [("files", ("extra/bird/new.png", png((11, 12, 13)), "image/png"))]
        second = client.post(path, data=fields, files=additions)
        assert second.status_code == 201, second.text
        second = second.json()["version"]
        assert second["version_number"] == 2 and second["parent_version_id"] == v1
        assert second["split_counts"]["val"] == first["split_counts"]["val"]
        assert second["split_counts"]["test"] == first["split_counts"]["test"]
        assert second["sample_count"] == first["sample_count"] + 1
        replay = client.post(path, data=fields, files=additions)
        assert replay.status_code == 201 and replay.json()["version"]["dataset_version_id"] == second["dataset_version_id"], replay.text
        stale = client.post(path, data={**fields, "request_id": str(uuid.uuid4())}, files=additions)
        assert stale.status_code == 409, stale.text
        forbidden = client.post(path, data={"base_version_id": second["dataset_version_id"], "request_id": str(uuid.uuid4())}, files=[("files", ("extra/test/bird/new.png", png((55, 66, 77)), "image/png"))])
        assert forbidden.status_code == 422, forbidden.text
        detail = client.get(f"/api/datasets/{dataset_id}").json()["dataset"]
        assert len(detail["versions"]) == 2
        assert all(v["has_weights"] is False and v["training_status"] == "untrained" for v in detail["versions"])
        print(f"PASS: name, unique IDs, v1 → v2, retry, stale base, training-only splits, untrained state. Dataset: {dataset_id}")


if __name__ == "__main__":
    main()

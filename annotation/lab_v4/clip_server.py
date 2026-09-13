"""Private network-disabled CLIP worker; one model load, bounded JSONL jobs.

Only anonymous RGB pixels are mounted. No labels, truth, database or credentials.
This file also runs directly inside the pinned GPU image.
"""

import contextlib
import json
import re
import sys
from pathlib import Path


def job_paths(job, root):
    if not isinstance(job, dict) or set(job) != {"id", "count"}:
        raise ValueError("invalid CLIP job schema")
    sid, count = job["id"], job["count"]
    if not isinstance(sid, str) or not re.fullmatch("[a-f0-9]{32}", sid):
        raise ValueError("invalid CLIP job ID")
    if type(count) is not int or not 1 <= count <= 512:
        raise ValueError("invalid CLIP job size")
    root = Path(root).resolve()
    paths = [(root / sid / f"{i}.png").resolve() for i in range(count)]
    if any(not p.is_relative_to(root / sid) for p in paths):
        raise ValueError("CLIP job escaped its directory")
    return paths


class Encoder:
    def __init__(self):
        import torch
        from transformers import CLIPModel, CLIPProcessor

        torch.set_num_threads(4)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.processor = CLIPProcessor.from_pretrained(
            "/models/clip", local_files_only=True
        )
        self.model = (
            CLIPModel.from_pretrained("/models/clip", local_files_only=True)
            .to(self.device)
            .eval()
        )

    def encode(self, paths):
        import torch
        import torch.nn.functional as F
        from PIL import Image

        vectors = []
        # Match existing GPU worker math/precision/preprocessing, including batch 8.
        for start in range(0, len(paths), 8):
            images = []
            for path in paths[start : start + 8]:
                with Image.open(path) as source:
                    images.append(source.convert("RGB"))
            inputs = self.processor(images=images, return_tensors="pt")
            with torch.inference_mode():
                features = self.model.get_image_features(
                    pixel_values=inputs["pixel_values"].to(self.device)
                )
            if not isinstance(features, torch.Tensor):
                features = features.pooler_output
            vectors.extend(F.normalize(features.float(), dim=-1).cpu().tolist())
        return vectors


def serve(stream, output, root="/job", factory=Encoder):
    with contextlib.redirect_stdout(sys.stderr):
        encoder = factory()
    while True:
        line = stream.readline(4097)
        if not line:
            return
        if len(line) > 4096 or not line.endswith("\n"):
            raise ValueError("oversize or truncated CLIP job")
        job = json.loads(line)
        paths = job_paths(job, root)
        # A failed job terminates the worker; the coordinator may retry this
        # deterministic local operation, never a paid model request.
        with contextlib.redirect_stdout(sys.stderr):
            vectors = encoder.encode(paths)
        output.write(
            json.dumps(
                {"id": job["id"], "ok": True, "vectors": vectors}, allow_nan=False
            )
            + "\n"
        )
        output.flush()


if __name__ == "__main__":
    serve(sys.stdin, sys.stdout)

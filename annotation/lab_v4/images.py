import base64
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

Image.MAX_IMAGE_PIXELS = 25_000_000


def sha_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_image(path):
    path = Path(path)
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("image exceeds 20 MiB")
    with Image.open(path) as im:
        if (
            im.format not in ("JPEG", "PNG", "WEBP")
            or im.width * im.height > 25_000_000
        ):
            raise ValueError("unsupported or oversize image")
        return ImageOps.exif_transpose(im).convert("RGB")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(value, out, ensure_ascii=False, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_image(path, image):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".image-", suffix=".png", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            image.save(out, format="PNG")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def inline_image(path, max_side=768):
    im = read_image(path)
    im.thumbnail((max_side, max_side))
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def quality(path):
    im = read_image(path)
    w, h = im.size
    im.thumbnail((512, 512))
    a = np.asarray(im, dtype=np.float32).mean(axis=2) / 255
    median = (
        np.asarray(im.filter(ImageFilter.MedianFilter(3)), dtype=np.float32).mean(
            axis=2
        )
        / 255
    )
    lap = -4 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
    return {
        "width": w,
        "height": h,
        "mean_luminance": round(float(a.mean()), 4),
        "dark_fraction": round(float((a < 0.12).mean()), 4),
        "bright_fraction": round(float((a > 0.97).mean()), 4),
        "laplacian_variance": round(float(lap.var()), 6) if lap.size else 0,
        "noise_fraction": round(float((np.abs(a - median) > 0.08).mean()), 4),
        "warning": "heuristics only; background blur is not subject blur; never infer species from these scores",
    }

from __future__ import annotations

import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def create_toy_imagefolder(root: str | Path, samples_per_class: int = 18, seed: int = 7) -> Path:
    root_path = Path(root)
    rng = random.Random(seed)
    classes = {
        "red_square": (210, 54, 72),
        "green_circle": (54, 160, 96),
        "blue_triangle": (57, 105, 210),
    }
    for label, color in classes.items():
        class_dir = root_path / label
        class_dir.mkdir(parents=True, exist_ok=True)
        for idx in range(samples_per_class):
            image = Image.new("RGB", (96, 96), (238, 242, 247))
            draw = ImageDraw.Draw(image)
            jitter = rng.randint(-12, 12)
            fill = tuple(max(0, min(255, channel + jitter)) for channel in color)
            offset = rng.randint(-5, 5)
            if "square" in label:
                draw.rectangle((24 + offset, 24, 72 + offset, 72), fill=fill)
            elif "circle" in label:
                draw.ellipse((22, 22 + offset, 74, 74 + offset), fill=fill)
            else:
                draw.polygon([(48 + offset, 18), (20, 76), (76, 76)], fill=fill)
            noise = np.random.default_rng(seed + idx).normal(0, 3, (96, 96, 3))
            arr = np.clip(np.asarray(image, dtype=np.float32) + noise, 0, 255).astype(np.uint8)
            Image.fromarray(arr).save(class_dir / f"{label}_{idx:03d}.png")
    return root_path

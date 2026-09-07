#!/usr/bin/env python3
"""Regenerate the committed, synthetic FineVision example ImageFolder."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from finevision.ml_toolkit.toydata import create_toy_imagefolder


ROOT = Path("data/examples/toy-shapes-imagefolder")
SEED = 7
SAMPLES_PER_CLASS = 4


def main() -> None:
    create_toy_imagefolder(ROOT, samples_per_class=SAMPLES_PER_CLASS, seed=SEED)
    images = sorted(ROOT.glob("*/*.png"))
    lines = [
        "# Synthetic Toy Shapes Dataset",
        "",
        "This ImageFolder fixture is generated entirely in this repository. It contains no third-party photographs, people, logos, or trademarks.",
        "",
        f"- Generator: `scripts/generate_example_dataset.py`",
        f"- Seed: `{SEED}`",
        f"- Samples: `{len(images)}` (`3 classes × {SAMPLES_PER_CLASS}`)",
        "- Image format: `96×96 RGB PNG`",
        "- Command: `uv run python scripts/generate_example_dataset.py`",
        "",
        "## SHA-256",
        "",
        "```text",
    ]
    lines.extend(f"{sha256(path.read_bytes()).hexdigest()}  {path.relative_to(ROOT).as_posix()}" for path in images)
    lines.extend(["```", ""])
    (ROOT / "SOURCE.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()

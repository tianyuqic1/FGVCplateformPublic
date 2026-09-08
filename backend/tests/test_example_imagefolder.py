from pathlib import Path

from PIL import Image

from finevision.ml_toolkit.datasets import scan_imagefolder


def test_repository_example_imagefolder_is_ready_and_reproducible() -> None:
    root = Path("data/examples/toy-shapes-imagefolder")

    manifest = scan_imagefolder(root, "toy-shapes", "dataset@toy-shapes-001")

    assert manifest.classes == ["blue_triangle", "green_circle", "red_square"]
    assert len(manifest.samples) == 12
    assert manifest.readiness["ready"] is True
    assert (root / "SOURCE.md").is_file()
    for sample in manifest.samples:
        with Image.open(sample.path) as image:
            assert image.size == (96, 96)
            assert image.format == "PNG"

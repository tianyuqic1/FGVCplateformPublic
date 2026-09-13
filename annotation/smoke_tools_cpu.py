"""Explicit local model smoke checks; no remote calls and no memory writes."""
import argparse
import json
from pathlib import Path
import time

from lab_v4.images import atomic_json, read_image
from lab_v4.tools import LocalTools


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--models", type=Path, required=True)
    p.add_argument("--image", type=Path, required=True)
    p.add_argument("--sr-image", type=Path, required=True, help="native low-resolution PNG/JPEG, long edge <=224")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    tools = LocalTools(args.output.parent / "cpu-tools-cache", args.models)
    result = []
    try:
        for name in tools.available:
            start = time.monotonic()
            try:
                output = tools.enhance(name, args.sr_image if name == "sr_x2" else args.image)
                row = {"tool": name, "ok": True, "output_size": list(read_image(output).size)}
            except Exception as error:
                row = {"tool": name, "ok": False, "error": str(error)}
            row["seconds"] = round(time.monotonic() - start, 2)
            result.append(row)
            print(json.dumps(row), flush=True)
            atomic_json(args.output, result)
    finally:
        tools.close()
    if not all(r["ok"] for r in result):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

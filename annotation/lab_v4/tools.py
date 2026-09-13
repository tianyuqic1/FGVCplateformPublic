"""Whitelisted local tools; one restoration per prediction is enforced by Engine."""

import contextlib
import json
import math
import os
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .images import atomic_json, inline_image, quality, read_image, save_image, sha_file
from .memory import vector
from .state import digest

TOOLS = ("lowlight", "sr_x2", "denoise", "deblur_motion", "deblur_defocus")
RUNTIME = "sha256:64f60be51101760ca19573002b44d146b60035c7a37051befe3b083c6b4aa96a"


def parse_location(raw):
    from .engine import parse_json

    value = parse_json(raw)
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, dict):
        raise ValueError("ambiguous location")
    box = value.get("bbox", value.get("bbox_2d"))
    if (
        not isinstance(box, list)
        or len(box) != 4
        or any(
            type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1000
            for x in box
        )
    ):
        raise ValueError("invalid normalized location")
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("empty location")
    return box


class LocalTools:
    def __init__(
        self, root, models, bounds=None, locator=None, sam=None, clip_mode="oneshot"
    ):
        if clip_mode not in ("persistent", "oneshot"):
            raise ValueError("invalid CLIP execution mode")
        self.clip_mode = clip_mode
        self._clip = None
        self._gpu_lock = threading.RLock()
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.models = Path(models).resolve()
        self.bounds = bounds or {}
        self.locator = locator
        self.sam = Path(sam).resolve() if sam else None
        if locator:
            u = urllib.parse.urlparse(locator)
            if (
                u.scheme != "http"
                or u.hostname not in ("localhost", "127.0.0.1", "::1")
                or u.username
                or u.query
                or u.fragment
            ):
                raise ValueError("locator must be explicit local HTTP endpoint")
        self.manifest = json.loads((self.models / "manifest.json").read_text())
        self.sam_revision = None
        if self.sam:
            if not (self.sam / "model.safetensors").exists():
                raise ValueError("local SAM2 weights missing")
            self.sam_revision = digest(
                {
                    name: sha_file(self.sam / name)
                    for name in (
                        "config.json",
                        "preprocessor_config.json",
                        "model.safetensors",
                    )
                }
            )
        for rel, entry in self.manifest.items():
            p = (self.models / rel).resolve()
            if not p.is_relative_to(self.models) or sha_file(p) != entry["sha256"]:
                raise ValueError("model integrity failure")
        self.revision = digest(
            {"manifest": self.manifest, "runtime": RUNTIME, "tools_version": "platform-v1-cpu"}
        )
        self.available = [t for t in TOOLS if "weights/" + t + ".pth" in self.manifest]
        if "clip/pytorch_model.bin" not in self.manifest:
            raise ValueError("CLIP weights missing; finish explicit download first")

    def gpu(self, operation, path, **options):
        # Serialize creation, encoding, and memory release as one owner operation.
        # __new__-based legacy test doubles may not initialize this lock.
        with getattr(self, "_gpu_lock", contextlib.nullcontext()):
            return self._gpu(operation, path, **options)

    def _gpu(self, operation, path, **options):
        if (
            operation in ("clip", "clip_batch")
            and getattr(self, "clip_mode", "oneshot") == "persistent"
        ):
            if self._clip is None:
                from .clip_runtime import ClipRuntime

                self._clip = ClipRuntime(self.root, self.models, RUNTIME)
            paths = options.pop("_paths") if operation == "clip_batch" else [path]
            rows = self._clip.encode(paths)
            return {
                "ok": True,
                **({"vector": rows[0]} if operation == "clip" else {"vectors": rows}),
            }
        # Free persistent CLIP GPU memory before SAM/restoration in the same tool
        # owner. Frozen evaluation uses a separate encoder owner with explicit close.
        if operation not in ("clip", "clip_batch"):
            self.close()
        with tempfile.TemporaryDirectory(prefix="gpu-", dir=self.root) as work:
            work = Path(work)
            if operation == "clip_batch":
                paths = options.pop("_paths")
                if not 1 <= len(paths) <= 512:
                    raise ValueError("invalid embedding batch size")
                for index, p in enumerate(paths):
                    save_image(work / f"{index}.png", read_image(p))
                options["count"] = len(paths)
            else:
                save_image(work / "input.png", read_image(path))
            name = "annotation-tool-" + uuid.uuid4().hex[:12]
            command = [
                "docker",
                "run",
                "--rm",
                "-i",
                "--name",
                name,
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--cpus",
                "2",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "256",
                "--memory",
                "6g",
                "--tmpfs",
                "/tmp:rw,size=1g",
                "-e",
                "HF_HUB_OFFLINE=1",
                "-e",
                "TRANSFORMERS_OFFLINE=1",
                "-e",
                "HF_HOME=/tmp/hf",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
                "-v",
                str(self.models) + ":/models:ro",
                "-v",
                str(work) + ":/job",
                "-v",
                str(Path(__file__).resolve().parent) + ":/code:ro",
            ]
            if operation == "sam2":
                if not self.sam:
                    raise RuntimeError("SAM2 unavailable")
                if self.sam.parent.name == "snapshots":
                    command += ["-v", str(self.sam.parent.parent) + ":/sam-cache:ro"]
                    options["sam_path"] = "/sam-cache/snapshots/" + self.sam.name
                else:
                    command += ["-v", str(self.sam) + ":/sam:ro"]
            command += ["--entrypoint", "python3", RUNTIME, "/code/gpu_worker.py"]
            try:
                r = subprocess.run(
                    command,
                    input=json.dumps({"operation": operation, **options}),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                value = json.loads(r.stdout.strip().splitlines()[-1])
                if r.returncode or not value.get("ok"):
                    raise RuntimeError(
                        "local GPU worker failed: "
                        + value.get("error", "runtime unavailable")
                    )
                if operation in TOOLS:
                    dest = self.root / (
                        digest(
                            {
                                "image": sha_file(path),
                                "tool": operation,
                                "revision": self.revision,
                            }
                        )
                        + ".png"
                    )
                    save_image(dest, read_image(work / "output.png"))
                    value["path"] = str(dest)
                return value
            except (subprocess.TimeoutExpired, ValueError, IndexError) as error:
                raise RuntimeError("local worker timeout/invalid output") from error
            finally:
                # A killed docker CLI does not necessarily stop its GPU container.
                subprocess.run(
                    ["docker", "rm", "-f", name], capture_output=True, timeout=15
                )

    def cached(self, operation, path):
        key = digest(
            {"sha": sha_file(path), "operation": operation, "revision": self.revision}
        )
        meta = self.root / (key + ".json")
        try:
            value = json.loads(meta.read_text())
            if operation == "clip":
                vector(value["vector"], 512)
            if "path" not in value or sha_file(value["path"]) == value["output_sha"]:
                return value
        except (OSError, ValueError, KeyError, TypeError):
            pass
        last = None
        for attempt in range(2):
            try:
                value = self.gpu(operation, path)
                if "path" in value:
                    value["output_sha"] = sha_file(value["path"])
                atomic_json(meta, value)
                return value
            except RuntimeError as error:
                last = error
                if attempt == 0:
                    time.sleep(0.5)
        raise last

    def embed(self, path):
        return vector(self.cached("clip", path)["vector"], 512)

    def warm_embeddings(self, samples):
        """Batch unlabeled pixels only. No writes to Qdrant or confirmed ledger."""
        paths = []
        for sample in samples:
            sha = sha_file(sample["image"])
            original = self.root / (sha + "-original.png")
            if not original.exists():
                save_image(original, read_image(sample["image"]))
            paths.append(original)
        return self.warm_paths(paths)

    def warm_prepared_views(self, prepared, include_subject=True):
        """Preserve historical input batch shapes: originals 8, subjects 1.

        Subject batch encoding is not numerically interchangeable with single
        encoding in the pinned image processor/runtime. Reuse the process, not
        a changed batch shape, so the existing embedding cache remains valid.
        """
        from .policy import subject_eligible

        originals = [row["original"] for row in prepared]
        subjects = [
            row["subject"]
            for row in prepared
            if include_subject and subject_eligible(row)
        ]
        original = self.warm_paths(originals)
        subject = self.warm_paths(subjects, batch_size=1)
        return {
            "view_count": len(originals) + len(subjects),
            "unique_misses": original["unique_misses"] + subject["unique_misses"],
            "batch_jobs": original["batch_jobs"] + subject["batch_jobs"],
            "original": original,
            "subject": subject,
        }

    def warm_paths(self, paths, batch_size=512):
        """Batch already-prepared unlabeled views; same per-image cache keys."""
        if type(batch_size) is not int or not 1 <= batch_size <= 512:
            raise ValueError("invalid warmup batch size")
        pending = {}
        for original in paths:
            key = digest(
                {
                    "sha": sha_file(original),
                    "operation": "clip",
                    "revision": self.revision,
                }
            )
            meta = self.root / (key + ".json")
            try:
                vector(json.loads(meta.read_text())["vector"], 512)
                continue
            except (OSError, ValueError, KeyError, TypeError):
                pending[key] = (original, meta)
        items = list(pending.values())
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            for attempt in range(2):
                try:
                    rows = self.gpu(
                        "clip_batch", None, _paths=[str(p) for p, _ in batch]
                    )["vectors"]
                    if len(rows) != len(batch):
                        raise RuntimeError("embedding batch size mismatch")
                    rows = [vector(v, 512) for v in rows]
                    for (_, meta), v in zip(batch, rows):
                        atomic_json(meta, {"ok": True, "vector": v})
                    break
                except RuntimeError:
                    if attempt:
                        raise
                    time.sleep(0.5)
        return {
            "unique_misses": len(items),
            "batch_jobs": (len(items) + batch_size - 1) // batch_size,
        }

    def close(self):
        with getattr(self, "_gpu_lock", contextlib.nullcontext()):
            clip = getattr(self, "_clip", None)
            if clip is not None:
                try:
                    clip.close()
                finally:
                    self._clip = None

    def enhance(self, tool, path):
        if tool not in self.available:
            raise ValueError("tool not installed")
        im = read_image(path)
        if tool == "sr_x2" and max(im.size) > 224:
            # Never downsample a high-resolution input merely to upscale it.
            # SwinIR is reserved for genuinely low-resolution views on CPU.
            raise ValueError("CPU 超分只接受原生长边不超过 224 的视图")
        # Bounded compute/memory. Retain the untouched original separately.
        if max(im.size) > 512:
            im.thumbnail((512, 512))
            small = self.root / (sha_file(path) + "-tool-input.png")
            save_image(small, im)
            path = small
        return self.cached(tool, path)["path"]

    def locate(self, path):

        payload = {
            "model": "local-qwen-4b",
            "temperature": 0,
            "max_tokens": 128,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": 'Locate the main subject including all visible body parts. Do not classify it. Return ONLY JSON {"bbox":[x0,y0,x1,y1]} with coordinates normalized 0..1000, or {"bbox":null} if uncertain.',
                        },
                        {"type": "image_url", "image_url": {"url": inline_image(path)}},
                    ],
                }
            ],
        }
        request = urllib.request.Request(
            self.locator.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        # No remote secrets. Local transient failures may safely be retried once.
        for attempt in range(2):
            try:

                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, *args):
                        return None

                with urllib.request.build_opener(NoRedirect).open(
                    request, timeout=120
                ) as response:
                    raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError("oversize local output")
                choices = json.loads(raw)["choices"]
                choice = choices[0]
                if choice.get("finish_reason") != "stop":
                    raise ValueError("incomplete location")
                b = parse_location(choice["message"]["content"])
                if (
                    not isinstance(b, list)
                    or len(b) != 4
                    or any(
                        type(x) not in (float, int)
                        or not math.isfinite(x)
                        or not 0 <= x <= 1000
                        for x in b
                    )
                ):
                    raise ValueError("invalid box")
                if b[2] <= b[0] or b[3] <= b[1]:
                    raise ValueError("empty box")
                im = read_image(path)
                return [
                    b[0] * im.width / 1000,
                    b[1] * im.height / 1000,
                    b[2] * im.width / 1000,
                    b[3] * im.height / 1000,
                ]
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                if attempt:
                    raise RuntimeError("local locator unavailable")

    def prepare(self, sample, sha):
        im = read_image(sample["image"])
        original = self.root / (sha + "-original.png")
        save_image(original, im)
        subject = None
        bounds = None
        route = "original_only"
        warnings = []
        if sample["id"] in self.bounds:
            record = self.bounds[sample["id"]]
            if record["sha"] != sha:
                raise ValueError("reused localization image mismatch")
            bounds = record["bounds"]
            route = record.get(
                "route", "verified_cached_qwen_sam" if bounds else "original_only"
            )
            warnings.extend(record.get("warnings", []))
        elif self.locator:
            try:
                bounds = self.locate(original)
                route = "qwen_box"
                if self.sam:
                    try:
                        mask = self.gpu("sam2", original, box=bounds)["bounds"]
                        bounds = [
                            min(bounds[0], mask[0]),
                            min(bounds[1], mask[1]),
                            max(bounds[2], mask[2]),
                            max(bounds[3], mask[3]),
                        ]
                        route = "qwen_sam_union"
                    except RuntimeError:
                        warnings.append("sam_fallback_box")
                pad = max(4, 0.1 * max(bounds[2] - bounds[0], bounds[3] - bounds[1]))
                bounds = [
                    bounds[0] - pad,
                    bounds[1] - pad,
                    bounds[2] + pad,
                    bounds[3] + pad,
                ]
            except RuntimeError:
                warnings.append("locator_fallback_original")
        if bounds is not None:
            if len(bounds) != 4 or any(
                type(x) not in (int, float) or not math.isfinite(x) for x in bounds
            ):
                raise ValueError("invalid cached bounds")
            bounds = [
                max(0, math.floor(bounds[0])),
                max(0, math.floor(bounds[1])),
                min(im.width, math.ceil(bounds[2])),
                min(im.height, math.ceil(bounds[3])),
            ]
            if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                raise ValueError("empty crop")
            subject = self.root / (sha + "-subject.png")
            save_image(subject, im.crop(bounds))
        return {
            "original": str(original),
            "original_sha": sha_file(original),
            "subject": str(subject) if subject else None,
            "subject_sha": sha_file(subject) if subject else None,
            "route": route,
            "bounds": bounds,
            "warnings": warnings,
            "quality": {
                "original": quality(original),
                "subject": quality(subject) if subject else None,
            },
        }

"""V4.4 pixel-only policy. Thresholds are heuristics, not calibrated confidence."""

import math

from .images import quality, read_image, sha_file

REVISION = "balanced_v44"


def subject_eligible(prepared):
    """Reject tiny, near-full-frame and unverifiable auxiliary crops.

    This checks geometry/provenance, NOT whether the locator found the right object.
    Always keep the original, even for accepted crops.
    """
    path = prepared.get("subject")
    if not path or not prepared.get("subject_sha"):
        return False
    if prepared.get("route") not in (
        "qwen_box",
        "qwen_sam_union",
        "verified_cached_qwen_sam",
    ):
        return False
    if sha_file(path) != prepared["subject_sha"]:
        raise ValueError("subject integrity failure")
    original, subject = read_image(prepared["original"]), read_image(path)
    bounds = prepared.get("bounds")
    if bounds is not None:
        if (
            len(bounds) != 4
            or any(type(n) is not int for n in bounds)
            or not 0 <= bounds[0] < bounds[2] <= original.width
            or not 0 <= bounds[1] < bounds[3] <= original.height
        ):
            raise ValueError("invalid subject provenance bounds")
        crop = original.crop(bounds)
        if crop.size != subject.size or crop.tobytes() != subject.tobytes():
            raise ValueError("subject is not a crop of original pixels")
    ratio = subject.width * subject.height / (original.width * original.height)
    return (
        min(subject.size) >= 64
        and subject.width <= original.width
        and subject.height <= original.height
        and 0.08 <= ratio <= 0.9
    )


def quality_decision(prepared, available):
    """None means ask VLM; dict means a deterministic, auditable shortcut."""
    target = "subject" if prepared.get("subject") else "original"
    q = prepared.get("quality", {}).get(target) or {}
    keys = (
        "width",
        "height",
        "mean_luminance",
        "dark_fraction",
        "bright_fraction",
        "laplacian_variance",
    )
    if any(type(q.get(k)) not in (int, float) or not math.isfinite(q[k]) for k in keys):
        return None
    if (
        q["mean_luminance"] < 0.12
        and q["dark_fraction"] > 0.65
        and "lowlight" in available
    ):
        return {"tool": "lowlight", "target": target, "reason": "severe_underexposure"}
    # High-frequency noise can look 'sharp' to a Laplacian score. Never use
    # sharpness alone to bypass restoration selection. Old caches lack this field.
    noise = q.get("noise_fraction")
    if noise is None:
        noise = quality(prepared[target])["noise_fraction"]
    if type(noise) not in (int, float) or not math.isfinite(noise):
        return None
    if (
        min(q["width"], q["height"]) >= 224
        and 0.18 <= q["mean_luminance"] <= 0.85
        and q["dark_fraction"] < 0.4
        and q["bright_fraction"] < 0.2
        and q["laplacian_variance"] >= 0.003
        and noise < 0.05
    ):
        return {"tool": "none", "target": target, "reason": "quality_fast_path"}
    return None


def fuse_references(rankings, limit=6):
    """Class-level reciprocal-rank fusion; one source/image per distinct class.

    Repeated views or neighbors of one class cannot multiply its vote within a
    query view. Cosine is retained for diagnostics, not interpreted as probability.
    """
    classes = {}
    for view, rows in rankings.items():
        seen = set()
        for row in rows:
            label = row["label"]
            if label in seen:
                continue
            seen.add(label)
            rank = len(seen)
            item = classes.setdefault(label, {"score": 0.0, "best": row, "views": []})
            item["score"] += 1 / (60 + rank)
            item["views"].append(view)
            if row["similarity"] > item["best"]["similarity"]:
                item["best"] = row
    ordered = sorted(
        classes.items(),
        key=lambda item: (-item[1]["score"], -item[1]["best"]["similarity"], item[0]),
    )
    return [
        {**item["best"], "fusion_score": item["score"], "query_views": item["views"]}
        for _, item in ordered[:limit]
    ]

"""Experimental, opt-in CLIP text candidates; never reads query truth."""

import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import numpy as np

from .clip_runtime import ClipRuntime
from .images import atomic_json, sha_file
from .memory import vector
from .state import digest
from .tools import RUNTIME, LocalTools

REVISION = "clip_text_rrf_v1"
TEMPLATES = (
    "a photo of a {name}.",
    "a close-up photo of a {name}.",
    "a photo of the bird {name}.",
)
POLICY = {
    "revision": REVISION,
    "templates": list(TEMPLATES),
    "text_template_pool": "normalize_mean_of_unit_embeddings",
    "query_view_pool": "mean_cosine",
    "branch_top_k": 10,
    "rrf_k": 60,
    "image_weight": 1,
    "text_weight": 1,
    "tie_break": "image_rank_then_text_rank_then_class_id",
    "reference_limit": 6,
    "default_enabled": False,
}
DOMAIN_TEMPLATES = {
    "cub": TEMPLATES,
    "cars": (
        "a photo of a {name}.",
        "a close-up photo of a {name}.",
        "a photo of the car {name}.",
    ),
    "imagenet100": (
        "a photo of a {name}.",
        "a close-up photo of a {name}.",
        "an image of a {name}.",
    ),
}


def policy_for(domain):
    if domain not in DOMAIN_TEMPLATES:
        raise ValueError("unsupported text prototype domain")
    templates = DOMAIN_TEMPLATES[domain]
    return {
        **POLICY,
        "revision": REVISION + "-" + domain,
        "templates": list(templates),
        "domain": domain,
    }


def class_prompts(classes, policy=POLICY):
    templates = policy.get("templates") if isinstance(policy, dict) else None
    if (
        not isinstance(templates, list)
        and not isinstance(templates, tuple)
        or len(templates) != 3
        or any(
            not isinstance(template, str)
            or template.count("{name}") != 1
            or len(template) > 128
            for template in templates
        )
    ):
        raise ValueError("invalid text templates")
    if not isinstance(classes, list) or not classes:
        raise ValueError("empty class catalog")
    ids = []
    prompts = []
    for row in classes:
        if not isinstance(row, dict):
            raise ValueError("invalid class")
        sid, name = row.get("id"), row.get("name")
        if (
            not isinstance(sid, str)
            or not sid
            or not isinstance(name, str)
            or not name.strip()
        ):
            raise ValueError("invalid class identity")
        if sid in ids or len(name) > 180:
            raise ValueError("duplicate or oversize class")
        ids.append(sid)
        prompts.extend(t.format(name=" ".join(name.lower().split())) for t in templates)
    return prompts


def load_prototypes(path, classes, revision, expected_policy=None):
    value = json.loads(Path(path).read_text())
    policy = value.get("policy")
    allowed = [POLICY, *(policy_for(domain) for domain in DOMAIN_TEMPLATES)]
    if (
        value.get("embedding_revision") != revision
        or value.get("classes_sha") != digest(classes)
        or policy not in allowed
        or (expected_policy is not None and policy != expected_policy)
    ):
        raise ValueError("text prototypes/model/catalog/policy mismatch")
    rows = value["classes"]
    if [(r["id"], r["name"]) for r in rows] != [(r["id"], r["name"]) for r in classes]:
        raise ValueError("prototype class order mismatch")
    for row in rows:
        vector(row["vector"], 512)
    if value.get("vectors_sha") != digest(rows):
        raise ValueError("text prototypes changed")
    return value


def build_prototypes(classes, models, output, policy=POLICY):
    """One local invocation; no downloads, automatic retries or Qdrant writes."""
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("prototype output already exists")
    prompts = class_prompts(classes, policy)
    tools = LocalTools(output.parent / "model-audit", models)
    runtime = ClipRuntime(output.parent, models.resolve(), RUNTIME)
    name = "annotation-text-" + uuid.uuid4().hex[:12]
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="text-job-", dir=output.parent) as work:
            command = runtime._command(work, name)
            command[-1] = "/code/clip_text_worker.py"
            process = subprocess.run(
                command,
                input=json.dumps({"texts": prompts}),
                text=True,
                capture_output=True,
                timeout=120,
            )
            if process.returncode:
                raise RuntimeError("CLIP text export failed: " + process.stderr[-2000:])
            encoded = json.loads(process.stdout)["vectors"]
            if len(encoded) != len(prompts):
                raise ValueError("text result count mismatch")
            encoded = np.asarray(
                [vector(row, 512) for row in encoded], dtype=np.float64
            )
            rows = []
            for i, row in enumerate(classes):
                mean = encoded[
                    i * len(policy["templates"]) : (i + 1) * len(policy["templates"])
                ].mean(axis=0)
                rows.append(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "vector": vector(mean.tolist(), 512),
                    }
                )
            value = {
                "embedding_revision": tools.revision,
                "classes_sha": digest(classes),
                "policy": policy,
                "classes": rows,
                "vectors_sha": digest(rows),
                "prompt_count": len(prompts),
                "prompts_sha": digest(prompts),
                "worker_sha": sha_file(Path(__file__).with_name("clip_text_worker.py")),
                "seconds": time.monotonic() - started,
            }
            atomic_json(output, value)
            return value
    finally:
        ClipRuntime._remove_container(name)
        runtime.close()
        tools.close()


def text_ranking(queries, prototypes):
    if not queries:
        raise ValueError("empty query views")
    rows = prototypes["classes"]
    text = np.asarray([vector(r["vector"], 512) for r in rows], dtype=np.float64)
    images = np.asarray([vector(v, 512) for v in queries.values()], dtype=np.float64)
    scores = (images @ text.T).mean(axis=0)
    return [
        {"label": rows[i]["id"], "similarity": float(scores[i])}
        for i in sorted(range(len(rows)), key=lambda i: (-scores[i], rows[i]["id"]))
    ]


def fuse_labels(image_labels, text_labels):
    """Equal RRF on top ten per branch. No similarity/probability mixing."""

    def ranks(labels):
        unique = list(dict.fromkeys(labels))[: POLICY["branch_top_k"]]
        return {label: i + 1 for i, label in enumerate(unique)}

    image, text = ranks(image_labels), ranks(text_labels)
    score = {
        label: (1 / (60 + image[label]) if label in image else 0)
        + (1 / (60 + text[label]) if label in text else 0)
        for label in image.keys() | text.keys()
    }
    return sorted(
        score,
        key=lambda label: (
            -score[label],
            image.get(label, 10**9),
            text.get(label, 10**9),
            label,
        ),
    )

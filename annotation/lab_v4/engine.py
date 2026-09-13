"""Sequential predict -> lock -> human confirmation -> transactional outbox.

This module has no truth-file reader. An injected annotation callback is invoked
only after SQLite has durably locked the prediction. The model sees anonymous
pixels, not class-bearing source paths.
"""

import json
import math
import random
import time

from .images import atomic_json, inline_image, quality, read_image, sha_file
from .memory import flush_outbox
from .policy import REVISION, quality_decision, subject_eligible
from .state import UnknownOutcome


class ConfigurationBlocked(RuntimeError):
    pass


class RetryDeferred(RuntimeError):
    def __init__(self, message, until=0):
        super().__init__(message)
        self.until = until


def parse_json(raw):
    raw = raw.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


def candidates(response, allowed, retained=()):
    if response.get("kind") != "ok":
        return []
    try:
        value = parse_json(response.get("raw", "")).get("class_ids", [])
        if not isinstance(value, list):
            return []
    except (ValueError, AttributeError, IndexError):
        return []
    seen = set(retained)
    result = []
    for cid in value:
        if isinstance(cid, str) and cid in allowed and cid not in seen:
            result.append(cid)
            seen.add(cid)
    return result


class Engine:
    def __init__(
        self,
        ledger,
        memory,
        tools,
        provider,
        classes,
        annotator,
        sleep=time.sleep,
        queue_wait_seconds=60,
        sample_remote_seconds=240,
        retrieval_mode="online",
        reference_lookup=None,
        workflow_policy="legacy",
    ):
        self.ledger = ledger
        self.memory = memory
        self.tools = tools
        self.provider = provider
        self.classes = classes
        self.allowed = {c["id"] for c in classes}
        self.annotator = annotator
        self.sleep = sleep
        self.queue_wait_seconds = queue_wait_seconds
        self.sample_remote_seconds = sample_remote_seconds
        if retrieval_mode not in ("online", "off"):
            raise ValueError("invalid retrieval mode")
        self.retrieval_mode = retrieval_mode
        if reference_lookup is not None and retrieval_mode != "off":
            raise ValueError("frozen reference lookup requires static evaluation")
        self.reference_lookup = reference_lookup
        if workflow_policy not in ("legacy", REVISION):
            raise ValueError("invalid workflow policy")
        self.optimized = workflow_policy == REVISION
        if retrieval_mode == "off" and annotator is not None:
            raise ValueError("static evaluation must not have an annotation callback")
        if (
            not math.isfinite(queue_wait_seconds)
            or queue_wait_seconds < 0
            or not math.isfinite(sample_remote_seconds)
            or sample_remote_seconds <= 0
        ):
            raise ValueError("invalid queue/sample budget")
        if len(self.allowed) != len(classes) or len(classes) < 2:
            raise ValueError("need at least two unique classes")
        self.top_k = min(10, len(classes))

    def call(self, sid, name, request):
        name = "model:" + name
        while True:
            response = self.ledger.begin(sid, name, request)
            if response is None:
                remaining = self.sample_remote_seconds - self.ledger.remote_seconds(sid)
                start = time.monotonic()
                if remaining <= 0:
                    response = {
                        "kind": "failed",
                        "error": "sample remote time budget exhausted",
                        "dispatched": False,
                    }
                elif hasattr(self.provider, "with_budget"):
                    response = self.provider.with_budget(request, remaining)
                else:
                    response = self.provider(request)
                response = {
                    **response,
                    "latency_ms": max(
                        response.get("latency_ms", 0),
                        int((time.monotonic() - start) * 1000),
                    ),
                }
                if response.get("kind") == "retryable":
                    attempts = self.ledger.stage(sid, name)["attempts"]
                    response = {
                        **response,
                        "retry_after": max(
                            float(response.get("retry_after", 0)),
                            random.uniform(1, min(30, 2**attempts)),
                        ),
                    }
                self.ledger.complete(sid, name, response)
            kind = response.get("kind")
            if kind == "unknown":
                raise UnknownOutcome(f"{sid}/{name}: provider outcome unknown")
            if kind == "fatal":
                raise ConfigurationBlocked(
                    response.get("error", "provider configuration blocked")
                )
            if kind == "wait":
                raise RetryDeferred(f"{sid}/{name}: delayed retry", response["until"])
            if kind in ("retryable", "deferred"):
                continue
            return response

    def predict(self, sample, seq):
        sid = sample["id"]
        sha = sha_file(sample["image"])
        self.ledger.register(sid, seq, sha, str(sample["image"]))
        old = self.ledger.prediction(sid)
        if old is not None:
            return old

        def prepare():
            read_image(sample["image"])  # corrupt original is never silently accepted
            try:
                return self.tools.prepare(sample, sha)
            except (RuntimeError, OSError):
                return {
                    "original": sample["image"],
                    "original_sha": sha,
                    "subject": None,
                    "quality": {},
                    "warnings": ["preparation_fallback_original"],
                }

        prepared = self.ledger.local(sid, "local:prepare", {"sha": sha}, prepare)
        for view in ("original", "subject"):
            if (
                prepared.get(view)
                and prepared.get(view + "_sha")
                and sha_file(prepared[view]) != prepared[view + "_sha"]
            ):
                raise ConfigurationBlocked("prepared image integrity failure")

        if (
            self.optimized
            and prepared.get("subject")
            and not subject_eligible(prepared)
        ):
            prepared = {
                **prepared,
                "subject": None,
                "warnings": [
                    *prepared.get("warnings", []),
                    "subject_geometry_rejected",
                ],
            }

        # Canonical CLIP vector always uses original pixels, never restoration.
        def embed():
            try:
                return self.tools.embed(prepared["original"])
            except (RuntimeError, OSError):
                return None

        embedding = (
            self.ledger.local(sid, "local:embedding", {"sha": sha}, embed)
            if self.retrieval_mode == "online"
            else None
        )
        queries = {"original": embedding} if embedding is not None else {}
        if (
            self.optimized
            and self.retrieval_mode == "online"
            and prepared.get("subject")
        ):

            def embed_subject():
                try:
                    return self.tools.embed(prepared["subject"])
                except (RuntimeError, OSError):
                    return None

            subject_vector = self.ledger.local(
                sid,
                "local:embedding:subject",
                {"sha": prepared["subject_sha"]},
                embed_subject,
            )
            if subject_vector is not None:
                queries["subject"] = subject_vector

        def retrieve():
            if self.reference_lookup is not None:
                if self.optimized:
                    if not hasattr(self.reference_lookup, "prepared_lookup"):
                        raise ConfigurationBlocked(
                            "optimized frozen lookup must support prepared views"
                        )
                    return self.reference_lookup.prepared_lookup(
                        sid, prepared, seq, sha
                    )
                return self.reference_lookup(sid, prepared["original"], seq, sha)
            if self.retrieval_mode == "off":
                return {"confirmed_before": 0, "enabled": False, "references": []}
            count = self.ledger.ready_count(seq)
            eligible = self.ledger.ready_classes(seq) if self.optimized else {}
            enabled = (
                bool(eligible) and bool(queries)
                if self.optimized
                else count > 100 and embedding is not None
            )
            warning = "embedding_unavailable" if embedding is None else None
            try:
                refs = (
                    (
                        self.memory.search_views(
                            queries,
                            seq,
                            sha,
                            eligible,
                            blocked_shas=[s for s, _ in self.ledger.pending_points()],
                        )
                        if self.optimized
                        else self.memory.search(embedding, seq, sha)
                    )
                    if enabled
                    else []
                )
            except (RuntimeError, OSError):
                refs, enabled, warning = [], False, "retrieval_unavailable"
            return {
                "confirmed_before": count,
                "enabled": enabled,
                "references": refs,
                "warning": warning,
                "eligible_classes": eligible,
            }

        snapshot = self.ledger.local(
            sid, "local:retrieval", {"seq": seq, "sha": sha}, retrieve
        )
        original = inline_image(prepared["original"])
        images = [original]
        if prepared.get("subject"):
            images.append(inline_image(prepared["subject"]))
        tool_result = {"tool": "none", "status": "not_requested"}
        if self.tools.available:
            selection_request = {
                "operation": "select_tool",
                "images": images,
                "classes": [],
                "quality": prepared["quality"],
                "tools": ["none", *self.tools.available],
            }
            if self.optimized:
                selection_request["evidence_policy"] = REVISION

            def select_optional():
                if self.optimized:
                    decision = quality_decision(prepared, self.tools.available)
                    if decision is not None:
                        return {
                            "kind": "ok",
                            "raw": json.dumps(decision),
                            "selection_source": decision["reason"],
                        }
                try:
                    return self.call(sid, "select", selection_request)
                except (UnknownOutcome, RetryDeferred):
                    # Freeze the fallback decision, never silently retry a paid
                    # uncertain selector when this sample resumes classification.
                    return {"kind": "failed", "error": "optional_selection_unavailable"}

            selection = self.ledger.local(
                sid, "local:selection", selection_request, select_optional
            )
            tool_result["selection_source"] = selection.get("selection_source", "vlm")
            if selection.get("kind") != "ok":
                tool_result = {
                    "tool": "none",
                    "status": "fallback",
                    "error": "optional_selection_unavailable",
                }
            try:
                decision = (
                    parse_json(selection.get("raw", ""))
                    if selection.get("kind") == "ok"
                    else {}
                )
            except (ValueError, IndexError):
                decision = {}
            if not isinstance(decision, dict):
                decision = {}
            tool = decision.get("tool", "none")
            target = decision.get("target", "subject")
            if (
                tool in self.tools.available
                and target in ("original", "subject")
                and prepared.get(target)
            ):

                def enhance():
                    try:
                        path = self.tools.enhance(tool, prepared[target])
                        if self.optimized and tool == "lowlight":
                            before, after = quality(prepared[target]), quality(path)
                            if (
                                after["mean_luminance"] <= before["mean_luminance"]
                                or after["mean_luminance"] > 0.9
                                or after["bright_fraction"] > 0.35
                            ):
                                raise ValueError("lowlight output quality rejected")
                        return {
                            "tool": tool,
                            "status": "ok",
                            "path": path,
                            "output_sha": sha_file(path),
                            "selection_source": selection.get(
                                "selection_source", "vlm"
                            ),
                        }
                    except (RuntimeError, ValueError, OSError):
                        return {
                            "tool": tool,
                            "status": "fallback",
                            "error": "local restoration unavailable or rejected",
                        }

                tool_result = self.ledger.local(
                    sid, "local:enhancement", {"tool": tool, "target": target}, enhance
                )
                if tool_result["status"] == "ok":
                    if (
                        tool_result.get("output_sha")
                        and sha_file(tool_result["path"]) != tool_result["output_sha"]
                    ):
                        raise ConfigurationBlocked("enhancement image changed")
                    images.append(inline_image(tool_result["path"]))
            elif tool != "none":
                tool_result = {"tool": "none", "status": "invalid_selection"}
        # V4.4 uses one reference per class and the actual 8-image transport budget.
        refs = []
        reference_limit = min(6, 8 - len(images)) if self.optimized else 4
        for r in snapshot["references"][:reference_limit]:
            if r["label"] not in self.allowed:
                raise ConfigurationBlocked("reference label outside current catalog")
            if sha_file(r["image"]) != r["image_sha"]:
                raise ConfigurationBlocked("reference image changed")
            refs.append(
                {
                    "label": r["label"],
                    "similarity": r["similarity"],
                    "image_index": len(images),
                }
            )
            images.append(inline_image(r["image"]))
        base = {"images": images, "classes": self.classes, "references": refs}
        if self.optimized:
            base["evidence_policy"] = REVISION
        first = self.call(sid, "classify", {"operation": "classify", **base})
        ids = candidates(first, self.allowed)[:10]
        if len(ids) < self.top_k:
            extra = self.call(
                sid,
                "supplement",
                {
                    "operation": "supplement",
                    **base,
                    "classes": [c for c in self.classes if c["id"] not in ids],
                    "retained": ids,
                },
            )
            ids = (ids + candidates(extra, self.allowed, ids))[:10]
        prediction = {
            "status": "ok" if len(ids) == self.top_k else "failed",
            "class_ids": ids,
            "sha": sha,
            "retrieval_enabled": snapshot["enabled"],
            "confirmed_before": snapshot["confirmed_before"],
            "reference_labels": [r["label"] for r in refs],
            "reference_count": len(refs),
            "tool": tool_result,
            "warnings": [
                *prepared.get("warnings", []),
                *([snapshot["warning"]] if snapshot.get("warning") else []),
            ],
        }
        self.ledger.freeze(sid, prediction)
        return prediction

    def run(self, samples):
        results = {}
        next_flush = 0.0

        def flush_optional():
            nonlocal next_flush
            if self.retrieval_mode == "off":
                return
            if time.time() < next_flush:
                return
            try:
                flush_outbox(self.ledger, self.memory, attempts=1)
            except RuntimeError:
                # Outbox stays durable and is retried on subsequent samples/run.
                next_flush = time.time() + 30
                with self.ledger.db:
                    self.ledger.event(None, "outbox", "deferred", {"retry_after": 30})

        def process(seq, sample):
            sid = sample["id"]
            started = time.monotonic()
            old = self.ledger.task(sid)
            if old and old["status"] in ("ok", "failed"):
                if self.ledger.db.execute(
                    "SELECT 1 FROM samples WHERE id=?", (sid,)
                ).fetchone():
                    self.ledger.register(
                        sid, seq, sha_file(sample["image"]), str(sample["image"])
                    )
                results[sid] = json.loads(old["result"])
                return
            try:
                prediction = self.predict(sample, seq)
                if self.retrieval_mode == "off":
                    result = {
                        "id": sid,
                        **prediction,
                        "attempt_seconds": time.monotonic() - started,
                    }
                    self.ledger.save_task(sid, result)
                    results[sid] = result
                    return
                # Callback sees locked prediction; no truth enters model steps.
                label = self.annotator(sample["id"], prediction)
                if label not in self.allowed:
                    raise ValueError("human label outside catalog")
                prepared = self.ledger.stage(sample["id"], "local:prepare")
                data = json.loads(prepared["response"])["value"]
                if (
                    data.get("original_sha")
                    and sha_file(data["original"]) != data["original_sha"]
                ):
                    raise ConfigurationBlocked(
                        "canonical image changed before confirmation"
                    )
                embedding = json.loads(
                    self.ledger.stage(sample["id"], "local:embedding")["response"]
                )["value"]
                point = {
                    "vector": embedding,
                    "payload": {
                        **self.memory.scope,
                        "sha": prediction["sha"],
                        "sample": sample["id"],
                        "seq": seq,
                        "label": label,
                        "image": data["original"],
                        "image_sha": sha_file(data["original"]),
                    },
                }
                if self.optimized:
                    point["payload"]["view"] = "original"
                    stage = self.ledger.stage(sample["id"], "local:embedding:subject")
                    if stage and subject_eligible(data):
                        subject_vector = json.loads(stage["response"])["value"]
                        if subject_vector is not None:
                            point["views"] = [
                                {
                                    "vector": subject_vector,
                                    "payload": {
                                        **point["payload"],
                                        "view": "subject",
                                        "view_image": data["subject"],
                                        "view_sha": data["subject_sha"],
                                        "bounds": data.get("bounds"),
                                        "route": data.get("route"),
                                        "view_policy": REVISION,
                                    },
                                }
                            ]
                if embedding is not None:
                    self.ledger.confirm(sample["id"], label, point)
                    flush_optional()
                result = {
                    "id": sample["id"],
                    **prediction,
                    "label": label,
                    "top10_hit": label in prediction["class_ids"],
                }
                self.ledger.save_task(sid, result)
            except UnknownOutcome:
                result = {
                    "id": sid,
                    "status": "unknown",
                    "class_ids": [],
                    "error": "provider outcome requires explicit resolution",
                }
                self.ledger.save_task(sid, result)
            except RetryDeferred as error:
                result = {
                    "id": sid,
                    "status": "deferred",
                    "class_ids": [],
                    "next_at": error.until,
                }
                self.ledger.save_task(sid, result, error.until)
            except ConfigurationBlocked:
                result = {
                    "id": sid,
                    "status": "blocked",
                    "class_ids": [],
                    "error": "provider configuration or integrity blocked",
                }
                self.ledger.save_task(sid, result)
                results[sid] = result
                raise
            except OSError:
                result = {
                    "id": sid,
                    "status": "failed",
                    "class_ids": [],
                    "error": "local input or I/O failure",
                }
                self.ledger.save_task(sid, result)
            results[sid] = result

        def checkpoint():
            atomic_json(
                self.ledger.root / "results.json",
                [
                    results.get(
                        s["id"],
                        {"id": s["id"], "status": "not_attempted", "class_ids": []},
                    )
                    for s in samples
                ],
            )

        with self.ledger.exclusive():
            flush_optional()
            if self.retrieval_mode == "online" and hasattr(
                self.tools, "warm_embeddings"
            ):
                try:
                    self.tools.warm_embeddings(samples)
                except (RuntimeError, OSError):
                    pass  # Individual embeddings can still succeed or degrade.
            try:
                # Fixed-order first pass; no unknown outcome blocks the next image.
                for seq, sample in enumerate(samples):
                    process(seq, sample)
                    checkpoint()
                    if self.retrieval_mode == "off":
                        print(
                            json.dumps(
                                {
                                    "progress": seq + 1,
                                    "total": len(samples),
                                    "id": sample["id"],
                                    "status": results[sample["id"]]["status"],
                                }
                            ),
                            flush=True,
                        )
                waited = 0.0
                while True:
                    pending = [
                        (seq, s, self.ledger.task(s["id"]))
                        for seq, s in enumerate(samples)
                        if results[s["id"]]["status"] == "deferred"
                    ]
                    if not pending:
                        break
                    seq, sample, task = min(pending, key=lambda row: row[2]["next_at"])
                    delay = max(0.01, task["next_at"] - time.time())
                    if waited + delay > self.queue_wait_seconds:
                        break
                    # Bounded sleeps leave the process responsive to interruption.
                    self.sleep(min(delay, 30))
                    waited += min(delay, 30)
                    if task["next_at"] <= time.time():
                        process(seq, sample)
                        checkpoint()
            finally:
                checkpoint()
        return [
            results.get(
                s["id"], {"id": s["id"], "status": "not_attempted", "class_ids": []}
            )
            for s in samples
        ]

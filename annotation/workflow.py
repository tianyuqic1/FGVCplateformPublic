"""Production adapter: real human confirmation, never an oracle/truth reader.

The evaluated v4.4 engine is vendored alongside this file. Its sample prediction
ledger remains immutable. Platform confirmations are synced through a separate
durable PostgreSQL outbox and made available only after Qdrant acknowledges.
"""
import json
from pathlib import Path

from lab_v4.engine import Engine
from lab_v4.images import atomic_json, quality, read_image, save_image, sha_file
from lab_v4.memory import Memory, flush_outbox, vector
from lab_v4.policy import subject_eligible
from lab_v4.state import Ledger, digest
from lab_v4.text_candidates import class_prompts, fuse_labels, policy_for, text_ranking
from lab_v4.tools import LocalTools


class OnlineLedger(Ledger):
    # Human labeling order need not equal upload order. Only delivered,
    # human-confirmed records existing at prediction time can be retrieved.
    def ready_count(self, before):
        return super().ready_count(2**63 - 1)

    def ready_classes(self, before, minimum=10):
        return super().ready_classes(2**63 - 1, minimum)


class OnlineMemory(Memory):
    def __init__(self, path, scope, tools, classes, domain, method, url):
        super().__init__(path, scope, url=url)
        self.tools, self.classes, self.domain, self.method = tools, classes, domain, method
        self.prototypes = None

    def search_views(self, queries, before, exclude_sha, eligible_labels, **kwargs):
        try:
            return self._search_views(queries, before, exclude_sha, eligible_labels, **kwargs)
        except Exception as e:
            raise RuntimeError("optional retrieval unavailable") from e

    def _search_views(self, queries, before, exclude_sha, eligible_labels, **kwargs):
        options = {**kwargs, "class_limit": 10}
        refs = super().search_views(queries, 2**63 - 1, exclude_sha, eligible_labels, **options)
        if self.method != "D":
            return refs[:6]
        # Generic prompts avoid hard-coding bird semantics in other datasets.
        if self.prototypes is None:
            policy = policy_for({"birds": "cub", "cars": "cars", "general": "imagenet100"}[self.domain])
            key = digest({"classes": self.classes, "policy": policy, "tools": self.tools.revision})
            cache = self.tools.root / ("text-" + key + ".json")
            if cache.exists():
                value = json.loads(cache.read_text())
                if value.get("key") != key:
                    raise RuntimeError("text prototype identity mismatch")
                self.prototypes = value["prototypes"]
            else:
                # The low-resource runtime is ephemeral and CPU only.
                import numpy as np
                paths = list(self.tools.root.glob("*-original.png"))
                if not paths:
                    raise RuntimeError("text encoder needs an anonymous image job")
                encoded = self.tools.gpu("clip_text", paths[0], texts=class_prompts(self.classes, policy))["vectors"]
                if len(encoded) != 3 * len(self.classes):
                    raise RuntimeError("text encoder row count mismatch")
                rows = np.array([vector(v, 512) for v in encoded]).reshape(len(self.classes), 3, 512).mean(axis=1)
                self.prototypes = {"classes": [{**c, "vector": vector(v.tolist(), 512)} for c, v in zip(self.classes, rows)]}
                atomic_json(cache, {"key": key, "prototypes": self.prototypes})
        text = [r["label"] for r in text_ranking(queries, self.prototypes) if r["label"] in eligible_labels]
        labels = fuse_labels([r["label"] for r in refs], text)
        by_label = {r["label"]: r for r in refs}
        output = []
        for label in labels:
            if label not in by_label:
                matches = super().search_views(queries, 2**63 - 1, exclude_sha, [label], limit=60,
                    blocked_shas=kwargs.get("blocked_shas", ()), class_limit=1)
                if matches:
                    by_label[label] = matches[0]
            if label in by_label:
                output.append(by_label[label])
            if len(output) == 6:
                break
        return output


class OriginalTools:
    available = []
    def __init__(self, tools):
        self.tools = tools
    def prepare(self, sample, sha):
        path = self.tools.root / (sha + "-original.png")
        save_image(path, read_image(sample["image"]))
        return {"original": str(path), "original_sha": sha_file(path), "subject": None,
                "quality": quality(path), "warnings": [], "route": "original_only"}


class UnavailableMemory:
    def __init__(self, scope):
        self.scope = scope
    def search_views(self, *args, **kwargs):
        raise RuntimeError("retrieval database unavailable")
    def upsert(self, point):
        raise RuntimeError("memory outbox retained until database recovers")
    def close(self):
        pass


class Workflow:
    def __init__(self, root, project, models, locator, sam, provider, qdrant):
        self.root, self.project = Path(root), project
        self.tools = LocalTools(self.root / "cache", models, locator=locator, sam=sam)
        self.ledger = OnlineLedger(self.root, {"project": project["id"], "classes": project["classes"],
            "method": project["method"], "domain": project["domain"], "workflow": "platform-v1-cpu",
            "tools": self.tools.revision, "sam": self.tools.sam_revision,
            "provider_model": provider.model, "provider_endpoint": provider.base})
        scope = {"project": project["id"], "catalog": digest(project["classes"]), "encoder": self.tools.revision}
        try:
            self.memory = OnlineMemory(self.root / "qdrant", scope, self.tools, project["classes"],
                                       project["domain"], project["method"], qdrant)
        except Exception:
            self.memory = UnavailableMemory(scope)
        self.engine = Engine(self.ledger, self.memory,
            OriginalTools(self.tools) if project["method"] == "A" else self.tools,
            provider, project["classes"], None,
            retrieval_mode="online" if project["method"] in ("C", "D") else "off",
            workflow_policy="balanced_v44")

    def predict(self, task, path):
        with self.ledger.exclusive():
            result = self.engine.predict({"id": task["id"], "image": str(path)}, task["seq"])
            stage = self.ledger.stage(task["id"], "local:prepare")
            prepared = json.loads(stage["response"])["value"] if stage else {}
            # Return observability, never local paths, remote secrets, or raw prompts.
            return {**result, "route": prepared.get("route", "original_only"),
                "bounds": prepared.get("bounds"), "quality": prepared.get("quality", {}),
                "method": self.project["method"], "resource_profile": "cpu-low",
                "remote_seconds": round(self.ledger.remote_seconds(task["id"]), 2)}

    def confirm(self, task, path):
        if task["status"] != "confirmed" or task["label"] not in {c["id"] for c in self.project["classes"]}:
            raise ValueError("only human-confirmed records may enter memory")
        sid = task["id"]
        with self.ledger.exclusive():
            self.ledger.register(sid, task["seq"], sha_file(path), str(path))
            if self.ledger.prediction(sid) is None:
                self.ledger.freeze(sid, {"status": "manual", "class_ids": []})
            stage = self.ledger.stage(sid, "local:prepare")
            prepared = json.loads(stage["response"])["value"] if stage else OriginalTools(self.tools).prepare({"image": path}, task["sha"])
            original = prepared["original"]
            if sha_file(original) != prepared["original_sha"]:
                raise ValueError("confirmed original changed")
            payload = {**self.memory.scope, "sha": task["sha"], "sample": sid, "seq": task["seq"],
                "label": task["label"], "image": original, "image_sha": sha_file(original), "view": "original"}
            point = {"vector": self.tools.embed(original), "payload": payload}
            if prepared.get("subject") and subject_eligible(prepared):
                try:
                    point["views"] = [{"vector": self.tools.embed(prepared["subject"]), "payload": {
                        **payload, "view": "subject", "view_image": prepared["subject"],
                        "view_sha": prepared["subject_sha"]}}]
                except (RuntimeError, OSError):
                    pass  # Original indexing remains valid; enhancements never enter memory.
            self.ledger.confirm(sid, task["label"], point)
            flush_outbox(self.ledger, self.memory, attempts=1)

    def close(self):
        self.tools.close()
        self.memory.close()
        self.ledger.close()

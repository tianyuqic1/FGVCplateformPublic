import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from lab_v4.engine import Engine
from lab_v4.images import sha_file
from lab_v4.memory import Memory
from lab_v4.state import UnknownOutcome
from lab_v4.tools import LocalTools
from workflow import OnlineLedger, OnlineMemory, Workflow
from worker import prediction


class Tools:
    available = []
    def __init__(self, root):
        self.root = Path(root)
    def prepare(self, sample, sha):
        return {"original": sample["image"], "original_sha": sha, "subject": None,
                "quality": {}, "warnings": [], "route": "original_only"}
    def embed(self, path):
        return [1.0] + [0.0] * 511
    def close(self):
        pass


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = OnlineLedger(self.root / "ledger", {"test": 1})
        self.memory = Memory(self.root / "memory", {"project": "p"})
        self.tools = Tools(self.root)
        self.classes = [{"id": "a", "name": "Alpha"}, {"id": "b", "name": "Beta"}]
    def tearDown(self):
        self.memory.close()
        self.ledger.close()
        self.temp.cleanup()
    def image(self, i):
        path = self.root / f"image-{i}.png"
        Image.new("RGB", (16, 16), (i, 20, 30)).save(path)
        return path
    def workflow(self):
        w = Workflow.__new__(Workflow)
        w.ledger, w.memory, w.tools = self.ledger, self.memory, self.tools
        w.project = {"classes": self.classes}
        return w
    def test_small_catalog_and_no_auto_confirmation(self):
        provider = Mock(spec=[], return_value={"kind": "ok", "raw": json.dumps({"class_ids": ["b", "a"]})})
        engine = Engine(self.ledger, self.memory, self.tools, provider, self.classes, None,
                        retrieval_mode="off", workflow_policy="balanced_v44")
        path = self.image(1)
        result = engine.predict({"id": "x", "image": str(path)}, 1)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.ledger.ready_count(999), 0)
        self.assertEqual(self.memory.client.count(self.memory.collection).count, 0)
        engine.predict({"id": "x", "image": str(path)}, 1)
        self.assertEqual(provider.call_count, 1)
    def test_unknown_is_never_automatically_replayed(self):
        provider = Mock(spec=[], return_value={"kind": "unknown", "error": "timeout"})
        engine = Engine(self.ledger, self.memory, self.tools, provider, self.classes, None, retrieval_mode="off")
        path = self.image(1)
        for _ in range(2):
            with self.assertRaises(UnknownOutcome):
                engine.predict({"id": "x", "image": str(path)}, 1)
        self.assertEqual(provider.call_count, 1)
    def test_manual_label_outbox_and_ten_distinct_sources(self):
        w = self.workflow()
        for i in range(10):
            path = self.image(i)
            task = {"id": str(i), "seq": i + 100, "sha": sha_file(path), "status": "confirmed", "label": "b"}
            w.confirm(task, path)
            if i == 8:
                self.assertEqual(self.ledger.ready_classes(0), {})
        # Deliberately query an earlier uploaded sample: human confirmation time,
        # not upload order, determines availability in an annotation workspace.
        self.assertEqual(self.ledger.ready_classes(0), {"b": 10})
        w.confirm(task, path)
        self.assertEqual(self.ledger.ready_count(0), 10)
        self.assertEqual(self.memory.client.count(self.memory.collection).count, 10)
    def test_only_human_confirmation_can_enter_memory(self):
        path = self.image(1)
        for status in ("pending", "suggested", "running", "unknown"):
            with self.assertRaises(ValueError):
                self.workflow().confirm({"status": status, "label": "a"}, path)
        self.assertEqual(self.memory.client.count(self.memory.collection).count, 0)
    def test_subject_view_is_indexed_but_counts_as_one_source(self):
        original = self.root / "original.png"
        subject = self.root / "subject.png"
        enhanced = self.root / "enhanced.png"
        Image.new("RGB", (400, 300), (70, 80, 90)).save(original)
        Image.new("RGB", (200, 200), (70, 80, 90)).save(subject)
        Image.new("RGB", (200, 200), (170, 180, 190)).save(enhanced)
        task = {"id": "crop", "seq": 1, "sha": sha_file(original), "status": "confirmed", "label": "a"}
        self.ledger.register(task["id"], 1, task["sha"], str(original))
        self.ledger.freeze(task["id"], {"status": "ok", "class_ids": ["a", "b"]})
        prepared = {"original": str(original), "original_sha": sha_file(original),
            "subject": str(subject), "subject_sha": sha_file(subject), "bounds": [50, 30, 250, 230],
            "route": "qwen_sam_union", "enhanced": str(enhanced)}
        self.ledger.local(task["id"], "local:prepare", {}, lambda: prepared)
        with patch.object(self.tools, "embed", wraps=self.tools.embed) as embed:
            self.workflow().confirm(task, original)
            self.assertEqual([call.args[0] for call in embed.call_args_list], [str(original), str(subject)])
        self.assertEqual(self.memory.client.count(self.memory.collection).count, 2)
        self.assertEqual(self.ledger.ready_count(0), 1)
        self.assertEqual(self.ledger.ready_classes(0), {})
    def test_failed_vector_delivery_does_not_advance_gate(self):
        path = self.image(1)
        task = {"id": "x", "seq": 1, "sha": sha_file(path), "status": "confirmed", "label": "a"}
        with patch.object(self.memory, "upsert", side_effect=RuntimeError("database offline")):
            with self.assertRaises(RuntimeError):
                self.workflow().confirm(task, path)
        self.assertEqual(len(self.ledger.pending_points()), 1)
        self.assertEqual(self.ledger.ready_count(999), 0)
        self.workflow().confirm(task, path)
        self.assertEqual(len(self.ledger.pending_points()), 0)
        self.assertEqual(self.ledger.ready_count(999), 1)
    def test_scope_and_self_exclusion(self):
        path = self.image(1)
        self.workflow().confirm({"id": "x", "seq": 1, "sha": sha_file(path), "status": "confirmed", "label": "a"}, path)
        self.assertEqual(self.memory.search_views({"original": self.tools.embed(path)}, 999, sha_file(path), {"a"}), [])
        with patch.dict(self.memory.scope, {"project": "another"}):
            self.assertEqual(self.memory.search_views({"original": self.tools.embed(path)}, 999, "different", {"a"}), [])
    def test_worker_quarantines_unknown(self):
        w = Mock()
        w.predict.side_effect = UnknownOutcome("network")
        result = prediction(w, {}, "unused")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(w.predict.call_count, 1)
    def test_retrieval_error_is_optional(self):
        memory = OnlineMemory.__new__(OnlineMemory)
        memory._search_views = Mock(side_effect=ValueError("prototype mismatch"))
        with self.assertRaises(RuntimeError):
            memory.search_views({}, 1, "sha", {})
    def test_cpu_sr_rejects_high_resolution_without_dispatch(self):
        tools = LocalTools.__new__(LocalTools)
        tools.available = ["sr_x2"]
        tools.root = self.root
        tools.cached = Mock(return_value={"path": "test-output"})
        large = self.root / "large.png"
        Image.new("RGB", (500, 400)).save(large)
        with self.assertRaises(ValueError):
            tools.enhance("sr_x2", large)
        tools.cached.assert_not_called()
        self.assertEqual(tools.enhance("sr_x2", self.image(1)), "test-output")
        tools.cached.assert_called_once()


if __name__ == "__main__":
    unittest.main()

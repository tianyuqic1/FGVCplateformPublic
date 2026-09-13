import fcntl
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class UnknownOutcome(RuntimeError):
    pass


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Ledger:
    def __init__(self, root, config):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "state.sqlite", timeout=10)
        self.db.row_factory = sqlite3.Row
        # Reject incompatible runs before any DDL or journal-mode mutation.
        if self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='config'"
        ).fetchone():
            saved = self.db.execute("SELECT value FROM config WHERE id=1").fetchone()
            if saved and saved["value"] != canonical(config):
                self.db.close()
                raise ValueError(
                    "run configuration changed; use new empty run directory"
                )
        self.db.executescript("""
        PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS samples(id TEXT PRIMARY KEY,seq INTEGER UNIQUE NOT NULL,sha TEXT NOT NULL,path TEXT NOT NULL,started REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS stages(sample TEXT REFERENCES samples(id),name TEXT,request_hash TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,response TEXT,error TEXT,next_at REAL NOT NULL DEFAULT 0,PRIMARY KEY(sample,name));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,sample TEXT,stage TEXT,kind TEXT,detail TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS predictions(sample TEXT PRIMARY KEY REFERENCES samples(id),value TEXT NOT NULL,created REAL NOT NULL);
        CREATE TRIGGER IF NOT EXISTS immutable_prediction_update BEFORE UPDATE ON predictions BEGIN SELECT RAISE(ABORT,'prediction is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_prediction_delete BEFORE DELETE ON predictions BEGIN SELECT RAISE(ABORT,'prediction is immutable'); END;
        CREATE TABLE IF NOT EXISTS confirmed(sha TEXT PRIMARY KEY,sample TEXT UNIQUE REFERENCES predictions(sample),label TEXT NOT NULL,seq INTEGER NOT NULL,point TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox(sha TEXT PRIMARY KEY REFERENCES confirmed(sha),payload TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS tasks(sample TEXT PRIMARY KEY,status TEXT NOT NULL,next_at REAL NOT NULL DEFAULT 0,result TEXT NOT NULL);
        """)
        old = self.db.execute("SELECT value FROM config WHERE id=1").fetchone()
        value = canonical(config)
        if old and old["value"] != value:
            self.db.close()
            raise ValueError("run configuration changed; use new empty run directory")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO config VALUES(1,?)", (value,))
        self.config = config

    @contextmanager
    def exclusive(self):
        with (self.root / "coordinator.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("another coordinator owns this run")
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def close(self):
        self.db.close()

    def event(self, sample, stage, kind, detail):
        self.db.execute(
            "INSERT INTO events(sample,stage,kind,detail,created) VALUES(?,?,?,?,?)",
            (sample, stage, kind, canonical(detail), time.time()),
        )

    def register(self, sample, seq, sha, path):
        with self.db:
            row = self.db.execute(
                "SELECT * FROM samples WHERE id=?", (sample,)
            ).fetchone()
            if row:
                if (row["seq"], row["sha"], row["path"]) != (seq, sha, path):
                    raise ValueError("sample changed during resume")
            else:
                previous = self.db.execute("SELECT MAX(seq) FROM samples").fetchone()[0]
                if previous is not None and seq <= previous:
                    raise ValueError("fixed sequence required")
                self.db.execute(
                    "INSERT INTO samples VALUES(?,?,?,?,?)",
                    (sample, seq, sha, path, time.time()),
                )

    def stage(self, sample, name):
        row = self.db.execute(
            "SELECT * FROM stages WHERE sample=? AND name=?", (sample, name)
        ).fetchone()
        return dict(row) if row else None

    def begin(self, sample, name, request, max_attempts=3, max_paid=6):
        h = digest(request)
        with self.db:
            row = self.stage(sample, name)
            if row and row["request_hash"] != h:
                raise ValueError("stage input changed during resume")
            if row and row["status"] in ("pending", "unknown"):
                raise UnknownOutcome(
                    f"{sample}/{name}: explicitly resolve unknown request before resuming"
                )
            if row and row["status"] == "done":
                return json.loads(row["response"])
            if row and row["status"] == "failed":
                return (
                    json.loads(row["response"])
                    if row["response"]
                    else {"kind": "failed", "error": row["error"]}
                )
            count = row["attempts"] if row else 0
            paid = self.db.execute(
                "SELECT COALESCE(SUM(attempts),0) FROM stages WHERE sample=? AND name LIKE 'model:%'",
                (sample,),
            ).fetchone()[0]
            if count >= max_attempts or paid >= max_paid:
                return {"kind": "failed", "error": "attempt budget exhausted"}
            if row and row["next_at"] > time.time():
                return {"kind": "wait", "until": row["next_at"]}
            self.db.execute(
                "INSERT INTO stages(sample,name,request_hash,status,attempts) VALUES(?,?,?,'pending',1) ON CONFLICT(sample,name) DO UPDATE SET status='pending',attempts=stages.attempts+1",
                (sample, name, h),
            )
            self.event(
                sample, name, "dispatch", {"attempt": count + 1, "request_hash": h}
            )
        return None

    def complete(self, sample, name, response):
        status = {
            "ok": "done",
            "retryable": "retry",
            "deferred": "retry",
            "unknown": "unknown",
        }.get(response.get("kind"), "failed")
        import math

        wait = float(response.get("retry_after", 0))
        if not math.isfinite(wait) or wait < 0:
            raise ValueError("invalid retry delay")
        with self.db:
            if response.get("kind") == "deferred":
                if response.get("dispatched") is not False:
                    raise ValueError(
                        "only explicit local admission may defer without an attempt"
                    )
                self.db.execute(
                    "UPDATE stages SET attempts=MAX(0,attempts-1) WHERE sample=? AND name=?",
                    (sample, name),
                )
            self.db.execute(
                "UPDATE stages SET status=?,response=?,error=?,next_at=? WHERE sample=? AND name=?",
                (
                    status,
                    canonical(response),
                    response.get("error", ""),
                    time.time() + wait,
                    sample,
                    name,
                ),
            )
            self.event(sample, name, status, response)

    def resolve(self, sample, name, action):
        if action not in ("retry", "fail"):
            raise ValueError("resolution must be retry or fail")
        with self.db:
            row = self.stage(sample, name)
            fatal = (
                row
                and row["status"] == "failed"
                and row["response"]
                and json.loads(row["response"]).get("kind") == "fatal"
            )
            if not row or (row["status"] not in ("pending", "unknown") and not fatal):
                raise ValueError("no uncertain or configuration-blocked request")
            self.db.execute(
                "UPDATE stages SET status=?,error=?,response=NULL,next_at=0 WHERE sample=? AND name=?",
                (
                    "retry" if action == "retry" else "failed",
                    "operator resolved uncertain request",
                    sample,
                    name,
                ),
            )
            self.event(
                sample,
                name,
                "operator_resolution",
                {"action": action, "duplicate_charge_possible": action == "retry"},
            )

    def prediction(self, sample):
        row = self.db.execute(
            "SELECT value FROM predictions WHERE sample=?", (sample,)
        ).fetchone()
        return json.loads(row["value"]) if row else None

    def remote_seconds(self, sample):
        # Events retain all attempts, unlike the stage's last-response snapshot.
        total = 0.0
        for row in self.db.execute(
            "SELECT detail FROM events WHERE sample=? AND stage LIKE 'model:%' AND kind IN ('done','retry','unknown','failed')",
            (sample,),
        ):
            response = json.loads(row["detail"])
            if response.get("dispatched") is not False:
                total += max(0, float(response.get("latency_ms", 0))) / 1000
        return total

    def task(self, sample):
        row = self.db.execute(
            "SELECT * FROM tasks WHERE sample=?", (sample,)
        ).fetchone()
        return dict(row) if row else None

    def save_task(self, sample, result, next_at=0):
        with self.db:
            self.db.execute(
                "INSERT INTO tasks VALUES(?,?,?,?) ON CONFLICT(sample) DO UPDATE SET status=excluded.status,next_at=excluded.next_at,result=excluded.result",
                (sample, result["status"], next_at, canonical(result)),
            )

    def freeze(self, sample, value):
        with self.db:
            old = self.prediction(sample)
            if old is not None:
                if canonical(old) != canonical(value):
                    raise ValueError("cannot overwrite locked prediction")
                return
            self.db.execute(
                "INSERT INTO predictions VALUES(?,?,?)",
                (sample, canonical(value), time.time()),
            )
            self.event(sample, "prediction", "locked", {"status": value["status"]})

    def confirm(self, sample, label, point):
        with self.db:
            if self.prediction(sample) is None:
                raise ValueError("prediction must be locked before annotation")
            row = self.db.execute(
                "SELECT * FROM samples WHERE id=?", (sample,)
            ).fetchone()
            expected = {
                "sha": row["sha"],
                "seq": row["seq"],
                "sample": sample,
                "label": label,
            }
            if any(point["payload"].get(k) != v for k, v in expected.items()):
                raise ValueError("confirmation payload mismatch")
            for view in point.get("views", []):
                if view["payload"].get("view") != "subject" or any(
                    view["payload"].get(k) != point["payload"].get(k)
                    for k in (*expected, "image", "image_sha")
                ):
                    raise ValueError("confirmation view mismatch")
            old = self.db.execute(
                "SELECT label FROM confirmed WHERE sha=?", (row["sha"],)
            ).fetchone()
            if old:
                if old["label"] != label:
                    raise ValueError("conflicting labels for identical image")
                return
            self.db.execute(
                "INSERT INTO confirmed(sha,sample,label,seq,point) VALUES(?,?,?,?,?)",
                (row["sha"], sample, label, row["seq"], canonical(point)),
            )
            self.db.execute(
                "INSERT INTO outbox(sha,payload) VALUES(?,?)",
                (row["sha"], canonical(point)),
            )
            self.event(
                sample, "annotation", "confirmed", {"label": label, "seq": row["seq"]}
            )

    def ready_count(self, before):
        return self.db.execute(
            "SELECT COUNT(*) FROM confirmed c JOIN outbox o ON c.sha=o.sha WHERE o.delivered=1 AND c.seq<?",
            (before,),
        ).fetchone()[0]

    def ready_classes(self, before, minimum=10):
        # confirmed.sha is unique: extra crop vectors never advance this gate.
        return {
            r["label"]: r["n"]
            for r in self.db.execute(
                "SELECT c.label,COUNT(*) n FROM confirmed c JOIN outbox o ON c.sha=o.sha "
                "WHERE o.delivered=1 AND c.seq<? GROUP BY c.label HAVING COUNT(*)>=?",
                (before, minimum),
            )
        }

    def pending_points(self):
        return [
            (r["sha"], json.loads(r["payload"]))
            for r in self.db.execute(
                "SELECT * FROM outbox WHERE delivered=0 ORDER BY rowid"
            )
        ]

    def delivered(self, sha):
        with self.db:
            self.db.execute("UPDATE outbox SET delivered=1 WHERE sha=?", (sha,))

    def local(self, sample, name, request, compute):
        """Idempotent local step. Unlike remote requests, crash recovery may recompute."""
        row = self.stage(sample, name)
        h = digest(request)
        if row:
            if row["request_hash"] != h:
                raise ValueError("local input changed during resume")
            if row["status"] == "done":
                return json.loads(row["response"])["value"]
        with self.db:
            self.db.execute(
                "INSERT INTO stages(sample,name,request_hash,status,attempts) VALUES(?,?,?,'pending',1) ON CONFLICT(sample,name) DO UPDATE SET attempts=stages.attempts+1",
                (sample, name, h),
            )
        value = compute()
        self.complete(sample, name, {"kind": "ok", "value": value})
        return value

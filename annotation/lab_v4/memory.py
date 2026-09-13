import math
import uuid

from .images import sha_file
from .policy import fuse_references
from .state import canonical


def vector(value, dim):
    if (
        not isinstance(value, (tuple, list))
        or len(value) != dim
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
    ):
        raise ValueError("invalid embedding")
    norm = math.hypot(*value)
    if not math.isfinite(norm) or norm < 1e-8:
        raise ValueError("invalid embedding norm")
    return [float(v / norm) for v in value]


class Memory:
    def __init__(self, path, scope, dim=512, url=None):
        from qdrant_client import QdrantClient, models

        self.m = models
        self.scope = scope
        self.dim = dim
        self.client = QdrantClient(url=url, timeout=10) if url else QdrantClient(path=str(path))
        self.collection = "confirmed_samples"
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                self.collection,
                vectors_config=models.VectorParams(
                    size=dim, distance=models.Distance.COSINE
                ),
            )
        params = self.client.get_collection(self.collection).config.params.vectors
        if params.size != dim or params.distance != models.Distance.COSINE:
            self.client.close()
            raise ValueError("embedding collection configuration changed")

    def close(self):
        self.client.close()

    def upsert(self, point):
        # One durable outbox entry per source, one idempotent point batch.
        points, seen = [], set()
        for item in [point, *point.get("views", [])]:
            payload = item["payload"]
            if any(payload.get(k) != v for k, v in self.scope.items()):
                raise ValueError("point scope mismatch")
            for k in ("sha", "sample", "seq", "label", "image", "image_sha"):
                if payload.get(k) != point["payload"].get(k):
                    raise ValueError("auxiliary view source mismatch")
            view = payload.get("view", "original")
            if view not in ("original", "subject") or view in seen:
                raise ValueError("invalid or duplicate memory view")
            if not seen and view != "original":
                raise ValueError("canonical memory point must be original")
            if (
                view == "subject"
                and sha_file(payload["view_image"]) != payload["view_sha"]
            ):
                raise ValueError("memory crop changed")
            seen.add(view)
            key = canonical(self.scope) + "/" + payload["sha"]
            if view != "original":
                key += "/" + view
            points.append(
                self.m.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
                    vector=vector(item["vector"], self.dim),
                    payload=payload,
                )
            )
        self.client.upsert(
            self.collection,
            points=points,
            wait=True,
        )

    def search_views(
        self,
        queries,
        before,
        exclude_sha,
        eligible_labels,
        limit=60,
        blocked_shas=(),
        class_limit=6,
    ):
        if not eligible_labels:
            return []
        m = self.m
        must = [
            m.FieldCondition(key=k, match=m.MatchValue(value=v))
            for k, v in self.scope.items()
        ]
        must += [
            m.FieldCondition(key="seq", range=m.Range(lt=before)),
            m.FieldCondition(
                key="label", match=m.MatchAny(any=sorted(eligible_labels))
            ),
        ]
        filt = m.Filter(
            must=must,
            must_not=[
                m.FieldCondition(
                    key="sha",
                    match=m.MatchAny(any=sorted({exclude_sha, *blocked_shas})),
                )
            ],
        )
        rankings = {}
        for view, query in queries.items():
            points = self.client.query_points(
                self.collection,
                query=vector(query, self.dim),
                query_filter=filt,
                limit=limit,
                with_payload=True,
            ).points
            rankings[view] = [{**p.payload, "similarity": p.score} for p in points]
        return fuse_references(rankings, limit=class_limit)

    def search(self, query, before, exclude_sha, limit=20):
        m = self.m
        must = [
            m.FieldCondition(key=k, match=m.MatchValue(value=v))
            for k, v in self.scope.items()
        ]
        must.append(m.FieldCondition(key="seq", range=m.Range(lt=before)))
        points = self.client.query_points(
            self.collection,
            query=vector(query, self.dim),
            query_filter=m.Filter(
                must=must,
                must_not=[
                    m.FieldCondition(key="sha", match=m.MatchValue(value=exclude_sha))
                ],
            ),
            limit=limit,
            with_payload=True,
        ).points
        refs = []
        counts = {}
        for p in points:
            label = p.payload["label"]
            counts.setdefault(label, 0)
            if counts[label] >= 2:
                continue
            counts[label] += 1
            refs.append({**p.payload, "similarity": p.score})
            if len(refs) == 6:
                break
        return refs


def flush_outbox(ledger, memory, attempts=3, sleep=None):
    import random
    import time

    sleep = sleep or time.sleep
    for sha, point in ledger.pending_points():
        for attempt in range(attempts):
            try:
                memory.upsert(point)
                ledger.delivered(sha)
                break
            except (ConnectionError, TimeoutError, OSError) as error:
                if attempt == attempts - 1:
                    raise RuntimeError(
                        "vector outbox pending; resume before next prediction"
                    ) from error
                sleep(random.uniform(0, min(8, 2**attempt)))

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class FineR1ServiceError(RuntimeError):
    pass


def rerank_candidates(
    *,
    service_url: str,
    image_data_url: str,
    candidates: list[str],
    dataset_summary: str,
    request_id: str,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    payload = json.dumps(
        {
            "image_data_url": image_data_url,
            "candidates": candidates,
            "dataset_summary": dataset_summary,
            "request_id": request_id,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        f"{service_url.rstrip('/')}/v1/rerank",
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise FineR1ServiceError(f"Fine-R1 service returned HTTP {exc.code}: {detail[:1000]}") from exc
    except (TimeoutError, URLError) as exc:
        raise FineR1ServiceError(f"Fine-R1 service request failed: {exc}") from exc
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise FineR1ServiceError("Fine-R1 service returned invalid JSON") from exc
    label = str(result.get("suggested_label") or "").strip()
    if label not in candidates:
        raise FineR1ServiceError("Fine-R1 result does not uniquely map to a candidate label")
    return result


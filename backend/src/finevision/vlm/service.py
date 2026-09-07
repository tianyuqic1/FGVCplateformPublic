from __future__ import annotations

import os
import re
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator


ANSWER_PATTERN = re.compile(r"<answer>(.*?)</answer>", re.DOTALL | re.IGNORECASE)
THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)
PROMPT_VERSION = "finevision-finer1-v1.1"


class RerankRequest(BaseModel):
    image_data_url: str = Field(..., min_length=20)
    candidates: list[str] = Field(..., min_length=2, max_length=10)
    dataset_summary: str = Field(default="", max_length=6000)
    request_id: str = Field(..., min_length=1, max_length=200)

    @field_validator("candidates")
    @classmethod
    def validate_candidates(cls, value: list[str]) -> list[str]:
        normalized = [str(item).strip() for item in value]
        if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("candidates must be non-empty and unique")
        return normalized


class FineR1Runtime:
    def __init__(self) -> None:
        self.model_path = os.environ.get("FINEVISION_FINER1_MODEL_PATH", "StevenHH2000/Fine-R1-3B")
        self.model_revision = os.environ.get("FINEVISION_FINER1_MODEL_REVISION")
        self.max_new_tokens = int(os.environ.get("FINEVISION_FINER1_MAX_NEW_TOKENS", "1024"))
        self._model: Any = None
        self._processor: Any = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self.loaded:
            return
        with self._lock:
            if self.loaded:
                return
            try:
                import torch
                from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration
            except ImportError as exc:
                raise RuntimeError("Install FineVision with the vlm extra to run Fine-R1") from exc
            self._model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                self.model_path,
                revision=self.model_revision,
                device_map="auto",
                dtype=torch.bfloat16,
                low_cpu_mem_usage=True,
            )
            self._model.eval()
            self._processor = AutoProcessor.from_pretrained(self.model_path, revision=self.model_revision)
            self._processor.tokenizer.padding_side = "left"

    def rerank(self, request: RerankRequest) -> dict[str, Any]:
        self.load()
        import torch
        from qwen_vl_utils import process_vision_info

        prompt = _build_prompt(request.dataset_summary, request.candidates)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": request.image_data_url},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        text = self._processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = self._processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        ).to(next(self._model.parameters()).device)
        input_tokens = inputs.input_ids.shape[-1]
        started_at = time.perf_counter()
        with self._lock, torch.inference_mode():
            generated = self._model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                use_cache=True,
            )
        latency_seconds = time.perf_counter() - started_at
        generated_tokens = generated.shape[-1] - input_tokens
        output = self._processor.decode(generated[0, input_tokens:], skip_special_tokens=True)
        answer = _parse_candidate(output, request.candidates)
        if answer is None:
            raise ValueError("Fine-R1 output did not contain exactly one candidate label")
        think_match = THINK_PATTERN.search(output)
        return {
            "request_id": request.request_id,
            "suggested_label": answer,
            "reasoning": think_match.group(1).strip() if think_match else output.strip(),
            "raw_output": output,
            "model_id": "Fine-R1-3B",
            "model_revision": self.model_revision,
            "prompt_version": PROMPT_VERSION,
            "latency_seconds": latency_seconds,
            "input_tokens": int(input_tokens),
            "generated_tokens": int(generated_tokens),
        }


def _build_prompt(dataset_summary: str, candidates: list[str]) -> str:
    summary = dataset_summary.strip() or "No additional dataset description is available."
    return (
        "You are reviewing a fine-grained image classification sample.\n"
        f"Dataset context: {summary}\n"
        f"Candidate labels: {candidates!r}\n"
        "First inspect the visible object independently. Compare discriminative shape, color, texture, "
        "parts and context against every candidate. Candidate order is randomized and conveys no rank. "
        "Do not invent a label outside the candidate list. If the image is imperfect, still select the "
        "best supported candidate while stating the uncertainty. Keep the reasoning concise: use at most "
        "180 English words inside <think>.\n"
        "Return exactly:\n<think>visual observations, candidate comparison, uncertainty</think>\n"
        "<answer>one candidate label exactly as written</answer>"
    )


def _parse_candidate(output: str, candidates: list[str]) -> str | None:
    match = ANSWER_PATTERN.search(output)
    answer = match.group(1).strip() if match else output.strip()
    exact = [candidate for candidate in candidates if answer == candidate]
    if len(exact) == 1:
        return exact[0]
    normalized_answer = _normalize(answer)
    normalized_matches = [candidate for candidate in candidates if _normalize(candidate) == normalized_answer]
    return normalized_matches[0] if len(normalized_matches) == 1 else None


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.lower().replace("_", " ")).split())


runtime = FineR1Runtime()
app = FastAPI(title="FineVision Fine-R1 Service", version="0.1.0")


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "model_loaded": runtime.loaded}


@app.get("/ready")
def ready() -> dict[str, object]:
    try:
        runtime.load()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ready", "model_loaded": True}


@app.post("/v1/rerank")
def rerank(request: RerankRequest) -> dict[str, Any]:
    try:
        return runtime.rerank(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Fine-R1 inference failed: {exc}") from exc

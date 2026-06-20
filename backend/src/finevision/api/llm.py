from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from typing import Any
import urllib.error
import urllib.request


class LLMConfigurationError(RuntimeError):
    pass


class LLMRequestError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    review_model: str
    reasoning_effort: str
    base_url: str
    wire_api: str
    disable_response_storage: bool
    requires_openai_auth: bool
    structured_outputs: bool
    api_key: str | None
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "LLMSettings":
        return cls(
            provider=os.environ.get("FINEVISION_LLM_PROVIDER", "OpenAI"),
            model=os.environ.get("FINEVISION_LLM_MODEL", "gpt-5.5"),
            review_model=os.environ.get("FINEVISION_LLM_REVIEW_MODEL", os.environ.get("FINEVISION_LLM_MODEL", "gpt-5.5")),
            reasoning_effort=os.environ.get("FINEVISION_LLM_REASONING_EFFORT", "high"),
            base_url=os.environ.get("FINEVISION_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            wire_api=os.environ.get("FINEVISION_LLM_WIRE_API", "responses"),
            disable_response_storage=_env_bool("FINEVISION_LLM_DISABLE_RESPONSE_STORAGE", default=True),
            requires_openai_auth=_env_bool("FINEVISION_LLM_REQUIRES_OPENAI_AUTH", default=True),
            structured_outputs=_env_bool("FINEVISION_LLM_STRUCTURED_OUTPUTS", default=True),
            api_key=os.environ.get("OPENAI_API_KEY") or os.environ.get("FINEVISION_LLM_API_KEY"),
            timeout_seconds=float(os.environ.get("FINEVISION_LLM_TIMEOUT_SECONDS", "60")),
        )


def generate_assistance(
    *,
    task: str,
    context: dict[str, Any],
    settings: LLMSettings | None = None,
) -> dict[str, Any]:
    resolved = settings or LLMSettings.from_env()
    _validate_settings(resolved)
    prompt = _prompt_for(task=task, context=context)
    raw_text = _responses_request(prompt=prompt, task=task, settings=resolved)
    parsed = _parse_json_object(raw_text)
    now = datetime.now(UTC).isoformat()
    return {
        "task": task,
        "advisory_only": True,
        "provider": resolved.provider,
        "model": resolved.review_model if task == "review_assistance" else resolved.model,
        "reasoning_effort": resolved.reasoning_effort,
        "created_at": now,
        "summary": str(parsed.get("summary") or raw_text).strip(),
        "inspection_notes": _string_list(parsed.get("inspection_notes")),
        "suggested_actions": _string_list(parsed.get("suggested_actions")),
        "risk_flags": _string_list(parsed.get("risk_flags")),
        "confidence": str(parsed.get("confidence") or "unknown"),
        "raw_text": raw_text,
    }


def _responses_request(*, prompt: str, task: str, settings: LLMSettings) -> str:
    model = settings.review_model if task == "review_assistance" else settings.model
    payload = _responses_payload(prompt=prompt, model=model, settings=settings)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if settings.requires_openai_auth:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    url = f"{settings.base_url}/responses"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise LLMRequestError(f"LLM provider returned {exc.code}: {detail}") from exc
    except Exception as exc:
        raise LLMRequestError(f"LLM provider request failed: {exc}") from exc
    return _extract_response_text(body)


def _responses_payload(*, prompt: str, model: str, settings: LLMSettings) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "input": prompt,
        "store": not settings.disable_response_storage,
        "reasoning": {"effort": settings.reasoning_effort},
        "max_output_tokens": 900,
    }
    if settings.structured_outputs:
        payload["text"] = {"format": _assistance_response_format()}
    return payload


def _assistance_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "finevision_llm_assistance",
        "strict": True,
        "schema": _assistance_schema(),
    }


def _assistance_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "One concise Chinese sentence summarizing the advisory result.",
            },
            "inspection_notes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Evidence or visual/model signals the human operator should inspect.",
            },
            "suggested_actions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Safe next actions. Do not include automatic submission or production changes.",
            },
            "risk_flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Caveats, hallucination risks, or reasons the operator should be careful.",
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Assistant confidence in this advisory explanation.",
            },
        },
        "required": ["summary", "inspection_notes", "suggested_actions", "risk_flags", "confidence"],
        "additionalProperties": False,
    }


def _extract_response_text(body: dict[str, Any]) -> str:
    output = body.get("output") or []
    chunks: list[str] = []
    for item in output:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            text = content.get("text")
            if text:
                chunks.append(str(text))
    if chunks:
        return "\n".join(chunks).strip()
    text = body.get("output_text")
    if text:
        return str(text).strip()
    raise LLMRequestError("LLM provider response did not include output text")


def _prompt_for(*, task: str, context: dict[str, Any]) -> str:
    task_instruction = {
        "inference_explanation": "解释这次视觉分类推理为什么 accept / abstain / reject_ood，帮助用户理解阈值、top-k、margin、OOD score。",
        "review_assistance": "辅助人工复核一张低置信或 OOD 候选图片，指出应检查的视觉线索、易混类别和风险。",
        "training_diagnosis": "根据训练运行状态、错误和缺失产物给出排障建议。",
        "feedback_curation": "根据反馈池统计给出下一轮数据策展建议，但不要自动生成数据集版本。",
    }.get(task)
    if not task_instruction:
        raise LLMConfigurationError(f"Unsupported LLM assistance task: {task}")

    return (
        "你是 FineVision 的 LLM Assistant，只能提供 advisory-only 建议，不能替代人工标签、不能调整生产阈值、"
        "不能把反馈直接写回训练集。请用中文填写结构化字段；这些字段会被 JSON Schema 严格约束。\n"
        f"任务：{task_instruction}\n"
        f"上下文 JSON：{json.dumps(context, ensure_ascii=False, default=str)}"
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else {"summary": text}
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(stripped[start : end + 1])
                return parsed if isinstance(parsed, dict) else {"summary": text}
            except json.JSONDecodeError:
                pass
    return {"summary": text}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value:
        return [str(value)]
    return []


def _validate_settings(settings: LLMSettings) -> None:
    if settings.wire_api != "responses":
        raise LLMConfigurationError("Only responses wire_api is supported in the LLM Assistant MVP")
    if settings.requires_openai_auth and not settings.api_key:
        raise LLMConfigurationError("OPENAI_API_KEY or FINEVISION_LLM_API_KEY is required")
    if not settings.base_url:
        raise LLMConfigurationError("FINEVISION_LLM_BASE_URL is required")


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

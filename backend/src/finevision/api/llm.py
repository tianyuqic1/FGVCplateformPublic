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
    fallback_models: tuple[str, ...]
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
            fallback_models=tuple(_env_list("FINEVISION_LLM_FALLBACK_MODELS")),
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
    prompt_context, image_data_urls = _prepare_context_for_prompt(context)
    prompt = _prompt_for(task=task, context=prompt_context)
    raw_text, used_model = _llm_request(prompt=prompt, task=task, settings=resolved, image_data_urls=image_data_urls)
    parsed = _parse_json_object(raw_text)
    now = datetime.now(UTC).isoformat()
    summary = str(parsed.get("summary") or parsed.get("holistic_analysis") or raw_text).strip()
    return {
        "task": task,
        "advisory_only": True,
        "provider": resolved.provider,
        "model": used_model,
        "reasoning_effort": resolved.reasoning_effort,
        "created_at": now,
        "summary": summary,
        "holistic_analysis": str(parsed.get("holistic_analysis") or parsed.get("summary") or "").strip(),
        "inspection_notes": _string_list(parsed.get("inspection_notes")),
        "suggested_actions": _string_list(parsed.get("suggested_actions")),
        "risk_flags": _string_list(parsed.get("risk_flags")),
        "confidence": str(parsed.get("confidence") or "unknown"),
        "raw_text": raw_text,
    }


def generate_dataset_card(
    *,
    manifest: Any,
    existing_card: dict[str, Any] | None = None,
    settings: LLMSettings | None = None,
) -> dict[str, Any]:
    resolved = settings or LLMSettings.from_env()
    _validate_settings(resolved)
    prompt = _dataset_card_prompt(manifest=manifest, existing_card=existing_card or {})
    raw_text, used_model = _llm_request(
        prompt=prompt,
        task="dataset_card_generation",
        settings=resolved,
        response_format=_dataset_card_response_format(),
    )
    parsed = _parse_json_object(raw_text)
    now = datetime.now(UTC).isoformat()
    return {
        "task": str(parsed.get("task") or "image_classification").strip(),
        "domain": str(parsed.get("domain") or "general image classification").strip(),
        "summary": str(parsed.get("summary") or "").strip(),
        "known_confusions": _string_list(parsed.get("known_confusions")),
        "ood_policy": str(parsed.get("ood_policy") or "").strip(),
        "review_guidance": str(parsed.get("review_guidance") or "").strip(),
        "generated_from": "llm_class_labels",
        "llm_metadata": {
            "provider": resolved.provider,
            "model": used_model,
            "reasoning_effort": resolved.reasoning_effort,
            "created_at": now,
        },
    }


def _llm_request(
    *,
    prompt: str,
    task: str,
    settings: LLMSettings,
    image_data_urls: list[str] | None = None,
    response_format: dict[str, Any] | None = None,
) -> tuple[str, str]:
    primary_model = settings.review_model if task == "review_assistance" else settings.model
    errors: list[str] = []
    for model in _candidate_models(primary_model, settings.fallback_models):
        for wire_api in _candidate_wire_apis(settings.wire_api):
            try:
                if wire_api == "responses":
                    raw_text = _responses_request(
                        prompt=prompt,
                        model=model,
                        settings=settings,
                        image_data_urls=image_data_urls,
                        response_format=response_format,
                    )
                else:
                    raw_text = _chat_completions_request(
                        prompt=prompt,
                        model=model,
                        settings=settings,
                        image_data_urls=image_data_urls,
                        response_format=response_format,
                    )
                if settings.structured_outputs:
                    _ensure_required_json_fields(raw_text, response_format or _assistance_response_format())
                return raw_text, model
            except LLMRequestError as exc:
                errors.append(f"{model}/{wire_api}: {exc}")
    details = " | ".join(errors[-6:])
    raise LLMRequestError(f"LLM provider failed for all configured models: {details}")


def _candidate_models(primary_model: str, fallback_models: tuple[str, ...]) -> list[str]:
    candidates: list[str] = []
    for model in (primary_model, *fallback_models):
        normalized = model.strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates


def _candidate_wire_apis(wire_api: str) -> list[str]:
    if wire_api == "auto":
        return ["responses", "chat_completions"]
    return [wire_api]


def _responses_request(
    *,
    prompt: str,
    model: str,
    settings: LLMSettings,
    image_data_urls: list[str] | None = None,
    response_format: dict[str, Any] | None = None,
) -> str:
    payload = _responses_payload(
        prompt=prompt,
        model=model,
        settings=settings,
        image_data_urls=image_data_urls or [],
        response_format=response_format,
    )
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if settings.requires_openai_auth:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    url = f"{settings.base_url}/responses"
    try:
        body = _post_responses(url=url, payload=payload, headers=headers, timeout_seconds=settings.timeout_seconds)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        if image_data_urls:
            fallback_payload = _responses_payload(
                prompt=prompt,
                model=model,
                settings=settings,
                image_data_urls=[],
                response_format=response_format,
            )
            try:
                body = _post_responses(url=url, payload=fallback_payload, headers=headers, timeout_seconds=settings.timeout_seconds)
            except Exception as fallback_exc:
                raise LLMRequestError(
                    f"LLM provider rejected image input with {exc.code}: {detail}; text fallback failed: {fallback_exc}"
                ) from fallback_exc
        else:
            raise LLMRequestError(f"LLM provider returned {exc.code}: {detail}") from exc
    except Exception as exc:
        raise LLMRequestError(f"LLM provider request failed: {exc}") from exc
    return _extract_response_text(body)


def _chat_completions_request(
    *,
    prompt: str,
    model: str,
    settings: LLMSettings,
    image_data_urls: list[str] | None = None,
    response_format: dict[str, Any] | None = None,
) -> str:
    payload = _chat_completions_payload(
        prompt=prompt,
        model=model,
        settings=settings,
        image_data_urls=image_data_urls or [],
        response_format=response_format,
    )
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if settings.requires_openai_auth:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    url = f"{settings.base_url}/chat/completions"
    try:
        body = _post_responses(url=url, payload=payload, headers=headers, timeout_seconds=settings.timeout_seconds)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        if image_data_urls:
            fallback_payload = _chat_completions_payload(
                prompt=prompt,
                model=model,
                settings=settings,
                image_data_urls=[],
                response_format=response_format,
            )
            try:
                body = _post_responses(url=url, payload=fallback_payload, headers=headers, timeout_seconds=settings.timeout_seconds)
            except Exception as fallback_exc:
                raise LLMRequestError(
                    f"LLM provider rejected chat image input with {exc.code}: {detail}; text fallback failed: {fallback_exc}"
                ) from fallback_exc
        else:
            raise LLMRequestError(f"LLM provider returned {exc.code}: {detail}") from exc
    except Exception as exc:
        raise LLMRequestError(f"LLM provider request failed: {exc}") from exc
    return _extract_chat_completion_text(body)


def _post_responses(*, url: str, payload: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _responses_payload(
    *,
    prompt: str,
    model: str,
    settings: LLMSettings,
    image_data_urls: list[str] | None = None,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    image_inputs = [
        {"type": "input_image", "image_url": image_data_url, "detail": "auto"}
        for image_data_url in (image_data_urls or [])
        if _is_image_data_url(image_data_url)
    ]
    response_input: str | list[dict[str, Any]]
    if image_inputs:
        response_input = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    *image_inputs,
                ],
            }
        ]
    else:
        response_input = prompt
    payload: dict[str, Any] = {
        "model": model,
        "input": response_input,
        "store": not settings.disable_response_storage,
        "reasoning": {"effort": settings.reasoning_effort},
        "max_output_tokens": 900,
    }
    if settings.structured_outputs:
        payload["text"] = {"format": response_format or _assistance_response_format()}
    return payload


def _chat_completions_payload(
    *,
    prompt: str,
    model: str,
    settings: LLMSettings,
    image_data_urls: list[str] | None = None,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = _json_object_prompt(prompt=prompt, response_format=response_format)
    image_inputs = [
        {"type": "image_url", "image_url": {"url": image_data_url, "detail": "auto"}}
        for image_data_url in (image_data_urls or [])
        if _is_image_data_url(image_data_url)
    ]
    content: str | list[dict[str, Any]]
    if image_inputs:
        content = [{"type": "text", "text": prompt}, *image_inputs]
    else:
        content = prompt
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "max_tokens": 900,
    }
    if settings.structured_outputs:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _json_object_prompt(*, prompt: str, response_format: dict[str, Any] | None) -> str:
    schema = (response_format or _assistance_response_format()).get("schema", {})
    required = schema.get("required") if isinstance(schema, dict) else None
    return (
        f"{prompt}\n\n"
        "输出要求：必须只返回一个合法 JSON object，不要使用 Markdown，不要添加解释文字。"
        "JSON object 必须符合下面的 schema；缺失字段也要用合理空值补齐。\n"
        f"必须包含字段：{json.dumps(required or [], ensure_ascii=False)}。\n"
        f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
    )


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
                "maxLength": 180,
                "description": "One concise Chinese sentence for an operator. Do not dump raw logs or many decimals.",
            },
            "holistic_analysis": {
                "type": "string",
                "maxLength": 360,
                "description": (
                    "A first-pass holistic judgment before checklist items. Combine the image reference, "
                    "dataset summary, top-k evidence, thresholds, and any prior preliminary judgment. "
                    "If no image pixels are available, say the judgment is based on metadata and model evidence."
                ),
            },
            "inspection_notes": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {"type": "string", "maxLength": 140},
                "description": "Short evidence checks for the human operator. Prefer plain language over raw metric repetition.",
            },
            "suggested_actions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {"type": "string", "maxLength": 140},
                "description": "Safe next actions. Do not include automatic submission or production changes.",
            },
            "risk_flags": {
                "type": "array",
                "maxItems": 3,
                "items": {"type": "string", "maxLength": 140},
                "description": "Caveats, hallucination risks, or reasons the operator should be careful.",
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Assistant confidence in this advisory explanation.",
            },
        },
        "required": ["summary", "holistic_analysis", "inspection_notes", "suggested_actions", "risk_flags", "confidence"],
        "additionalProperties": False,
    }


def _dataset_card_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "finevision_dataset_card",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "maxLength": 80,
                    "description": "Dataset task, usually image_classification or fine_grained_image_classification.",
                },
                "domain": {
                    "type": "string",
                    "maxLength": 120,
                    "description": "Human-readable domain inferred from class labels, such as bird species or plant disease images.",
                },
                "summary": {
                    "type": "string",
                    "maxLength": 320,
                    "description": "Concise Chinese description of what the dataset is about and what labels are in scope.",
                },
                "known_confusions": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {"type": "string", "maxLength": 220},
                    "description": "Likely class confusions based only on class labels and domain knowledge.",
                },
                "ood_policy": {
                    "type": "string",
                    "maxLength": 260,
                    "description": "Chinese guidance for what should be treated as out of domain for this dataset.",
                },
                "review_guidance": {
                    "type": "string",
                    "maxLength": 260,
                    "description": "Chinese guidance for a human reviewer using the dataset scope.",
                },
            },
            "required": ["task", "domain", "summary", "known_confusions", "ood_policy", "review_guidance"],
            "additionalProperties": False,
        },
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


def _extract_chat_completion_text(body: dict[str, Any]) -> str:
    choices = body.get("choices") or []
    for choice in choices:
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    raise LLMRequestError("LLM provider chat response did not include message content")


def _prepare_context_for_prompt(context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    image_data_urls: list[str] = []

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, item in value.items():
                if key == "image_data_url" and isinstance(item, str) and _is_image_data_url(item):
                    image_data_urls.append(item)
                    cleaned[key] = "[attached image pixels omitted from JSON context]"
                    cleaned["image_pixels_attached"] = True
                    continue
                cleaned[str(key)] = scrub(item)
            return cleaned
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(context), image_data_urls[:1]


def _is_image_data_url(value: str) -> bool:
    return value.startswith("data:image/") and ";base64," in value


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
        "写作要求：面向视觉复核员，不要复述大段原始指标；分数最多保留两位小数；不要臆测图像内容；"
        "如果数据集类别已知，只围绕候选类别、阈值原因和人工检查动作给出短建议。"
        "先填写 holistic_analysis：结合 image_input、dataset_summary、top-k、阈值原因做综合初判；"
        "如果 image_input.image_pixels_attached=true，可以参考图像像素但仍保持保守；"
        "如果没有收到真实图像像素，只能说明这是基于图片引用/文件名和模型证据的初判。"
        "随后填写 inspection_notes、suggested_actions、risk_flags 时，要把 holistic_analysis 作为上下文，"
        "避免前后矛盾。\n"
        f"任务：{task_instruction}\n"
        f"上下文 JSON：{json.dumps(context, ensure_ascii=False, default=str)}"
    )


def _dataset_card_prompt(*, manifest: Any, existing_card: dict[str, Any]) -> str:
    classes = list(getattr(manifest, "classes", []) or [])
    split_counts = getattr(manifest, "split_counts", {}) or {}
    split_totals = {
        split: sum(class_counts.values())
        for split, class_counts in split_counts.items()
        if isinstance(class_counts, dict)
    }
    context = {
        "dataset_id": getattr(manifest, "dataset_id", ""),
        "dataset_version_id": getattr(manifest, "dataset_version_id", ""),
        "class_labels": classes[:400],
        "class_labels_truncated": len(classes) > 400,
        "class_count": len(classes),
        "sample_count": len(getattr(manifest, "samples", []) or []),
        "split_totals": split_totals,
        "readiness": getattr(manifest, "readiness", {}) or {},
        "current_dataset_card": {
            key: value
            for key, value in (existing_card or {}).items()
            if key in {"task", "domain", "summary", "known_confusions", "ood_policy", "review_guidance"}
        },
    }
    return (
        "你是 FineVision 的数据集摘要助手。请只根据 dataset_id、class_labels、样本统计和已有摘要，"
        "为视觉分类数据集生成可编辑的 dataset card。重点是读取类别标签，判断数据集大致关于什么："
        "例如鸟类物种、植物病害、车辆/交通工具、CIFAR-10 通用物体等。\n"
        "约束：不要发明 class_labels 之外的正式类别；不要给训练参数建议；不要把摘要写成营销文案；"
        "不确定领域时明确写成通用图像分类或需要人工补充。输出中文，简洁、可给推理和复核 LLM 作为上下文。"
        "known_confusions 只能写基于类别名可合理推断的易混点；不要堆长串英文类别名，"
        "不要输出被截断的半个类别名，优先写完整、短句、类别族级别的易混原因。\n"
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


def _ensure_required_json_fields(text: str, response_format: dict[str, Any]) -> None:
    schema = response_format.get("schema", {})
    required = schema.get("required", []) if isinstance(schema, dict) else []
    if not required:
        return
    parsed = _parse_json_object(text)
    missing = [field for field in required if field not in parsed]
    if missing:
        raise LLMRequestError(f"LLM response missing required structured fields: {', '.join(missing)}")


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value:
        return [str(value)]
    return []


def _validate_settings(settings: LLMSettings) -> None:
    if settings.wire_api not in {"responses", "chat_completions", "auto"}:
        raise LLMConfigurationError("FINEVISION_LLM_WIRE_API must be responses, chat_completions, or auto")
    if settings.requires_openai_auth and not settings.api_key:
        raise LLMConfigurationError("OPENAI_API_KEY or FINEVISION_LLM_API_KEY is required")
    if not settings.base_url:
        raise LLMConfigurationError("FINEVISION_LLM_BASE_URL is required")


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str]:
    value = os.environ.get(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]

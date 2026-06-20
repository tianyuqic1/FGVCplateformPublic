from __future__ import annotations

from finevision.api.llm import LLMSettings, _responses_payload


def test_responses_payload_uses_strict_json_schema_format() -> None:
    settings = LLMSettings(
        provider="OpenAI",
        model="gpt-5.5",
        review_model="gpt-5.5",
        reasoning_effort="high",
        base_url="https://mikuapi.org/v1",
        wire_api="responses",
        disable_response_storage=True,
        requires_openai_auth=True,
        structured_outputs=True,
        api_key="test-key",
        timeout_seconds=60,
    )

    payload = _responses_payload(prompt="test", model=settings.model, settings=settings)

    assert payload["store"] is False
    assert payload["reasoning"] == {"effort": "high"}
    response_format = payload["text"]["format"]
    assert response_format["type"] == "json_schema"
    assert response_format["name"] == "finevision_llm_assistance"
    assert response_format["strict"] is True
    assert response_format["schema"]["additionalProperties"] is False
    assert set(response_format["schema"]["required"]) == {
        "summary",
        "inspection_notes",
        "suggested_actions",
        "risk_flags",
        "confidence",
    }


def test_responses_payload_can_disable_structured_outputs_for_compatibility() -> None:
    settings = LLMSettings(
        provider="OpenAI",
        model="gpt-5.5",
        review_model="gpt-5.5",
        reasoning_effort="high",
        base_url="https://mikuapi.org/v1",
        wire_api="responses",
        disable_response_storage=True,
        requires_openai_auth=True,
        structured_outputs=False,
        api_key="test-key",
        timeout_seconds=60,
    )

    payload = _responses_payload(prompt="test", model=settings.model, settings=settings)

    assert "text" not in payload

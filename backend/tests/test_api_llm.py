from __future__ import annotations

from finevision.api.llm import LLMSettings, _dataset_card_response_format, _responses_payload


def test_responses_payload_uses_strict_json_schema_format() -> None:
    settings = LLMSettings(
        provider="OpenAI",
        model="gpt-5.5",
        review_model="gpt-5.5",
        fallback_models=(),
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
        "holistic_analysis",
        "final_category_suggestion",
        "inspection_notes",
        "suggested_actions",
        "risk_flags",
        "confidence",
    }
    assert "holistic_analysis" in response_format["schema"]["properties"]
    assert "final_category_suggestion" in response_format["schema"]["properties"]


def test_responses_payload_can_disable_structured_outputs_for_compatibility() -> None:
    settings = LLMSettings(
        provider="OpenAI",
        model="gpt-5.5",
        review_model="gpt-5.5",
        fallback_models=(),
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


def test_responses_payload_can_attach_uploaded_image_pixels() -> None:
    settings = LLMSettings(
        provider="OpenAI",
        model="gpt-5.5",
        review_model="gpt-5.5",
        fallback_models=(),
        reasoning_effort="high",
        base_url="https://mikuapi.org/v1",
        wire_api="responses",
        disable_response_storage=True,
        requires_openai_auth=True,
        structured_outputs=True,
        api_key="test-key",
        timeout_seconds=60,
    )

    payload = _responses_payload(
        prompt="inspect this image",
        model=settings.model,
        settings=settings,
        image_data_urls=["data:image/png;base64,AAAA"],
    )

    content = payload["input"][0]["content"]
    assert content[0] == {"type": "input_text", "text": "inspect this image"}
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/png;base64,")


def test_dataset_card_response_format_uses_strict_json_schema() -> None:
    response_format = _dataset_card_response_format()

    assert response_format["type"] == "json_schema"
    assert response_format["name"] == "finevision_dataset_card"
    assert response_format["strict"] is True
    schema = response_format["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "task",
        "domain",
        "summary",
        "known_confusions",
        "ood_policy",
        "review_guidance",
    }

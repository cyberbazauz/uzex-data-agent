from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.agent.llm_adapter import LLMPlannerAdapter
from app.config import Settings
from app.domain.errors import UnverifiedQuery
from app.language import detect_language

BASE_PAYLOAD: dict[str, Any] = {
    "schema_version": "1.0",
    "decision": "EXECUTE",
    "language": "ru",
    "script": "cyrillic",
    "intent": "count",
    "subject": "deals",
    "metric": "deal_count",
    "filters": [
        {
            "field": "deal_date",
            "operator": "between",
            "value": ["2024-01-01", "2024-12-31"],
        }
    ],
    "group_by": [],
    "order_by": [],
    "limit": None,
    "currency_mode": "not_applicable",
    "entity_candidates": [],
    "clarification": None,
    "comparison": None,
}


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "llm_api_key": "test-secret-key",
        "llm_model": "test-model",
        "llm_base_url": "https://llm.example/v1",
        "llm_retry_once": True,
    }
    values.update(overrides)
    return Settings(**values)


def llm_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


def payload_text(**overrides: object) -> str:
    payload = json.loads(json.dumps(BASE_PAYLOAD))
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class Recorder:
    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected extra LLM request")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def make_adapter(
    recorder: Recorder, settings: Settings | None = None
) -> LLMPlannerAdapter:
    return LLMPlannerAdapter(
        settings or make_settings(),
        transport=httpx.MockTransport(recorder),
    )


def test_valid_plan_is_parsed_as_strict_model() -> None:
    recorder = Recorder([llm_response(payload_text())])
    adapter = make_adapter(recorder)

    plan = adapter.plan(
        "Сколько сделок было в 2024 году?", detect_language("Сколько сделок было в 2024 году?")
    )

    assert plan.intent.value == "count"
    assert plan.metric.value == "deal_count"
    assert plan.filters[0].value == ["2024-01-01", "2024-12-31"]
    assert len(recorder.requests) == 1


def test_request_has_no_tools_and_keeps_key_out_of_body() -> None:
    recorder = Recorder([llm_response(payload_text())])
    adapter = make_adapter(recorder)
    question = "Сколько сделок было в 2024 году?"
    adapter.plan(question, detect_language(question))

    request = recorder.requests[0]
    body_text = request.content.decode("utf-8")
    body = json.loads(body_text)

    assert request.headers["authorization"] == "Bearer test-secret-key"
    assert "tools" not in body
    assert "tool_choice" not in body
    assert "functions" not in body
    assert "test-secret-key" not in body_text
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert "JSON Schema" in body["messages"][0]["content"]
    assert "UNSUPPORTED" in body["messages"][0]["content"]
    assert question in body["messages"][1]["content"]


def test_one_retry_recovers_from_markdown_wrapped_json() -> None:
    recorder = Recorder(
        [
            llm_response("```json\n" + payload_text() + "\n```"),
            llm_response(payload_text()),
        ]
    )
    adapter = make_adapter(recorder)

    plan = adapter.plan("Сколько сделок было в 2024 году?", detect_language("вопрос"))
    assert plan.intent.value == "count"
    assert len(recorder.requests) == 2


def test_two_invalid_answers_raise_unverified() -> None:
    recorder = Recorder([llm_response("not json"), llm_response("still not json")])
    adapter = make_adapter(recorder)

    with pytest.raises(UnverifiedQuery):
        adapter.plan("Сколько сделок было в 2024 году?", detect_language("вопрос"))
    assert len(recorder.requests) == 2


def test_invented_sql_field_is_rejected() -> None:
    recorder = Recorder(
        [
            llm_response(payload_text(sql="SELECT * FROM deals")),
            llm_response(payload_text(sql="SELECT * FROM deals")),
        ]
    )
    adapter = make_adapter(recorder)

    with pytest.raises(UnverifiedQuery):
        adapter.plan("Сколько сделок было в 2024 году?", detect_language("вопрос"))
    assert len(recorder.requests) == 2


def test_invented_answer_and_numbers_are_rejected() -> None:
    recorder = Recorder(
        [
            llm_response(payload_text(answer="106647")),
            llm_response(payload_text(result=106647)),
        ]
    )
    adapter = make_adapter(recorder)

    with pytest.raises(UnverifiedQuery):
        adapter.plan("Сколько сделок было в 2024 году?", detect_language("вопрос"))


def test_invalid_enum_is_rejected_and_retried() -> None:
    recorder = Recorder(
        [
            llm_response(payload_text(intent="run_sql")),
            llm_response(payload_text()),
        ]
    )
    adapter = make_adapter(recorder)

    plan = adapter.plan("Сколько сделок было в 2024 году?", detect_language("вопрос"))
    assert plan.intent.value == "count"
    assert len(recorder.requests) == 2


def test_transport_error_is_retried_once() -> None:
    recorder = Recorder(
        [
            httpx.ConnectError("temporary failure"),
            llm_response(payload_text()),
        ]
    )
    adapter = make_adapter(recorder)

    plan = adapter.plan("Сколько сделок было в 2024 году?", detect_language("вопрос"))
    assert plan.intent.value == "count"
    assert len(recorder.requests) == 2


def test_two_transport_errors_raise_unverified() -> None:
    recorder = Recorder(
        [
            httpx.ConnectError("temporary failure"),
            httpx.ReadTimeout("timeout"),
        ]
    )
    adapter = make_adapter(recorder)

    with pytest.raises(UnverifiedQuery):
        adapter.plan("Сколько сделок было в 2024 году?", detect_language("вопрос"))
    assert len(recorder.requests) == 2


def test_no_retry_when_disabled() -> None:
    recorder = Recorder([llm_response("not json")])
    adapter = make_adapter(recorder, make_settings(llm_retry_once=False))

    with pytest.raises(UnverifiedQuery):
        adapter.plan("Сколько сделок было в 2024 году?", detect_language("вопрос"))
    assert len(recorder.requests) == 1


def test_missing_configuration_raises_unverified_without_call() -> None:
    recorder = Recorder([])
    adapter = make_adapter(recorder, make_settings(llm_api_key="", llm_model=""))

    with pytest.raises(UnverifiedQuery):
        adapter.plan("Сколько сделок было в 2024 году?", detect_language("вопрос"))
    assert recorder.requests == []


def test_server_detection_overrides_language_and_script() -> None:
    recorder = Recorder(
        [llm_response(payload_text(language="uz", script="latin"))]
    )
    adapter = make_adapter(recorder)
    detection = detect_language("Сколько сделок было в 2024 году?")

    plan = adapter.plan("Сколько сделок было в 2024 году?", detection)
    assert plan.language is detection.language
    assert plan.script is detection.script


def test_empty_choices_envelope_raises_unverified() -> None:
    empty = httpx.Response(200, json={"choices": []})
    recorder = Recorder([empty, empty])
    adapter = make_adapter(recorder)

    with pytest.raises(UnverifiedQuery):
        adapter.plan("Сколько сделок было в 2024 году?", detect_language("вопрос"))


def test_http_error_status_raises_unverified() -> None:
    error = httpx.Response(500, json={"error": "boom"})
    recorder = Recorder([error, error])
    adapter = make_adapter(recorder)

    with pytest.raises(UnverifiedQuery):
        adapter.plan("Сколько сделок было в 2024 году?", detect_language("вопрос"))


def test_describe_never_exposes_api_key() -> None:
    adapter = make_adapter(Recorder([]))
    described = adapter.describe()
    assert "test-secret-key" not in json.dumps(described)
    assert described["model"] == "test-model"

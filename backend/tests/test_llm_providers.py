from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.agent.llm_adapter import (
    FailoverPlanner,
    GeminiPlannerAdapter,
    GroqPlannerAdapter,
    ProviderTechnicalError,
    gemini_response_json_schema,
    gemini_response_schema,
    openai_strict_schema,
)
from app.agent.planner import LocalPlannerAdapter, build_planner
from app.agent.query_plan import QueryPlan
from app.config import Settings
from app.domain.errors import UnverifiedQuery
from app.language import detect_language

QUESTION = "Сколько сделок было в 2024 году?"

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
        "planner_mode": "llm",
        "llm_provider": "gemini",
        "llm_api_key": "generic-secret",
        "llm_model": "generic-model",
        "llm_retry_once": True,
        "gemini_api_key": "gemini-secret-key",
        "gemini_model": "gemini-test-flash",
        "gemini_base_url": "https://gemini.example/v1beta",
        "groq_api_key": "groq-secret-key",
        "groq_model": "openai/gpt-oss-120b",
        "groq_base_url": "https://groq.example/openai/v1",
    }
    values.update(overrides)
    return Settings(**values)


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


def gemini_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {"content": {"parts": [{"text": content}], "role": "model"}}
            ]
        },
    )


def openai_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


def gemini_adapter(
    recorder: Recorder, settings: Settings | None = None
) -> GeminiPlannerAdapter:
    return GeminiPlannerAdapter(
        settings or make_settings(),
        transport=httpx.MockTransport(recorder),
    )


def groq_adapter(
    recorder: Recorder, settings: Settings | None = None
) -> GroqPlannerAdapter:
    return GroqPlannerAdapter(
        settings or make_settings(),
        transport=httpx.MockTransport(recorder),
    )


# ---------------------------------------------------------------------------
# Gemini native adapter
# ---------------------------------------------------------------------------


def test_gemini_uses_native_structured_output_and_no_tools() -> None:
    recorder = Recorder([gemini_response(payload_text())])
    adapter = gemini_adapter(recorder)

    plan = adapter.plan(QUESTION, detect_language(QUESTION))

    assert plan.intent.value == "count"
    request = recorder.requests[0]
    assert request.url.path.endswith(
        "/models/gemini-test-flash:generateContent"
    )
    assert request.headers["x-goog-api-key"] == "gemini-secret-key"
    body = json.loads(request.content.decode("utf-8"))
    body_text = request.content.decode("utf-8")
    assert "gemini-secret-key" not in body_text
    assert "tools" not in body
    assert "toolConfig" not in body
    assert "functionDeclarations" not in body_text
    assert body["generationConfig"]["temperature"] == 0
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseJsonSchema" in body["generationConfig"]
    system_text = body["systemInstruction"]["parts"][0]["text"]
    assert system_text
    # The schema is transmitted structurally; the prompt must not duplicate the
    # full JSON Schema (token/rate-limit waste).
    assert "\"$defs\"" not in system_text
    assert "JSON Schema" in system_text
    assert QUESTION in body["contents"][0]["parts"][0]["text"]


def test_gemini_response_schema_mode_is_supported() -> None:
    recorder = Recorder([gemini_response(payload_text())])
    adapter = gemini_adapter(
        recorder,
        make_settings(gemini_schema_mode="response_schema"),
    )
    adapter.plan(QUESTION, detect_language(QUESTION))

    config = json.loads(recorder.requests[0].content)["generationConfig"]
    assert "responseSchema" in config
    assert "responseJsonSchema" not in config


def test_gemini_retries_invalid_json_then_fails_closed() -> None:
    recorder = Recorder(
        [gemini_response("not json"), gemini_response("still not json")]
    )
    adapter = gemini_adapter(recorder)

    with pytest.raises(UnverifiedQuery) as exc:
        adapter.plan(QUESTION, detect_language(QUESTION))
    assert not isinstance(exc.value, ProviderTechnicalError)
    assert len(recorder.requests) == 2


def test_gemini_http_failure_is_a_provider_technical_error() -> None:
    error = httpx.Response(429, json={"error": {"message": "rate limit"}})
    recorder = Recorder([error, error])
    adapter = gemini_adapter(recorder)

    with pytest.raises(ProviderTechnicalError):
        adapter.plan(QUESTION, detect_language(QUESTION))


def test_gemini_missing_configuration_does_not_call_provider() -> None:
    recorder = Recorder([])
    adapter = gemini_adapter(
        recorder, make_settings(gemini_api_key="", gemini_model="")
    )
    with pytest.raises(UnverifiedQuery):
        adapter.plan(QUESTION, detect_language(QUESTION))
    assert recorder.requests == []


def test_gemini_invented_answer_field_is_rejected() -> None:
    recorder = Recorder(
        [
            gemini_response(payload_text(answer="106647")),
            gemini_response(payload_text(answer="106647")),
        ]
    )
    adapter = gemini_adapter(recorder)
    with pytest.raises(UnverifiedQuery):
        adapter.plan(QUESTION, detect_language(QUESTION))


def test_gemini_server_owns_language_and_script() -> None:
    recorder = Recorder([gemini_response(payload_text(language="uz", script="latin"))])
    adapter = gemini_adapter(recorder)
    detection = detect_language(QUESTION)
    plan = adapter.plan(QUESTION, detection)
    assert plan.language is detection.language
    assert plan.script is detection.script


# ---------------------------------------------------------------------------
# Groq strict structured output
# ---------------------------------------------------------------------------


def test_groq_uses_strict_json_schema_with_the_shared_query_plan() -> None:
    recorder = Recorder([openai_response(payload_text())])
    adapter = groq_adapter(recorder)

    plan = adapter.plan(QUESTION, detect_language(QUESTION))

    assert plan.decision.value == "EXECUTE"
    request = recorder.requests[0]
    assert str(request.url) == "https://groq.example/openai/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer groq-secret-key"
    body = json.loads(request.content)
    assert body["model"] == "openai/gpt-oss-120b"
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert "decision" in schema["properties"]
    assert set(schema["required"]) == set(schema["properties"])
    assert "sql" not in schema["properties"]
    assert "answer" not in schema["properties"]


def test_groq_http_failure_is_technical_but_invalid_plan_is_not() -> None:
    error = httpx.Response(503, json={"error": "unavailable"})
    with pytest.raises(ProviderTechnicalError):
        groq_adapter(Recorder([error, error])).plan(
            QUESTION, detect_language(QUESTION)
        )

    invalid = Recorder(
        [openai_response('{"answer": 1}'), openai_response('{"answer": 1}')]
    )
    with pytest.raises(UnverifiedQuery) as exc:
        groq_adapter(invalid).plan(QUESTION, detect_language(QUESTION))
    assert not isinstance(exc.value, ProviderTechnicalError)


def test_groq_json_validate_failed_retries_the_same_request_once() -> None:
    rejected = httpx.Response(
        400,
        json={
            "error": {
                "type": "invalid_request_error",
                "code": "json_validate_failed",
                "message": "Failed to validate JSON.",
            }
        },
    )
    recorder = Recorder([rejected, openai_response(payload_text())])

    plan = groq_adapter(recorder).plan(QUESTION, detect_language(QUESTION))

    assert plan.intent.value == "count"
    assert len(recorder.requests) == 2
    assert recorder.requests[0].content == recorder.requests[1].content


# ---------------------------------------------------------------------------
# Failover policy
# ---------------------------------------------------------------------------


def test_failover_switches_on_technical_provider_failure() -> None:
    primary_recorder = Recorder(
        [httpx.ConnectError("down"), httpx.ConnectError("down")]
    )
    fallback_recorder = Recorder([openai_response(payload_text())])
    planner = FailoverPlanner(
        gemini_adapter(primary_recorder), groq_adapter(fallback_recorder)
    )

    plan = planner.plan(QUESTION, detect_language(QUESTION))

    assert plan.intent.value == "count"
    assert len(primary_recorder.requests) == 2
    assert len(fallback_recorder.requests) == 1


def test_failover_does_not_switch_after_invalid_semantic_plan() -> None:
    primary_recorder = Recorder(
        [gemini_response("not json"), gemini_response("not json")]
    )
    fallback_recorder = Recorder([])
    planner = FailoverPlanner(
        gemini_adapter(primary_recorder), groq_adapter(fallback_recorder)
    )

    with pytest.raises(UnverifiedQuery) as exc:
        planner.plan(QUESTION, detect_language(QUESTION))
    assert not isinstance(exc.value, ProviderTechnicalError)
    assert fallback_recorder.requests == []


def test_failover_does_not_switch_when_primary_returns_valid_plan() -> None:
    primary_recorder = Recorder([gemini_response(payload_text(decision="CLARIFY", clarification={"code": "PARTICIPANT_ROLE_AMBIGUOUS", "options": []}, intent="count", subject="deals", metric="deal_count", filters=[]))])
    fallback_recorder = Recorder([])
    planner = FailoverPlanner(
        gemini_adapter(primary_recorder), groq_adapter(fallback_recorder)
    )

    plan = planner.plan(QUESTION, detect_language(QUESTION))
    assert plan.decision.value == "CLARIFY"
    assert fallback_recorder.requests == []


def test_failover_uses_same_provider_with_a_different_org_scope() -> None:
    quota = httpx.Response(
        429,
        headers={"retry-after": "3600"},
        json={"error": {"type": "tokens", "code": "rate_limit_exceeded"}},
    )
    primary_recorder = Recorder([quota])
    fallback_recorder = Recorder([openai_response(payload_text())])
    settings = make_settings()
    planner = FailoverPlanner(
        groq_adapter(primary_recorder, settings),
        GroqPlannerAdapter(
            settings,
            transport=httpx.MockTransport(fallback_recorder),
            credential_entries=[("org-b", "org-b-secret")],
            scope_ids=("org-b", "org-b-project"),
        ),
    )

    plan = planner.plan(QUESTION, detect_language(QUESTION))

    assert plan.intent.value == "count"
    assert planner.last_used == "fallback"
    assert len(primary_recorder.requests) == 1


def test_short_429_fails_over_to_an_independent_org_scope() -> None:
    short = httpx.Response(
        429,
        headers={"retry-after": "5"},
        json={"error": {"type": "tokens", "code": "rate_limit_exceeded"}},
    )
    primary_recorder = Recorder([short, short])
    fallback_recorder = Recorder([openai_response(payload_text())])
    settings = make_settings(llm_inline_cooldown_wait_seconds=0)
    planner = FailoverPlanner(
        groq_adapter(primary_recorder, settings),
        GroqPlannerAdapter(
            settings,
            transport=httpx.MockTransport(fallback_recorder),
            credential_entries=[("org-b", "org-b-secret")],
            scope_ids=("org-b", "org-b-project"),
        ),
    )

    plan = planner.plan(QUESTION, detect_language(QUESTION))

    assert plan.intent.value == "count"
    assert planner.last_used == "fallback"
    assert len(primary_recorder.requests) == 1
    assert len(fallback_recorder.requests) == 1


def test_short_429_refuses_the_same_org_scope() -> None:
    short = httpx.Response(
        429,
        headers={"retry-after": "5"},
        json={"error": {"type": "tokens", "code": "rate_limit_exceeded"}},
    )
    primary_recorder = Recorder([short, short])
    fallback_recorder = Recorder([])
    settings = make_settings(llm_inline_cooldown_wait_seconds=0)
    planner = FailoverPlanner(
        groq_adapter(primary_recorder, settings),
        GroqPlannerAdapter(
            settings,
            transport=httpx.MockTransport(fallback_recorder),
            credential_entries=[("same-org", "same-org-secret")],
            scope_ids=(
                settings.groq_organization_scope_id,
                settings.groq_project_scope_id,
            ),
        ),
    )

    with pytest.raises(ProviderTechnicalError):
        planner.plan(QUESTION, detect_language(QUESTION))

    assert planner.last_used is None
    assert fallback_recorder.requests == []


def test_failover_refuses_same_provider_in_the_same_org_scope() -> None:
    quota = httpx.Response(
        429,
        headers={"retry-after": "3600"},
        json={"error": {"type": "tokens", "code": "rate_limit_exceeded"}},
    )
    primary_recorder = Recorder([quota])
    fallback_recorder = Recorder([])
    settings = make_settings()
    planner = FailoverPlanner(
        groq_adapter(primary_recorder, settings),
        GroqPlannerAdapter(
            settings,
            transport=httpx.MockTransport(fallback_recorder),
            credential_entries=[("same-org", "same-org-secret")],
            scope_ids=(
                settings.groq_organization_scope_id,
                settings.groq_project_scope_id,
            ),
        ),
    )

    with pytest.raises(ProviderTechnicalError):
        planner.plan(QUESTION, detect_language(QUESTION))
    assert planner.last_used is None
    assert fallback_recorder.requests == []


def test_long_429_fails_over_to_a_different_model_in_the_same_org() -> None:
    quota = httpx.Response(
        429,
        headers={"retry-after": "3600"},
        json={"error": {"type": "tokens", "code": "rate_limit_exceeded"}},
    )
    primary_recorder = Recorder([quota])
    fallback_recorder = Recorder([openai_response(payload_text())])
    settings = make_settings()
    fallback_settings = settings.model_copy(
        update={"groq_model": "qwen/qwen3.8-27b"}
    )
    planner = FailoverPlanner(
        groq_adapter(primary_recorder, settings),
        GroqPlannerAdapter(
            fallback_settings,
            transport=httpx.MockTransport(fallback_recorder),
        ),
    )

    plan = planner.plan(QUESTION, detect_language(QUESTION))

    assert plan.intent.value == "count"
    assert planner.last_used == "fallback"
    assert len(primary_recorder.requests) == 1
    assert len(fallback_recorder.requests) == 1


def test_model_fallback_chain_uses_primary_provider_and_keys() -> None:
    settings = make_settings(
        llm_provider="groq",
        llm_primary_provider="groq",
        llm_primary_model="openai/gpt-oss-120b",
        groq_model="openai/gpt-oss-120b",
        llm_model_fallback_chain="qwen/qwen3.8-27b",
    )

    assert settings.model_fallback_chain() == [("groq", "qwen/qwen3.8-27b")]

    planner = build_planner(settings)
    assert isinstance(planner, FailoverPlanner)
    described = planner.describe()
    assert described["fallback"]["model"] == "qwen/qwen3.8-27b"
    assert (
        described["fallback"]["credential_scope"]["organization_scope_id"]
        == settings.groq_organization_scope_id
    )


def test_qwen_model_uses_instruct_mode_request_fields() -> None:
    recorder = Recorder([openai_response(payload_text())])
    settings = make_settings(groq_model="qwen/qwen3.8-27b")
    planner = GroqPlannerAdapter(
        settings, transport=httpx.MockTransport(recorder)
    )

    planner.plan(QUESTION, detect_language(QUESTION))

    body = json.loads(recorder.requests[0].content)
    assert body["model"] == "qwen/qwen3.8-27b"
    assert body["reasoning_effort"] == "none"
    assert body["temperature"] == 0.7


def test_primary_model_request_has_no_qwen_fields() -> None:
    recorder = Recorder([openai_response(payload_text())])
    settings = make_settings(groq_model="openai/gpt-oss-120b")
    planner = GroqPlannerAdapter(
        settings, transport=httpx.MockTransport(recorder)
    )

    planner.plan(QUESTION, detect_language(QUESTION))

    body = json.loads(recorder.requests[0].content)
    assert "reasoning_effort" not in body
    assert body["temperature"] == 0


def test_parse_accepts_a_textual_between_range() -> None:
    payload = json.loads(payload_text())
    payload["filters"][0]["value"] = "2024-01-01/2024-12-31"

    from app.agent.llm_adapter import _parse_plan

    plan = _parse_plan(
        json.dumps(payload, ensure_ascii=False), detect_language(QUESTION)
    )

    assert [str(value) for value in plan.filters[0].value] == [
        "2024-01-01",
        "2024-12-31",
    ]


def test_parse_normalizes_filter_list_metric() -> None:
    # "покажи записи по 2024" is a record list; some models emit the general
    # metric sentinel.  The wire-level repair adds no filter and no fact.
    payload = json.loads(payload_text())
    payload["decision"] = "EXECUTE"
    payload["intent"] = "filter_list"
    payload["subject"] = "deals"
    payload["metric"] = "not_applicable"
    payload["limit"] = None

    from app.agent.llm_adapter import _parse_plan

    plan = _parse_plan(
        json.dumps(payload, ensure_ascii=False), detect_language(QUESTION)
    )

    assert plan.intent.value == "filter_list"
    assert plan.metric.value == "record_lookup"


def test_parse_keeps_clarify_with_general_sentinels() -> None:
    payload = {
        "schema_version": "1.0",
        "decision": "CLARIFY",
        "language": "ru",
        "script": "cyrillic",
        "intent": "general",
        "subject": "not_applicable",
        "metric": "not_applicable",
        "filters": [],
        "group_by": [],
        "order_by": [],
        "limit": None,
        "currency_mode": "not_applicable",
        "entity_candidates": [],
        "clarification": {"code": "PERIOD_AMBIGUOUS", "options": []},
        "comparison": None,
        "explanation_requested": False,
    }

    from app.agent.llm_adapter import _parse_plan

    plan = _parse_plan(
        json.dumps(payload, ensure_ascii=False), detect_language(QUESTION)
    )

    assert plan.decision.value == "CLARIFY"
    assert plan.clarification is not None
    assert plan.clarification.code.value == "PERIOD_AMBIGUOUS"



def test_build_planner_wires_cross_org_groq_failover() -> None:
    settings = make_settings(
        llm_provider="groq",
        groq_organization_scope_id="org-a",
        groq_project_scope_id="project-a",
        groq_failover_api_keys="failover-secret-1,failover-secret-2",
        groq_failover_organization_scope_id="org-b",
        groq_failover_project_scope_id="project-b",
    )
    planner = build_planner(settings)

    assert isinstance(planner, FailoverPlanner)
    described = planner.describe()
    assert described["same_provider_rate_limit_failover"] is True
    assert (
        described["fallback"]["credential_scope"]["organization_scope_id"]
        == "org-b"
    )


def test_build_planner_ignores_failover_keys_in_the_same_scope() -> None:
    settings = make_settings(
        llm_provider="groq",
        groq_failover_api_keys="failover-secret-1",
        groq_failover_organization_scope_id="groq-default-org",
        groq_failover_project_scope_id="groq-default-project",
    )

    assert isinstance(build_planner(settings), GroqPlannerAdapter)


def test_build_planner_selects_gemini_with_groq_failover() -> None:
    planner = build_planner(make_settings())
    assert isinstance(planner, FailoverPlanner)
    described = planner.describe()
    assert described["primary"]["provider"] == "gemini"
    assert described["fallback"]["provider"] == "groq"
    assert described["fallback_policy"] == "technical_provider_error_only"


def test_build_planner_without_groq_key_returns_gemini_only() -> None:
    planner = build_planner(make_settings(groq_api_key=""))
    assert isinstance(planner, GeminiPlannerAdapter)


def test_build_planner_local_mode_is_unchanged() -> None:
    planner = build_planner(make_settings(planner_mode="local"))
    assert isinstance(planner, LocalPlannerAdapter)


def test_describe_never_exposes_any_api_key() -> None:
    planner = build_planner(make_settings())
    serialized = json.dumps(planner.describe())
    assert "gemini-secret-key" not in serialized
    assert "groq-secret-key" not in serialized
    assert "generic-secret" not in serialized


# ---------------------------------------------------------------------------
# Shared schema equivalence
# ---------------------------------------------------------------------------


def test_all_provider_schemas_derive_from_one_pydantic_model() -> None:
    model_schema = QueryPlan.model_json_schema()
    expected_fields = set(model_schema["properties"])

    gemini_json = gemini_response_json_schema()
    gemini_legacy = gemini_response_schema()
    groq = openai_strict_schema()

    assert set(gemini_json["properties"]) == expected_fields
    assert set(gemini_legacy["properties"]) == expected_fields
    assert set(groq["properties"]) == expected_fields
    # Every provider schema still forbids invented fields.
    for schema in (gemini_json, gemini_legacy, groq):
        assert "sql" not in schema["properties"]
        assert "answer" not in schema["properties"]
        assert "result" not in schema["properties"]
    assert gemini_json["properties"]["decision"]["enum"] == [
        "EXECUTE",
        "CLARIFY",
        "UNSUPPORTED",
        "GENERAL",
    ]

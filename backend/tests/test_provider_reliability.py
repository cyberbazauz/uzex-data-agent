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
)
from app.agent.planner import build_planner
from app.config import Settings
from app.language import detect_language
from app.llm.circuit_breaker import BreakerState, CircuitBreaker, CircuitBreakerRegistry
from app.llm.credential_registry import Credential, CredentialRegistry, CredentialState
from app.llm.error_classifier import (
    ProviderFailure,
    ProviderFailureKind,
    classify_http_failure,
    parse_rate_headers,
)
from app.llm.provider_manager import ProviderManager, build_credential_registry

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


def payload_text(**overrides: object) -> str:
    payload = json.loads(json.dumps(BASE_PAYLOAD))
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def openai_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


def gemini_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {"content": {"parts": [{"text": content}], "role": "model"}}
            ]
        },
    )


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


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "planner_mode": "llm",
        "llm_provider": "groq",
        "llm_retry_once": True,
        "groq_api_key": "groq-primary-secret",
        "groq_api_key_secondary_same_org": "groq-secondary-secret",
        "groq_model": "openai/gpt-oss-120b",
        "groq_base_url": "https://groq.example/openai/v1",
        "llm_inline_cooldown_wait_seconds": 0,
        "llm_max_concurrent_requests": 4,
    }
    values.update(overrides)
    return Settings(**values)


def groq_adapter(recorder: Recorder, settings: Settings | None = None) -> GroqPlannerAdapter:
    return GroqPlannerAdapter(
        settings or make_settings(), transport=httpx.MockTransport(recorder)
    )


# ---------------------------------------------------------------------------
# Header parsing / classification
# ---------------------------------------------------------------------------


def test_rate_headers_are_parsed_without_inventing_durations() -> None:
    rate = parse_rate_headers(
        {
            "retry-after": "1m30s",
            "x-ratelimit-reset-tokens": "250ms",
            "x-ratelimit-remaining-tokens": "0",
            "x-ratelimit-limit-tokens": "8000",
        }
    )
    assert rate.retry_after_seconds == 90.0
    assert rate.reset_tokens_seconds == 0.25
    assert rate.remaining_tokens == 0
    assert rate.limit_tokens == 8000

    missing = parse_rate_headers({})
    assert missing.retry_after_seconds is None
    assert missing.reset_seconds is None


def test_429_without_reset_is_long_and_not_retryable() -> None:
    failure = classify_http_failure(
        provider="groq", model="openai/gpt-oss-120b", status_code=429
    )
    assert failure.kind is ProviderFailureKind.RATE_LIMIT_LONG
    assert failure.retryable is False
    assert failure.fallback_allowed is True


def test_400_is_configuration_error_without_fallback() -> None:
    failure = classify_http_failure(
        provider="groq", model="openai/gpt-oss-120b", status_code=400
    )
    assert failure.kind is ProviderFailureKind.INVALID_REQUEST
    assert failure.fallback_allowed is False
    assert failure.retryable is False


def test_json_validate_failed_is_retried_but_never_fails_over() -> None:
    failure = classify_http_failure(
        provider="groq",
        model="openai/gpt-oss-120b",
        status_code=400,
        error_code="json_validate_failed",
    )
    assert failure.kind is ProviderFailureKind.INVALID_REQUEST
    # The same request can succeed on a second attempt; another model must
    # never be used to hide a schema rejection.
    assert failure.retryable is True
    assert failure.fallback_allowed is False


# ---------------------------------------------------------------------------
# Credential registry: 401-only same-scope rotation
# ---------------------------------------------------------------------------


def test_registry_rotates_only_within_the_same_scope() -> None:
    registry = CredentialRegistry(
        [
            Credential(
                name="primary",
                provider="groq",
                api_key="secret-a",
                organization_scope_id="org-1",
                project_scope_id="project-1",
            ),
            Credential(
                name="same-org",
                provider="groq",
                api_key="secret-b",
                organization_scope_id="org-1",
                project_scope_id="project-1",
            ),
            Credential(
                name="other-org",
                provider="groq",
                api_key="secret-c",
                organization_scope_id="org-2",
                project_scope_id="project-2",
            ),
        ]
    )

    next_credential = registry.next_for_auth_failure("primary")
    assert next_credential is not None
    assert next_credential.name == "same-org"
    registry.mark_invalid("primary", failure_kind=ProviderFailureKind.AUTH_INVALID)
    assert registry.get("primary").state is CredentialState.INVALID
    # The other organization is never returned as a credential pool member.
    registry.mark_invalid("same-org", failure_kind=ProviderFailureKind.AUTH_INVALID)
    assert registry.next_for_auth_failure("same-org") is None


def test_registry_masked_snapshot_never_contains_the_secret() -> None:
    registry = build_credential_registry(
        provider="groq",
        entries=[("primary", "super-secret-one"), ("secondary", "super-secret-two")],
        organization_scope_id="org",
        project_scope_id="project",
    )
    serialized = json.dumps(registry.public_snapshot())
    assert "super-secret-one" not in serialized
    assert "super-secret-two" not in serialized
    assert len(registry.public_snapshot()) == 2


def test_adapter_rotates_on_401_and_keeps_the_secret_out_of_describe() -> None:
    recorder = Recorder([httpx.Response(401), openai_response(payload_text())])
    adapter = groq_adapter(recorder)

    plan = adapter.plan(QUESTION, detect_language(QUESTION))

    assert plan.intent.value == "count"
    assert len(recorder.requests) == 2
    assert recorder.requests[0].headers["authorization"] == "Bearer groq-primary-secret"
    assert recorder.requests[1].headers["authorization"] == "Bearer groq-secondary-secret"
    described = json.dumps(adapter.describe())
    assert "groq-primary-secret" not in described
    assert "groq-secondary-secret" not in described
    assert described.count("groq-") >= 1


# ---------------------------------------------------------------------------
# 429: no key rotation, no quota pooling
# ---------------------------------------------------------------------------


def test_429_never_rotates_to_another_key() -> None:
    rate_limit = httpx.Response(
        429, headers={"retry-after": "3600"}, json={"error": "quota"}
    )
    recorder = Recorder([rate_limit])
    adapter = groq_adapter(recorder)

    with pytest.raises(ProviderTechnicalError) as exc:
        adapter.plan(QUESTION, detect_language(QUESTION))

    assert exc.value.is_rate_limit
    assert exc.value.retry_allowed is False
    # The second key was never tried: quota belongs to the organization scope.
    assert len(recorder.requests) == 1
    assert recorder.requests[0].headers["authorization"] == "Bearer groq-primary-secret"
    reliability = adapter.describe()["reliability"]
    active = [
        item
        for item in reliability["credentials"]
        if item["state"] == "ACTIVE"
    ]
    assert len(active) == 2


def test_short_429_waits_for_reset_then_retries_the_same_key() -> None:
    responses = [
        httpx.Response(429, headers={"retry-after": "1"}, json={"error": "rpm"}),
        openai_response(payload_text()),
    ]
    recorder = Recorder(responses)
    adapter = groq_adapter(
        recorder,
        make_settings(llm_inline_cooldown_wait_seconds=5),
    )

    plan = adapter.plan(QUESTION, detect_language(QUESTION))

    assert plan.intent.value == "count"
    assert len(recorder.requests) == 2
    # Same credential: a short RPM window is a queue, not a reason to pool keys.
    assert recorder.requests[0].headers["authorization"] == "Bearer groq-primary-secret"
    assert recorder.requests[1].headers["authorization"] == "Bearer groq-primary-secret"


def test_provider_manager_sleeps_only_for_a_short_reset() -> None:
    registry = build_credential_registry(
        provider="groq",
        entries=[("primary", "secret-a"), ("secondary", "secret-b")],
        organization_scope_id="org",
        project_scope_id="project",
    )
    sleeps: list[float] = []
    manager = ProviderManager(
        provider="groq",
        model="openai/gpt-oss-120b",
        registry=registry,
        max_inline_wait_seconds=5,
        sleep=sleeps.append,
        rate_limit_short_max_seconds=60,
    )
    sent: list[str] = []
    responses = [
        httpx.Response(429, headers={"retry-after": "2"}),
        httpx.Response(200),
    ]

    def send(api_key: str) -> httpx.Response:
        sent.append(api_key)
        return responses.pop(0)

    assert manager.execute(send, lambda response: "ok") == "ok"
    assert sleeps == [2.0]
    assert sent == ["secret-a", "secret-a"]
    assert all(item.state is CredentialState.ACTIVE for item in registry.all())


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def test_breaker_opens_half_opens_and_closes() -> None:
    now = [0.0]
    breaker = CircuitBreaker(
        "groq:model:org:project",
        failure_threshold=2,
        recovery_seconds=10,
        clock=lambda: now[0],
    )
    assert breaker.allow() is True
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert breaker.allow() is False

    now[0] = 10.0
    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.allow() is True
    # Only one half-open canary is allowed.
    assert breaker.allow() is False
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow() is True


def test_open_breaker_blocks_the_provider_call() -> None:
    registry = build_credential_registry(
        provider="groq",
        entries=[("primary", "secret-a")],
        organization_scope_id="org",
        project_scope_id="project",
    )
    breakers = CircuitBreakerRegistry(failure_threshold=1, recovery_seconds=60)
    manager = ProviderManager(
        provider="groq",
        model="openai/gpt-oss-120b",
        registry=registry,
        breakers=breakers,
        sleep=lambda _: None,
    )
    calls: list[str] = []

    def failing(api_key: str) -> httpx.Response:
        calls.append(api_key)
        return httpx.Response(500)

    with pytest.raises(ProviderTechnicalError):
        manager.execute(failing, lambda response: "unused")

    with pytest.raises(ProviderTechnicalError) as exc:
        manager.execute(failing, lambda response: "unused")

    assert exc.value.retry_allowed is False
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Failover policy: independence and qualification
# ---------------------------------------------------------------------------


def test_long_429_does_not_fail_over_to_the_same_provider() -> None:
    primary_recorder = Recorder([httpx.Response(429, headers={"retry-after": "3600"})])
    fallback_recorder = Recorder([])
    planner = FailoverPlanner(
        groq_adapter(primary_recorder),
        groq_adapter(fallback_recorder),
    )

    with pytest.raises(ProviderTechnicalError):
        planner.plan(QUESTION, detect_language(QUESTION))
    assert fallback_recorder.requests == []


def test_long_429_fails_over_to_an_independent_provider() -> None:
    gemini_settings = make_settings(
        llm_provider="gemini",
        gemini_api_key="gemini-secret",
        gemini_model="gemini-test-flash",
        gemini_base_url="https://gemini.example/v1beta",
    )
    primary_recorder = Recorder([httpx.Response(429, json={"error": "quota"})])
    fallback_recorder = Recorder([openai_response(payload_text())])
    primary = GeminiPlannerAdapter(
        gemini_settings, transport=httpx.MockTransport(primary_recorder)
    )
    fallback = groq_adapter(fallback_recorder)
    planner = FailoverPlanner(primary, fallback)

    plan = planner.plan(QUESTION, detect_language(QUESTION))
    assert plan.intent.value == "count"
    assert planner.last_used == "fallback"
    assert len(fallback_recorder.requests) == 1


def test_invalid_request_does_not_fail_over() -> None:
    primary_recorder = Recorder([httpx.Response(400)])
    fallback_recorder = Recorder([])
    planner = FailoverPlanner(
        groq_adapter(primary_recorder),
        GeminiPlannerAdapter(
            make_settings(
                gemini_api_key="gemini-secret",
                gemini_model="gemini-test-flash",
                gemini_base_url="https://gemini.example/v1beta",
            ),
            transport=httpx.MockTransport(fallback_recorder),
        ),
    )

    with pytest.raises(ProviderTechnicalError):
        planner.plan(QUESTION, detect_language(QUESTION))
    assert fallback_recorder.requests == []


def test_unqualified_fallback_chain_is_not_built(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(llm_fallback_chain="groq:openai/gpt-oss-20b")
    monkeypatch.setattr("app.llm.qualification.is_qualified", lambda *_: False)
    planner = build_planner(settings)
    assert isinstance(planner, GroqPlannerAdapter)


def test_qualified_fallback_chain_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(
        llm_fallback_chain="groq:openai/gpt-oss-20b",
        llm_qualification_required=False,
    )
    planner = build_planner(settings)
    assert isinstance(planner, FailoverPlanner)


# ---------------------------------------------------------------------------
# Shared provider schema remains the only contract
# ---------------------------------------------------------------------------


def test_failure_object_never_contains_a_key() -> None:
    recorder = Recorder([httpx.Response(401)])
    adapter = groq_adapter(
        recorder, make_settings(groq_api_key_secondary_same_org="")
    )
    with pytest.raises(ProviderTechnicalError) as exc:
        adapter.plan(QUESTION, detect_language(QUESTION))
    serialized = json.dumps(exc.value.failure.public_dict())
    assert "groq-primary-secret" not in serialized


def test_provider_technical_error_diagnostics_are_sanitized() -> None:
    failure = ProviderFailure(
        kind=ProviderFailureKind.RATE_LIMIT_LONG,
        provider="groq",
        model="openai/gpt-oss-120b",
        status_code=429,
        message="Authorization: Bearer groq-primary-secret",
        retry_after_seconds=30.0,
        fallback_allowed=True,
        quota_scope="groq-org-b",
    )
    error = ProviderTechnicalError(
        "quota cooldown for groq-primary-secret",
        failure=failure,
        retry_allowed=False,
        wait_seconds=30.0,
    )

    diagnostics = error.diagnostics

    assert diagnostics["error_type"] == "ProviderTechnicalError"
    assert diagnostics["failure_kind"] == "rate_limit_long"
    assert diagnostics["provider"] == "groq"
    assert diagnostics["model"] == "openai/gpt-oss-120b"
    assert diagnostics["status_code"] == 429
    assert diagnostics["retry_after_seconds"] == 30.0
    assert diagnostics["quota_scope"] == "groq-org-b"
    assert diagnostics["retry_allowed"] is False
    assert diagnostics["wait_seconds"] == 30.0
    serialized = json.dumps(diagnostics)
    assert "groq-primary-secret" not in serialized
    assert "Authorization" not in serialized


def test_provider_technical_error_diagnostics_without_failure() -> None:
    diagnostics = ProviderTechnicalError("network down").diagnostics

    assert diagnostics["error_type"] == "ProviderTechnicalError"
    assert diagnostics["retry_allowed"] is True
    assert "failure_kind" not in diagnostics


def test_gemini_schema_remains_available_for_explicit_legacy_use() -> None:
    schema = gemini_response_json_schema()
    assert "decision" in schema["properties"]

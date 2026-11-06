from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(rate_limit: int = 100, **overrides: object) -> TestClient:
    settings = Settings(
        app_env="test",
        rate_limit_per_minute=rate_limit,
        cors_origins=["http://testserver"],
        **overrides,
    )
    return TestClient(create_app(settings=settings))


def test_health_is_public_and_hides_internals() -> None:
    with make_client() as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    serialized = json.dumps(body).lower()
    assert "password" not in serialized
    assert "database_url" not in serialized
    assert "llm_api_key" not in serialized


def test_ready_reports_database_schema_and_dataset() -> None:
    with make_client() as client:
        response = client.get("/api/v1/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"database": True, "dataset": True, "schema": True}
    assert body["dataset_cutoff"] == "2026-10-30"
    assert body["dataset_version"].startswith("sha256:")


def test_meta_returns_verified_counts_without_secrets() -> None:
    with make_client() as client:
        response = client.get("/api/v1/meta")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["total_deals"] == 500000
    assert body["contracts"] == 6616
    assert body["participants"] == 14392
    assert body["brokers"] == 120
    assert body["usd_rates"] == 1765
    assert body["supported_languages"] == ["ru", "uz"]
    assert body["dataset_cutoff"] == "2026-10-30"
    serialized = json.dumps(body).lower()
    assert "llm_api_key" not in serialized
    assert "database_url" not in serialized
    assert "password" not in serialized
    assert "secret" not in serialized


def test_examples_are_marked_as_examples_only() -> None:
    with make_client() as client:
        response = client.get("/api/v1/examples")

    assert response.status_code == 200
    body = response.json()
    assert body["examples"]
    assert all(item["is_example"] is True for item in body["examples"])
    assert "пример" in body["note"].lower()


def test_chat_found_response_has_data_and_evidence() -> None:
    with make_client() as client:
        response = client.post(
            "/api/v1/chat",
            json={"question": "Сколько сделок было в 2024 году?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FOUND"
    assert body["language"] == "ru"
    assert body["data"]["value"] == 106647
    assert body["evidence"]["matched_rows"] == 106647
    assert body["evidence"]["dataset_cutoff"] == "2026-10-30"
    assert body["clarification"] is None


def test_chat_not_found_response_has_no_data() -> None:
    with make_client() as client:
        response = client.post(
            "/api/v1/chat",
            json={"question": "Сколько сделок было в 2027 году?"},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "NOT_FOUND"
    assert body["data"] is None
    assert body["evidence"]["matched_rows"] == 0


def test_chat_clarification_response_has_no_evidence() -> None:
    with make_client() as client:
        response = client.post(
            "/api/v1/chat",
            json={"question": "Покажи брокера KAHIR"},
        )

    body = response.json()
    assert body["status"] == "NEEDS_CLARIFICATION"
    assert body["clarification"]["code"] == "BROKER_SIDE_AMBIGUOUS"
    assert body["data"] is None
    assert body["evidence"] is None


def test_chat_unsupported_has_no_fabricated_data() -> None:
    with make_client() as client:
        response = client.post(
            "/api/v1/chat",
            json={"question": "Спрогнозируй количество сделок в 2027 году"},
        )

    body = response.json()
    assert body["status"] == "UNSUPPORTED"
    assert body["data"] is None
    assert body["evidence"] is None


def test_blank_question_is_rejected() -> None:
    with make_client() as client:
        response = client.post("/api/v1/chat", json={"question": "   "})

    assert response.status_code == 422


def test_too_long_question_is_rejected() -> None:
    with make_client(max_question_length=20) as client:
        response = client.post("/api/v1/chat", json={"question": "а" * 21})

    assert response.status_code == 422


def test_unknown_fields_are_rejected() -> None:
    with make_client() as client:
        response = client.post(
            "/api/v1/chat",
            json={"question": "Сколько сделок?", "sql": "SELECT * FROM deals"},
        )

    assert response.status_code == 422


def test_request_id_header_is_echoed_and_sanitized() -> None:
    with make_client() as client:
        response = client.post(
            "/api/v1/chat",
            json={"question": "Сколько всего сделок?"},
            headers={"X-Request-ID": "client-123"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "client-123"
    assert response.json()["request_id"] == "client-123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"


def test_rate_limit_returns_429_with_retry_after() -> None:
    with make_client(rate_limit=2) as client:
        first = client.post("/api/v1/chat", json={"question": "Сколько всего сделок?"})
        second = client.post("/api/v1/chat", json={"question": "Сколько всего сделок?"})
        third = client.post("/api/v1/chat", json={"question": "Сколько всего сделок?"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert int(third.headers["Retry-After"]) >= 1


def test_cors_allows_configured_origin_only() -> None:
    with make_client() as client:
        allowed = client.options(
            "/api/v1/chat",
            headers={
                "Origin": "http://testserver",
                "Access-Control-Request-Method": "POST",
            },
        )
        denied = client.options(
            "/api/v1/chat",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://testserver"
    assert "access-control-allow-origin" not in denied.headers


def test_unexpected_failure_returns_safe_unverified_envelope() -> None:
    class BrokenOrchestrator:
        def chat(self, question, request_id=None):  # noqa: ANN001
            raise RuntimeError("internal secret detail must not leak")

    app = create_app(settings=Settings(app_env="test"), orchestrator=BrokenOrchestrator())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/chat",
            json={"question": "Сколько всего сделок?"},
        )

    assert response.status_code == 500
    body = response.json()
    assert body["status"] == "UNVERIFIED"
    assert body["data"] is None
    assert body["evidence"] is None
    assert "internal secret detail" not in json.dumps(body)

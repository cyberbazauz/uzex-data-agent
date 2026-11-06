from __future__ import annotations

import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from app.agent.local_planner import plan_question
from app.agent.orchestrator import Orchestrator
from app.domain.enums import ResponseStatus
from app.language import LanguageDetection

pytestmark = [pytest.mark.performance, pytest.mark.integration]

REPRESENTATIVE_QUESTIONS = [
    "Сколько сделок было в 2024 году?",
    "Сумма сделок в USD",
    "Динамика сделок по годам",
    "2024-yilda nechta bitim bo'ldi?",
    "2024-йилда нечта битим бўлди?",
]


def test_representative_requests_stay_within_budget() -> None:
    orchestrator = Orchestrator()
    # Warm-up: engine creation and statement cache are one-time costs.
    orchestrator.chat("Сколько всего сделок?")

    durations: list[float] = []
    for question in REPRESENTATIVE_QUESTIONS:
        started = time.perf_counter()
        response = orchestrator.chat(question)
        durations.append(time.perf_counter() - started)
        assert response.status in {
            ResponseStatus.FOUND,
            ResponseStatus.NOT_FOUND,
        }

    # Generous local-dataset budgets; the point is detecting pathological
    # blocking, not benchmarking a particular machine.
    assert max(durations) < 5.0, durations
    assert statistics.median(durations) < 2.0, durations


def test_concurrent_requests_all_complete_without_cross_talk() -> None:
    orchestrator = Orchestrator()
    orchestrator.chat("Сколько всего сделок?")

    def ask(question: str):
        return orchestrator.chat(question)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(ask, REPRESENTATIVE_QUESTIONS[index % len(REPRESENTATIVE_QUESTIONS)])
            for index in range(8)
        ]
        responses = [future.result(timeout=30) for future in futures]

    assert len(responses) == 8
    assert all(
        response.status in {ResponseStatus.FOUND, ResponseStatus.NOT_FOUND}
        for response in responses
    )
    assert all(response.request_id.startswith("req_") for response in responses)


def test_one_slow_request_does_not_block_another_user() -> None:
    class SlowPlanner:
        name = "slow-test"

        def plan(self, question: str, detection: LanguageDetection):
            if question.startswith("SLOW "):
                time.sleep(1.5)
                question = question.removeprefix("SLOW ")
            return plan_question(question, detection)

        def describe(self) -> dict[str, object]:
            return {"name": self.name}

    orchestrator = Orchestrator(planner=SlowPlanner())
    finished = threading.Event()
    slow_result: list[object] = []

    def slow_call() -> None:
        slow_result.append(
            orchestrator.chat("SLOW Сколько сделок было в 2024 году?")
        )
        finished.set()

    slow_thread = threading.Thread(target=slow_call, daemon=True)
    slow_thread.start()
    time.sleep(0.15)

    started = time.perf_counter()
    fast = orchestrator.chat("Сколько всего сделок?")
    fast_elapsed = time.perf_counter() - started

    assert fast.status is ResponseStatus.FOUND
    assert fast_elapsed < 1.0, fast_elapsed
    assert not finished.is_set(), "fast request waited for the slow one"

    slow_thread.join(timeout=10)
    assert finished.is_set()
    assert slow_result and slow_result[0].status is ResponseStatus.FOUND


def test_repeated_identical_questions_are_deterministic() -> None:
    orchestrator = Orchestrator()
    question = "Сколько сделок было в 2024 году?"
    responses = [orchestrator.chat(question) for _ in range(5)]

    assert {response.status for response in responses} == {ResponseStatus.FOUND}
    values = {response.data.value for response in responses if response.data}
    matched = {
        response.evidence.matched_rows for response in responses if response.evidence
    }
    assert values == {106647}
    assert matched == {106647}


def test_postgres_statement_timeout_maps_to_unverified() -> None:
    def timeout_executor(conn, execution, execution_id):  # noqa: ANN001
        conn.execute(text("SET LOCAL statement_timeout = '50ms'"))
        conn.execute(text("SELECT pg_sleep(0.5)"))
        raise AssertionError("the slow statement should have timed out")

    response = Orchestrator(executor=timeout_executor).chat(
        "Сколько сделок было в 2024 году?"
    )

    assert response.status is ResponseStatus.UNVERIFIED
    assert response.data is None
    assert response.evidence is None


def test_backend_restart_returns_the_same_verified_answer() -> None:
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    def ask_once() -> dict:
        with TestClient(create_app(settings=Settings(app_env="test"))) as client:
            response = client.post(
                "/api/v1/chat",
                json={"question": "Сколько сделок было в 2024 году?"},
            )
        assert response.status_code == 200
        return response.json()

    first = ask_once()
    second = ask_once()

    assert first["status"] == second["status"] == "FOUND"
    assert first["data"]["value"] == second["data"]["value"] == 106647
    assert (
        first["evidence"]["dataset_version"]
        == second["evidence"]["dataset_version"]
    )
    assert first["evidence"]["dataset_cutoff"] == second["evidence"]["dataset_cutoff"]

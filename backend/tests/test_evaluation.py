from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.domain.enums import ResponseStatus
from evaluation.run_evaluation import evaluate

QUESTIONS_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "questions.json"
QUESTIONS = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))


def test_evaluation_set_has_exactly_100_unique_questions() -> None:
    assert len(QUESTIONS) == 100
    ids = [item["id"] for item in QUESTIONS]
    assert len(ids) == len(set(ids))
    assert all(item["question"].strip() for item in QUESTIONS)


def test_evaluation_set_covers_every_question_reachable_status() -> None:
    # UNVERIFIED is an internal failure state (LLM/DB error), not something a
    # well-formed question should produce.  It is covered by dedicated failure
    # injection tests instead of the evaluation set.  GENERAL is a separate
    # conversational contract and is covered by test_general_mode.py because
    # the deterministic local planner can never emit it.
    expected = {item["expected_status"] for item in QUESTIONS}
    assert expected == {
        ResponseStatus.FOUND.value,
        ResponseStatus.NOT_FOUND.value,
        ResponseStatus.NEEDS_CLARIFICATION.value,
        ResponseStatus.UNSUPPORTED.value,
    }
    assert {item["language"] for item in QUESTIONS} == {"ru", "uz"}


def test_evaluation_set_covers_all_locales() -> None:
    locales = {item.get("locale", "ru") for item in QUESTIONS}
    assert locales == {"ru", "uz_latn", "uz_cyrl"}


@pytest.mark.evaluation
@pytest.mark.skipif(
    os.getenv("UZEX_RUN_EVALUATION") != "1",
    reason="set UZEX_RUN_EVALUATION=1 to execute the live 100-question set",
)
def test_live_evaluation_set_matches_expectations() -> None:
    from app.agent.orchestrator import Orchestrator

    report = evaluate(QUESTIONS, Orchestrator())

    failures = [item for item in report["results"] if not item["passed"]]
    assert not failures, "\n".join(
        f"{item['id']} {item['question']}: {', '.join(item['failures'])}"
        for item in failures
    )

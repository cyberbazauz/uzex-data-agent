from __future__ import annotations

import json
from pathlib import Path

from app.agent.query_plan import FilterCondition, QueryPlan
from app.domain.enums import (
    CurrencyMode,
    Decision,
    FilterField,
    Intent,
    Language,
    Metric,
    Operator,
    Script,
    Subject,
)
from evaluation.qualify_provider import (
    DEFAULT_QUESTIONS,
    evaluate_case,
    load_questions,
    main,
    run,
    structural_check,
)


def count_plan(start: str, end: str) -> QueryPlan:
    return QueryPlan(
        decision=Decision.EXECUTE,
        language=Language.RU,
        script=Script.CYRILLIC,
        intent=Intent.COUNT,
        subject=Subject.DEALS,
        metric=Metric.DEAL_COUNT,
        filters=[
            FilterCondition(
                field=FilterField.DEAL_DATE,
                operator=Operator.BETWEEN,
                value=[start, end],
            )
        ],
        currency_mode=CurrencyMode.NOT_APPLICABLE,
    )


EXPECTED = {
    "decision": "EXECUTE",
    "intent": "count",
    "subject": "deals",
    "metric": "deal_count",
    "date_range": ["2024-01-01", "2024-12-31"],
    "critical": ["decision", "intent", "subject", "metric", "date_range"],
}


def test_real_qualification_set_is_structurally_valid() -> None:
    data = load_questions(DEFAULT_QUESTIONS)
    assert structural_check(data) == []
    assert len(data["questions"]) == 112


def test_structural_check_rejects_a_small_set(tmp_path: Path) -> None:
    path = tmp_path / "small.json"
    path.write_text(
        json.dumps({"questions": [{"id": "x", "category": "ru"}]}), encoding="utf-8"
    )
    problems = structural_check(load_questions(path))
    assert problems
    assert any("minimum" in problem for problem in problems)


def test_evaluate_case_passes_matching_plan() -> None:
    assert evaluate_case(count_plan("2024-01-01", "2024-12-31"), EXPECTED) == []


def test_evaluate_case_detects_wrong_period_and_intent() -> None:
    failures = evaluate_case(count_plan("2023-01-01", "2023-12-31"), EXPECTED)
    assert "date_range" in failures

    wrong = count_plan("2024-01-01", "2024-12-31").model_copy(
        update={"intent": Intent.SUM_AMOUNT, "metric": Metric.AMOUNT_MIN}
    )
    failures = evaluate_case(wrong, EXPECTED)
    assert "intent" in failures
    assert "metric" in failures


def test_evaluate_case_rejects_injected_numeric_fact() -> None:
    injected = count_plan("2024-01-01", "2024-12-31")
    # Simulate a malicious entity mention carrying the injected number.
    failures = evaluate_case(
        injected,
        {
            "decision_any": ["EXECUTE"],
            "injected_fact": "106647",
            "critical": ["no_injected_fact"],
        },
    )
    assert failures == []  # number is absent from the plan

    # A plan that did echo a numeric fact would fail the gate.
    from app.agent.query_plan import EntityReference
    from app.domain.enums import MatchKind

    leaking = injected.model_copy(
        update={
            "entity_candidates": [
                EntityReference(kind=MatchKind.PARTICIPANT, mention="106647")
            ]
        }
    )
    assert "no_injected_fact" in evaluate_case(
        leaking,
        {
            "decision_any": ["EXECUTE"],
            "injected_fact": "106647",
            "critical": ["no_injected_fact"],
        },
    )


def test_check_set_cli_does_not_need_a_provider(capsys) -> None:  # noqa: ANN001
    assert main(["--check-set"]) == 0
    output = capsys.readouterr().out
    assert "SET_OK" in output
    assert '"questions": 112' in output


def test_limited_run_can_never_be_qualified(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    class FakeAdapter:
        def plan(self, question, detection):  # noqa: ANN001
            return count_plan("2024-01-01", "2024-12-31")

    monkeypatch.setattr(
        "evaluation.qualify_provider._adapter", lambda *args, **kwargs: FakeAdapter()
    )
    output = tmp_path / "result.json"
    exit_code = run(
        provider="fake",
        model="fake-model",
        questions_path=DEFAULT_QUESTIONS,
        output_path=output,
        report_dir=tmp_path,
        delay=0.0,
        limit=2,
        write_qualification=False,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["status"] == "UNQUALIFIED"
    assert payload["evaluated_cases"] == 2
    assert payload["limited_run"] is True


def _rate_limit_error(
    kind: str = "short", wait_seconds: float | None = 0.0
) -> Exception:
    from app.llm.error_classifier import ProviderFailure, ProviderFailureKind
    from app.llm.errors import ProviderTechnicalError

    failure_kind = (
        ProviderFailureKind.RATE_LIMIT_SHORT
        if kind == "short"
        else ProviderFailureKind.RATE_LIMIT_LONG
    )
    failure = ProviderFailure(
        kind=failure_kind,
        provider="groq",
        model="fake-model",
        status_code=429,
    )
    return ProviderTechnicalError(
        "groq HTTP 429",
        failure=failure,
        retry_allowed=False,
        wait_seconds=wait_seconds,
    )


def test_rate_limit_is_paced_with_the_provider_reset_then_retried(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    class OnceLimitedAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def plan(self, question, detection):  # noqa: ANN001
            self.calls += 1
            if self.calls == 1:
                raise _rate_limit_error("short", 0.0)
            return count_plan("2024-01-01", "2024-12-31")

    adapter = OnceLimitedAdapter()
    monkeypatch.setattr(
        "evaluation.qualify_provider._adapter", lambda *args, **kwargs: adapter
    )
    sleeps: list[float] = []
    monkeypatch.setattr(
        "evaluation.qualify_provider.time.sleep", lambda seconds: sleeps.append(seconds)
    )
    output = tmp_path / "result.json"
    run(
        provider="fake",
        model="fake-model",
        questions_path=DEFAULT_QUESTIONS,
        output_path=output,
        report_dir=tmp_path,
        delay=0.0,
        limit=1,
        write_qualification=False,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert adapter.calls == 2
    assert sleeps and sleeps[0] >= 1.0
    assert payload["technical_failures"] == 0
    assert payload["schema_adherence"] == 1.0
    # A limited run is never qualified.
    assert payload["status"] == "UNQUALIFIED"


def test_exhausted_rate_limit_is_reported_with_a_sanitized_kind(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    from evaluation.qualify_provider import MAX_RATE_LIMIT_RETRIES

    class AlwaysLimitedAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def plan(self, question, detection):  # noqa: ANN001
            self.calls += 1
            raise _rate_limit_error("short", 0.0)

    adapter = AlwaysLimitedAdapter()
    monkeypatch.setattr(
        "evaluation.qualify_provider._adapter", lambda *args, **kwargs: adapter
    )
    monkeypatch.setattr("evaluation.qualify_provider.time.sleep", lambda seconds: None)
    output = tmp_path / "result.json"
    run(
        provider="fake",
        model="fake-model",
        questions_path=DEFAULT_QUESTIONS,
        output_path=output,
        report_dir=tmp_path,
        delay=0.0,
        limit=2,
        write_qualification=False,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert adapter.calls == 2 * (MAX_RATE_LIMIT_RETRIES + 1)
    assert payload["technical_failures"] == 2
    assert payload["technical_failure_kinds"] == {"rate_limit_short": 2}
    failure = payload["failures"][0]
    assert failure["error_type"] == "ProviderTechnicalError"
    assert failure["error_kind"] == "rate_limit_short"
    assert failure["http_status"] == 429
    assert "gsk_" not in json.dumps(payload)


def test_long_rate_limit_is_not_paced_inside_qualification(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    class LongLimitedAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def plan(self, question, detection):  # noqa: ANN001
            self.calls += 1
            raise _rate_limit_error("long", 0.0)

    adapter = LongLimitedAdapter()
    monkeypatch.setattr(
        "evaluation.qualify_provider._adapter", lambda *args, **kwargs: adapter
    )
    monkeypatch.setattr("evaluation.qualify_provider.time.sleep", lambda seconds: None)
    output = tmp_path / "result.json"
    run(
        provider="fake",
        model="fake-model",
        questions_path=DEFAULT_QUESTIONS,
        output_path=output,
        report_dir=tmp_path,
        delay=0.0,
        limit=1,
        write_qualification=False,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert adapter.calls == 1
    assert payload["technical_failure_kinds"] == {"rate_limit_long": 1}


def test_rate_limit_wait_uses_real_reset_and_is_bounded() -> None:
    from evaluation.qualify_provider import (
        MAX_RATE_LIMIT_SLEEP_SECONDS,
        MIN_RATE_LIMIT_SLEEP_SECONDS,
        _rate_limit_wait,
    )

    assert _rate_limit_wait(_rate_limit_error("short", 15.0), 1.0) == 15.0
    assert _rate_limit_wait(_rate_limit_error("short", 0.0), 1.0) == (
        MIN_RATE_LIMIT_SLEEP_SECONDS
    )
    assert _rate_limit_wait(_rate_limit_error("short", 9999.0), 1.0) == (
        MAX_RATE_LIMIT_SLEEP_SECONDS
    )
    assert _rate_limit_wait(ValueError("not a provider failure"), 1.0) is None

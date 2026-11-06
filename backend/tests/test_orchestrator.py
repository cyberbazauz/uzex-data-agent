from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.agent.orchestrator import Orchestrator, normalize_request_id
from app.domain.enums import ClarificationCode, Decision, Intent, Metric, ResponseStatus
from app.handlers.base import HandlerResult

GOLDEN_2024 = 106647


def test_found_count_includes_exact_value_and_evidence() -> None:
    response = Orchestrator().chat("Сколько сделок было в 2024 году?")

    assert response.status is ResponseStatus.FOUND
    assert response.language == "ru"
    assert response.data is not None
    assert response.data.value == GOLDEN_2024
    assert response.evidence is not None
    assert response.evidence.matched_rows == GOLDEN_2024
    assert response.evidence.dataset_cutoff == "2026-10-30"
    assert response.evidence.dataset_version.startswith("sha256:")
    assert response.evidence.source_tables == ["deals"]
    assert response.duration_ms >= 0
    assert "106\u00a0647" in response.answer


def test_not_found_for_period_outside_dataset_still_has_evidence() -> None:
    response = Orchestrator().chat("Сколько сделок было в 2027 году?")

    assert response.status is ResponseStatus.NOT_FOUND
    assert response.data is None
    assert response.evidence is not None
    assert response.evidence.matched_rows == 0
    assert response.evidence.dataset_cutoff == "2026-10-30"


def test_ambiguous_region_requires_clarification_without_data() -> None:
    response = Orchestrator().chat("Сколько сделок было в Ташкенте?")

    assert response.status is ResponseStatus.NEEDS_CLARIFICATION
    assert response.clarification is not None
    assert response.clarification.code is ClarificationCode.REGION_MEANING_AMBIGUOUS
    assert response.data is None
    assert response.evidence is None


def test_ambiguous_participant_name_is_not_auto_resolved() -> None:
    response = Orchestrator().chat("Покажи участника sujo plus mchj")

    assert response.status is ResponseStatus.NEEDS_CLARIFICATION
    assert response.clarification is not None
    assert response.clarification.code is ClarificationCode.PARTICIPANT_AMBIGUOUS
    assert len(response.clarification.options) == 2
    assert response.data is None
    assert response.evidence is None


def test_forecast_is_unsupported() -> None:
    response = Orchestrator().chat("Спрогнозируй количество сделок в 2027 году")

    assert response.status is ResponseStatus.UNSUPPORTED
    assert response.data is None
    assert response.evidence is None


def test_profit_question_is_unsupported() -> None:
    response = Orchestrator().chat("Какая прибыль от сделок?")

    assert response.status is ResponseStatus.UNSUPPORTED
    assert response.data is None
    assert response.evidence is None


def test_deal_lookup_returns_record_without_aggregate_value() -> None:
    response = Orchestrator().chat("Покажи сделку 100001")

    assert response.status is ResponseStatus.FOUND
    assert response.data is not None
    assert response.data.value is None
    assert response.data.rows[0].fields["deal_no"] == "100001"
    assert str(response.data.rows[0].fields["deal_amount"]).startswith("304547938.4")
    assert response.evidence is not None
    assert response.evidence.matched_rows == 1


def test_missing_lookup_returns_not_found_not_error() -> None:
    response = Orchestrator().chat("Покажи сделку 999999")

    assert response.status is ResponseStatus.NOT_FOUND
    assert response.evidence is not None
    assert response.evidence.matched_rows == 0


def test_missing_entity_returns_not_found() -> None:
    response = Orchestrator().chat("Покажи участника Несуществующая Компания")

    assert response.status is ResponseStatus.NOT_FOUND
    assert response.data is None
    assert response.evidence is None


def test_uzbek_latin_and_cyrillic_answers() -> None:
    latin = Orchestrator().chat("2024-yilda nechta bitim bo'ldi?")
    cyrillic = Orchestrator().chat("2024-йилда нечта битим бўлди?")

    assert latin.status is ResponseStatus.FOUND
    assert latin.language == "uz"
    assert latin.locale == "uz_latn"
    assert "ta bitim" in latin.answer
    assert cyrillic.status is ResponseStatus.FOUND
    assert cyrillic.locale == "uz_cyrl"
    assert "та битим" in cyrillic.answer
    assert latin.data is not None and latin.data.value == GOLDEN_2024
    assert cyrillic.data is not None and cyrillic.data.value == GOLDEN_2024


def test_currency_sums_are_reported_separately() -> None:
    response = Orchestrator().chat("Сумма сделок в валюте контракта")

    assert response.status is ResponseStatus.FOUND
    assert response.data is not None
    assert len(response.data.rows) == 2
    assert {row.currency for row in response.data.rows} == {
        "Узбекский сум",
        "Доллар США",
    }
    assert "не складываются" in response.answer


def test_volume_answer_keeps_units_separate() -> None:
    response = Orchestrator().chat("Объём по единицам измерения")

    assert response.status is ResponseStatus.FOUND
    assert response.data is not None
    assert len(response.data.rows) > 1
    assert all(row.unit for row in response.data.rows)
    assert "единицам измерения" in response.answer


def test_trend_does_not_fill_missing_periods_with_zero() -> None:
    response = Orchestrator().chat("Динамика сделок по годам")

    assert response.status is ResponseStatus.FOUND
    assert response.data is not None
    assert [row.group["year"] for row in response.data.rows] == [2022, 2023, 2024, 2025, 2026]
    assert all(row.value is not None and row.value > 0 for row in response.data.rows)


def test_compare_absent_period_is_not_confirmed_zero() -> None:
    response = Orchestrator().chat("Сравни 2023 и 2024 годы по количеству сделок")

    assert response.status is ResponseStatus.FOUND
    assert response.data is not None
    assert [row.group["period"] for row in response.data.rows] == ["2023", "2024"]
    assert [row.value for row in response.data.rows] == [93985, 106647]


def test_database_failure_is_unverified_not_not_found() -> None:
    @contextmanager
    def failing_connection():
        raise SQLAlchemyError("simulated database outage")
        yield  # pragma: no cover

    response = Orchestrator(connection_factory=failing_connection).chat(
        "Сколько сделок было в 2024 году?"
    )

    assert response.status is ResponseStatus.UNVERIFIED
    assert response.data is None
    assert response.evidence is None


def test_planner_failure_is_unverified_not_not_found() -> None:
    class BrokenPlanner:
        name = "broken"

        def plan(self, question, detection):  # noqa: ANN001
            raise RuntimeError("simulated planner outage")

        def describe(self):
            return {"name": self.name}

    response = Orchestrator(planner=BrokenPlanner()).chat(
        "Сколько сделок было в 2024 году?"
    )

    assert response.status is ResponseStatus.UNVERIFIED
    assert response.data is None
    assert response.evidence is None


def test_handler_result_validation_failure_is_unverified() -> None:
    def invalid_executor(conn, execution, execution_id):  # noqa: ANN001
        return HandlerResult(
            intent=execution.plan.intent,
            metric=Metric.DEAL_COUNT,
            rows=[],
            matched_rows=0,
            metric_sql="broken",
            source_tables=["deals"],
        )

    response = Orchestrator(executor=invalid_executor).chat(
        "Сколько сделок было в 2024 году?"
    )

    assert response.status is ResponseStatus.UNVERIFIED
    assert response.data is None
    assert response.evidence is None


def test_request_id_is_sanitized() -> None:
    assert normalize_request_id("client<script>alert(1)</script>") == "clientscriptalert1script"
    response = Orchestrator().chat("Сколько всего сделок?", request_id="abc<script>")
    assert response.request_id == "abcscript"


def test_blank_question_is_rejected_before_query() -> None:
    with pytest.raises(ValueError):
        Orchestrator().chat("   ")


def test_plan_decision_enums_still_match_public_states() -> None:
    assert Decision.EXECUTE.value == "EXECUTE"
    assert Intent.COUNT.value == "count"


def test_sql_not_found_does_not_trigger_a_second_planning_attempt() -> None:
    """A valid plan with an empty SQL result is an honest NOT_FOUND.

    Failover is a *planning* policy for technical provider errors.  It must
    never re-plan (or switch models) because PostgreSQL found no rows.
    """

    from app.agent.planner import LocalPlannerAdapter

    class CountingPlanner:
        name = "counting"

        def __init__(self) -> None:
            self.calls = 0
            self._local = LocalPlannerAdapter()

        def plan(self, question, detection):  # noqa: ANN001
            self.calls += 1
            return self._local.plan(question, detection)

        def describe(self) -> dict[str, object]:
            return {"name": self.name, "calls": self.calls}

    planner = CountingPlanner()
    response = Orchestrator(planner=planner).chat(
        "Сколько сделок было в 2019 году?"
    )

    assert response.status is ResponseStatus.NOT_FOUND
    assert response.data is None
    assert response.evidence is not None
    assert response.evidence.matched_rows == 0
    assert planner.calls == 1


from __future__ import annotations

from decimal import Decimal

import pytest

from app.agent.query_plan import (
    ComparisonPeriod,
    ComparisonSpec,
    ExecutionPlan,
    FilterCondition,
    QueryPlan,
)
from app.domain.enums import (
    CURRENCY_UZS,
    Decision,
    FilterField,
    GroupField,
    Intent,
    Language,
    Metric,
    Operator,
    Script,
    Subject,
)
from app.domain.errors import UnverifiedQuery
from app.handlers.base import HandlerResult, ResultRow
from app.responses.formatter import format_found
from app.responses.templates import context_for
from app.validation.answer_grounding import validate_answer_grounding

DATASET_VERSION = "sha256:" + "d" * 64
DATASET_CUTOFF = "2026-10-30"


def execution(
    intent: Intent,
    metric: Metric,
    *,
    subject: Subject = Subject.DEALS,
    filters: list[FilterCondition] | None = None,
    group_by: list[GroupField] | None = None,
    comparison: ComparisonSpec | None = None,
    limit: int | None = None,
) -> ExecutionPlan:
    plan = QueryPlan(
        decision=Decision.EXECUTE,
        language=Language.RU,
        script=Script.CYRILLIC,
        intent=intent,
        subject=subject,
        metric=metric,
        filters=filters or [],
        group_by=group_by or [],
        comparison=comparison,
        limit=limit,
    )
    return ExecutionPlan(plan=plan, filters=filters or [])


def result(
    intent: Intent,
    metric: Metric,
    rows: list[ResultRow],
    *,
    matched_rows: int,
    grouping: list[str] | None = None,
    currency_method: str | None = None,
    unit: str | None = None,
    metric_sql: str = "COUNT(DISTINCT deals.deal_no)",
) -> HandlerResult:
    return HandlerResult(
        intent=intent,
        metric=metric,
        rows=rows,
        matched_rows=matched_rows,
        metric_sql=metric_sql,
        grouping=grouping or [],
        source_tables=["deals"],
        currency_method=currency_method,
        unit=unit,
    ).with_dataset(DATASET_VERSION, DATASET_CUTOFF, "req_test")


def ru_context():
    return context_for(Language.RU, Script.CYRILLIC)


def _grounded(answer: str, plan: ExecutionPlan, handler: HandlerResult) -> None:
    validate_answer_grounding(answer, plan, handler)


def test_count_answer_with_period_and_group_separator_is_grounded() -> None:
    plan = execution(
        Intent.COUNT,
        Metric.DEAL_COUNT,
        filters=[
            FilterCondition(
                field=FilterField.DEAL_DATE,
                operator=Operator.BETWEEN,
                value=["2024-01-01", "2024-12-31"],
            )
        ],
    )
    handler = result(
        Intent.COUNT,
        Metric.DEAL_COUNT,
        [ResultRow(value=106647)],
        matched_rows=106647,
    )
    formatted = format_found(plan, handler, ru_context())
    _grounded(formatted.answer, plan, handler)


def test_grouped_converted_money_answer_is_grounded() -> None:
    plan = execution(
        Intent.SUM_AMOUNT,
        Metric.AMOUNT_SUM_UZS_CONVERTED,
        group_by=[GroupField.CURRENCY],
    )
    handler = result(
        Intent.SUM_AMOUNT,
        Metric.AMOUNT_SUM_UZS_CONVERTED,
        [
            ResultRow(value=Decimal("1234567890123.45"), currency=CURRENCY_UZS),
            ResultRow(value=Decimal("987654321.01"), currency=CURRENCY_UZS),
        ],
        matched_rows=12,
        grouping=["currency"],
        currency_method="documented conversion",
    )
    formatted = format_found(plan, handler, ru_context())
    _grounded(formatted.answer, plan, handler)


def test_month_trend_answer_is_grounded() -> None:
    plan = execution(
        Intent.TREND,
        Metric.DEAL_COUNT,
        group_by=[GroupField.MONTH],
    )
    handler = result(
        Intent.TREND,
        Metric.DEAL_COUNT,
        [
            ResultRow(group={"month": "2024-01"}, value=1200),
            ResultRow(group={"month": "2024-02"}, value=1300),
        ],
        matched_rows=2500,
        grouping=["month"],
    )
    formatted = format_found(plan, handler, ru_context())
    assert "01.2024" in formatted.answer
    _grounded(formatted.answer, plan, handler)


def test_top_n_answer_with_rank_and_limit_is_grounded() -> None:
    plan = execution(
        Intent.TOP_N,
        Metric.DEAL_COUNT,
        group_by=[GroupField.PRODUCT_NAME],
        limit=2,
    )
    handler = result(
        Intent.TOP_N,
        Metric.DEAL_COUNT,
        [
            ResultRow(group={"product_name": "Пшеница"}, value=500),
            ResultRow(group={"product_name": "Хлопок"}, value=400),
        ],
        matched_rows=900,
        grouping=["product_name"],
    )
    formatted = format_found(plan, handler, ru_context())
    _grounded(formatted.answer, plan, handler)


def test_record_lookup_answer_is_grounded() -> None:
    plan = execution(Intent.DEAL_LOOKUP, Metric.RECORD_LOOKUP)
    handler = result(
        Intent.DEAL_LOOKUP,
        Metric.RECORD_LOOKUP,
        [
            ResultRow(
                extra={
                    "deal_no": "100001",
                    "deal_date": "2024-05-01",
                    "product_name": "Пшеница 3 класс",
                    "quantity_lots": Decimal("12.5"),
                    "unit_name": "тонна",
                    "deal_amount": Decimal("304547938.4"),
                    "currency": "USD",
                    "status": "Исполнена",
                }
            )
        ],
        matched_rows=1,
        metric_sql="SELECT deal row",
    )
    formatted = format_found(plan, handler, ru_context())
    _grounded(formatted.answer, plan, handler)


def test_injected_number_in_answer_fails_closed() -> None:
    plan = execution(Intent.COUNT, Metric.DEAL_COUNT)
    handler = result(
        Intent.COUNT,
        Metric.DEAL_COUNT,
        [ResultRow(value=10)],
        matched_rows=10,
    )
    formatted = format_found(plan, handler, ru_context())
    tampered = formatted.answer + "\nДополнительно: 987\u00a0654\u00a0321 сделок."

    with pytest.raises(UnverifiedQuery):
        _grounded(tampered, plan, handler)


def test_plausible_but_unconfirmed_number_fails_closed() -> None:
    plan = execution(Intent.COUNT, Metric.DEAL_COUNT)
    handler = result(
        Intent.COUNT,
        Metric.DEAL_COUNT,
        [ResultRow(value=10)],
        matched_rows=10,
    )
    with pytest.raises(UnverifiedQuery):
        _grounded("Количество сделок: 11", plan, handler)


def test_unrelated_result_numbers_do_not_authorize_new_numbers() -> None:
    plan = execution(Intent.COUNT, Metric.DEAL_COUNT)
    handler = result(
        Intent.COUNT,
        Metric.DEAL_COUNT,
        [ResultRow(group={"year": 2024}, value=10)],
        matched_rows=10,
        grouping=["year"],
    )
    with pytest.raises(UnverifiedQuery):
        _grounded("В 2024 году было 10 сделок, прогноз на 2027 год — 15.", plan, handler)


def test_year_filter_is_allowed_but_unrelated_year_is_not() -> None:
    plan = execution(
        Intent.COUNT,
        Metric.DEAL_COUNT,
        filters=[
            FilterCondition(
                field=FilterField.DEAL_DATE,
                operator=Operator.BETWEEN,
                value=["2024-01-01", "2024-12-31"],
            )
        ],
    )
    handler = result(
        Intent.COUNT,
        Metric.DEAL_COUNT,
        [ResultRow(value=10)],
        matched_rows=10,
    )
    _grounded("Сделок за 2024 год: 10", plan, handler)
    with pytest.raises(UnverifiedQuery):
        _grounded("Сделок за 2025 год: 10", plan, handler)

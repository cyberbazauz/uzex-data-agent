from __future__ import annotations

from datetime import date
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
    CURRENCY_USD,
    CURRENCY_UZS,
    Decision,
    FilterField,
    GroupField,
    Intent,
    Language,
    Metric,
    Operator,
    ResponseStatus,
    Script,
    Subject,
)
from app.handlers.base import HandlerResult, ResultRow
from app.responses.formatter import (
    format_clarification,
    format_found,
    format_not_found,
    format_unsupported,
    format_unverified,
)
from app.responses.models import ChatResponse
from app.responses.templates import context_for

DATASET_VERSION = "sha256:" + "a" * 64
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
    notes: list[str] | None = None,
    currency_method: str | None = None,
    source_tables: list[str] | None = None,
    metric_sql: str = "COUNT(DISTINCT deals.deal_no)",
) -> HandlerResult:
    return HandlerResult(
        intent=intent,
        metric=metric,
        rows=rows,
        matched_rows=matched_rows,
        metric_sql=metric_sql,
        grouping=grouping or [],
        source_tables=source_tables or ["deals"],
        currency_method=currency_method,
        notes=notes or [],
    ).with_dataset(DATASET_VERSION, DATASET_CUTOFF, "req_test")


def ru_context():
    return context_for(Language.RU, Script.CYRILLIC)


def test_count_keeps_exact_value_and_uses_localized_separator() -> None:
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

    assert "106\u00a0647" in formatted.answer
    assert "2024" in formatted.answer
    assert formatted.data.value == 106647
    assert formatted.data.unit == "сделок"
    assert formatted.data.period == "2024 год"
    assert formatted.data.rows[0].value == 106647


def test_multi_currency_sum_never_adds_currencies() -> None:
    plan = execution(Intent.SUM_AMOUNT, Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY)
    handler = result(
        Intent.SUM_AMOUNT,
        Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
        [
            ResultRow(value=Decimal("1137509729.39000000"), currency=CURRENCY_USD),
            ResultRow(value=Decimal("206835219986242.97"), currency=CURRENCY_UZS),
        ],
        matched_rows=500000,
        grouping=["currency"],
    )

    formatted = format_found(plan, handler, ru_context())

    assert "не складываются" in formatted.answer
    assert "1\u00a0137\u00a0509\u00a0729,39" in formatted.answer
    assert "206\u00a0835\u00a0219\u00a0986\u00a0242,97" in formatted.answer
    assert [row.value for row in formatted.data.rows] == [
        Decimal("1137509729.39000000"),
        Decimal("206835219986242.97"),
    ]
    assert {row.currency for row in formatted.data.rows} == {CURRENCY_USD, CURRENCY_UZS}


def test_multi_unit_volume_keeps_units_separate() -> None:
    plan = execution(
        Intent.SUM_VOLUME,
        Metric.VOLUME_SUM_BY_UNIT,
        group_by=[GroupField.UNIT_NAME],
    )
    handler = result(
        Intent.SUM_VOLUME,
        Metric.VOLUME_SUM_BY_UNIT,
        [
            ResultRow(group={"unit_name": "тонна"}, value=Decimal("504603")),
            ResultRow(group={"unit_name": "килограмм"}, value=Decimal("9803867729")),
        ],
        matched_rows=500000,
        grouping=["unit_name"],
    )
    handler.rows[0].unit = "тонна"
    handler.rows[1].unit = "килограмм"

    formatted = format_found(plan, handler, ru_context())

    assert "тонна" in formatted.answer
    assert "килограмм" in formatted.answer
    assert any("не складываются" in note for note in formatted.data.notes)
    assert [row.unit for row in formatted.data.rows] == ["тонна", "килограмм"]


def test_min_max_reports_currency_and_deal_reference() -> None:
    plan = execution(Intent.MIN_MAX, Metric.AMOUNT_MIN)
    handler = result(
        Intent.MIN_MAX,
        Metric.AMOUNT_MIN,
        [
            ResultRow(
                group={"currency": CURRENCY_USD},
                value=Decimal("7327.96"),
                currency=CURRENCY_USD,
                extra={"deal_no": "345366", "deal_date": date(2024, 9, 25)},
            )
        ],
        matched_rows=6590,
        grouping=["currency"],
    )

    formatted = format_found(plan, handler, ru_context())

    assert "345366" in formatted.answer
    assert "25.09.2024" in formatted.answer
    assert "7\u00a0327,96" in formatted.answer
    assert formatted.data.rows[0].value == Decimal("7327.96")


def test_compare_marks_absent_period_without_inventing_zero() -> None:
    comparison = ComparisonSpec(
        left=ComparisonPeriod(
            label="2023", date_from=date(2023, 1, 1), date_to=date(2023, 12, 31)
        ),
        right=ComparisonPeriod(
            label="2027", date_from=date(2027, 1, 1), date_to=date(2027, 12, 31)
        ),
    )
    plan = execution(
        Intent.COMPARE, Metric.DEAL_COUNT, comparison=comparison
    )
    handler = result(
        Intent.COMPARE,
        Metric.DEAL_COUNT,
        [
            ResultRow(group={"period": "2023"}, value=93985),
            ResultRow(group={"period": "2027"}, value=None, extra={"matched_rows": 0}),
        ],
        matched_rows=93985,
        grouping=["period"],
    )

    formatted = format_found(plan, handler, ru_context())

    assert "93\u00a0985" in formatted.answer
    assert "2027" in formatted.answer
    assert "0" not in formatted.answer.split("2027", 1)[1].split("\n")[0]
    assert formatted.data.rows[1].value is None


def test_lookup_keeps_fact_fields_and_exact_decimal() -> None:
    plan = execution(
        Intent.DEAL_LOOKUP,
        Metric.RECORD_LOOKUP,
        filters=[
            FilterCondition(
                field=FilterField.DEAL_NO, operator=Operator.EQ, value="100001"
            )
        ],
        subject=Subject.DEALS,
    )
    handler = result(
        Intent.DEAL_LOOKUP,
        Metric.RECORD_LOOKUP,
        [
            ResultRow(
                group={"deal_no": "100001"},
                extra={
                    "deal_no": "100001",
                    "deal_date": date(2022, 1, 3),
                    "deal_product_name": "Шрот хлопковых семян",
                    "quantity_lots": 79720,
                    "unit_name": "килограмм",
                    "deal_amount": Decimal("304547938.40000000"),
                    "currency": CURRENCY_UZS,
                    "status": "Исполнен",
                },
            )
        ],
        matched_rows=1,
        metric_sql="SELECT deal row",
        source_tables=["deals", "contracts"],
    )

    formatted = format_found(plan, handler, ru_context())

    assert "100001" in formatted.answer
    assert "304\u00a0547\u00a0938,40" in formatted.answer
    assert formatted.data.value is None
    assert formatted.data.rows[0].fields["deal_no"] == "100001"
    assert formatted.data.rows[0].fields["deal_amount"] == Decimal(
        "304547938.40000000"
    )


def _deal_lookup_plan() -> ExecutionPlan:
    return execution(
        Intent.DEAL_LOOKUP,
        Metric.RECORD_LOOKUP,
        filters=[
            FilterCondition(
                field=FilterField.DEAL_NO, operator=Operator.EQ, value="280091"
            )
        ],
        subject=Subject.DEALS,
    )


def test_deal_line_reports_delivered_quantity_and_delivery_date() -> None:
    handler = result(
        Intent.DEAL_LOOKUP,
        Metric.RECORD_LOOKUP,
        [
            ResultRow(
                group={"deal_no": "280091"},
                extra={
                    "deal_no": "280091",
                    "deal_date": date(2024, 1, 2),
                    "deal_product_name": "Катод медный М0к",
                    "quantity_lots": 17,
                    "delivered_lots": 17,
                    "delivery_date": date(2024, 1, 19),
                    "unit_name": "тонна",
                    "deal_amount": Decimal("136000.00"),
                    "currency": CURRENCY_USD,
                    "status": "Исполнен",
                },
            )
        ],
        matched_rows=1,
        metric_sql="SELECT deal row",
        source_tables=["deals", "contracts"],
    )

    formatted = format_found(_deal_lookup_plan(), handler, ru_context())

    assert "280091" in formatted.answer
    assert "количество: 17 тонна" in formatted.answer
    assert "поставлено: 17 тонна" in formatted.answer
    assert "дата поставки: 19.01.2024" in formatted.answer


def test_deal_line_renders_missing_delivery_as_no_data_not_zero() -> None:
    handler = result(
        Intent.DEAL_LOOKUP,
        Metric.RECORD_LOOKUP,
        [
            ResultRow(
                group={"deal_no": "280091"},
                extra={
                    "deal_no": "280091",
                    "deal_date": date(2024, 1, 2),
                    "deal_product_name": "Катод медный М0к",
                    "quantity_lots": 17,
                    "delivered_lots": None,
                    "delivery_date": None,
                    "unit_name": "тонна",
                    "deal_amount": Decimal("136000.00"),
                    "currency": CURRENCY_USD,
                    "status": "Исполнен",
                },
            )
        ],
        matched_rows=1,
        metric_sql="SELECT deal row",
        source_tables=["deals", "contracts"],
    )

    formatted = format_found(_deal_lookup_plan(), handler, ru_context())

    assert "поставлено: нет данных" in formatted.answer
    assert "дата поставки: нет данных" in formatted.answer
    assert "поставлено: 0" not in formatted.answer


def test_non_found_messages_are_explicit() -> None:
    ctx = ru_context()
    assert "не найдено" in format_not_found(ctx)
    assert "NULL" in format_not_found(ctx, no_value=True)
    assert "нельзя" in format_unsupported(ctx)
    assert "Не могу" in format_unverified(ctx)
    assert "брокер" in format_clarification(
        ctx, __import__("app.domain.enums", fromlist=["ClarificationCode"]).ClarificationCode.BROKER_SIDE_AMBIGUOUS
    )


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("uz_latn", "ta bitim"),
        ("uz_cyrl", "та битим"),
    ],
)
def test_uzbek_locales_are_used(locale: str, expected: str) -> None:
    from app.responses.templates import context_for_locale

    plan = execution(Intent.COUNT, Metric.DEAL_COUNT)
    handler = result(
        Intent.COUNT, Metric.DEAL_COUNT, [ResultRow(value=106647)], matched_rows=106647
    )

    formatted = format_found(plan, handler, context_for_locale(locale))

    assert expected in formatted.answer
    assert "106\u00a0647" in formatted.answer


def test_chat_response_invariants_reject_unverified_with_data() -> None:
    from app.responses.models import AnswerData

    with pytest.raises(ValueError):
        ChatResponse(
            status=ResponseStatus.UNVERIFIED,
            language="ru",
            answer="x",
            data=AnswerData(metric="deal_count"),
            duration_ms=1,
            request_id="req_test",
        )

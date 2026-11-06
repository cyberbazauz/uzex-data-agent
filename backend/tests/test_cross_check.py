from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.agent.query_plan import (
    ComparisonPeriod,
    ComparisonSpec,
    ExecutionPlan,
    QueryPlan,
)
from app.domain.enums import (
    CURRENCY_UZS,
    Decision,
    Direction,
    GroupField,
    Intent,
    Language,
    Metric,
    OrderField,
    Script,
    Subject,
)
from app.domain.errors import UnverifiedQuery
from app.handlers.base import HandlerResult, ResultRow
from app.validation.cross_check import validate_cross_checks

DATASET_VERSION = "sha256:" + "e" * 64
DATASET_CUTOFF = "2026-10-30"


def execution(
    intent: Intent,
    metric: Metric,
    *,
    group_by: list[GroupField] | None = None,
    limit: int | None = None,
    direction: Direction = Direction.DESC,
    comparison: ComparisonSpec | None = None,
) -> ExecutionPlan:
    order_by = (
        [{"field": OrderField.METRIC, "direction": direction}]
        if intent is Intent.TOP_N
        else []
    )
    plan = QueryPlan(
        decision=Decision.EXECUTE,
        language=Language.RU,
        script=Script.CYRILLIC,
        intent=intent,
        subject=Subject.DEALS,
        metric=metric,
        group_by=group_by or [],
        order_by=order_by,
        limit=limit,
        comparison=comparison,
    )
    return ExecutionPlan(plan=plan, filters=[])


def result(
    intent: Intent,
    metric: Metric,
    rows: list[ResultRow],
    *,
    matched_rows: int,
    grouping: list[str] | None = None,
    currency_method: str | None = None,
) -> HandlerResult:
    return HandlerResult(
        intent=intent,
        metric=metric,
        rows=rows,
        matched_rows=matched_rows,
        metric_sql="COUNT(DISTINCT deals.deal_no)",
        grouping=grouping or [],
        source_tables=["deals"],
        currency_method=currency_method,
    ).with_dataset(DATASET_VERSION, DATASET_CUTOFF, "req_test")


def test_grouped_count_must_add_up_to_matched_rows() -> None:
    plan = execution(
        Intent.TREND,
        Metric.DEAL_COUNT,
        group_by=[GroupField.YEAR],
    )
    handler = result(
        Intent.TREND,
        Metric.DEAL_COUNT,
        [
            ResultRow(group={"year": 2023}, value=10),
            ResultRow(group={"year": 2024}, value=15),
        ],
        matched_rows=25,
        grouping=["year"],
    )
    validate_cross_checks(handler, plan)


def test_grouped_count_mismatch_fails() -> None:
    plan = execution(
        Intent.TREND,
        Metric.DEAL_COUNT,
        group_by=[GroupField.YEAR],
    )
    handler = result(
        Intent.TREND,
        Metric.DEAL_COUNT,
        [
            ResultRow(group={"year": 2023}, value=10),
            ResultRow(group={"year": 2024}, value=15),
        ],
        matched_rows=26,
        grouping=["year"],
    )
    with pytest.raises(UnverifiedQuery):
        validate_cross_checks(handler, plan)


def test_count_with_matched_rows_must_have_rows() -> None:
    plan = execution(Intent.COUNT, Metric.DEAL_COUNT)
    handler = result(
        Intent.COUNT,
        Metric.DEAL_COUNT,
        [],
        matched_rows=5,
    )
    with pytest.raises(UnverifiedQuery):
        validate_cross_checks(handler, plan)


def test_count_partition_is_skipped_for_top_n_limit() -> None:
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
            ResultRow(group={"product_name": "A"}, value=50),
            ResultRow(group={"product_name": "B"}, value=40),
        ],
        matched_rows=900,
        grouping=["product_name"],
    )
    validate_cross_checks(handler, plan)


def test_top_n_overflowing_limit_fails() -> None:
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
            ResultRow(group={"product_name": "A"}, value=50),
            ResultRow(group={"product_name": "B"}, value=40),
            ResultRow(group={"product_name": "C"}, value=30),
        ],
        matched_rows=900,
        grouping=["product_name"],
    )
    with pytest.raises(UnverifiedQuery):
        validate_cross_checks(handler, plan)


def test_top_n_wrong_order_fails() -> None:
    plan = execution(
        Intent.TOP_N,
        Metric.DEAL_COUNT,
        group_by=[GroupField.PRODUCT_NAME],
        limit=3,
    )
    handler = result(
        Intent.TOP_N,
        Metric.DEAL_COUNT,
        [
            ResultRow(group={"product_name": "A"}, value=10),
            ResultRow(group={"product_name": "B"}, value=50),
            ResultRow(group={"product_name": "C"}, value=30),
        ],
        matched_rows=900,
        grouping=["product_name"],
    )
    with pytest.raises(UnverifiedQuery):
        validate_cross_checks(handler, plan)


def test_top_n_ascending_order_is_respected() -> None:
    plan = execution(
        Intent.TOP_N,
        Metric.DEAL_COUNT,
        group_by=[GroupField.PRODUCT_NAME],
        limit=3,
        direction=Direction.ASC,
    )
    handler = result(
        Intent.TOP_N,
        Metric.DEAL_COUNT,
        [
            ResultRow(group={"product_name": "A"}, value=10),
            ResultRow(group={"product_name": "B"}, value=20),
            ResultRow(group={"product_name": "C"}, value=20),
        ],
        matched_rows=900,
        grouping=["product_name"],
    )
    validate_cross_checks(handler, plan)


def test_top_n_native_amount_checks_each_currency_separately() -> None:
    plan = execution(
        Intent.TOP_N,
        Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
        group_by=[GroupField.CONTRACT_NO],
        limit=2,
    )
    handler = result(
        Intent.TOP_N,
        Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
        [
            ResultRow(group={"currency": "USD", "contract_no": "1"}, value=Decimal("100"), currency="USD"),
            ResultRow(group={"currency": "USD", "contract_no": "2"}, value=Decimal("90"), currency="USD"),
            ResultRow(group={"currency": "UZS", "contract_no": "3"}, value=Decimal("50"), currency="UZS"),
            ResultRow(group={"currency": "UZS", "contract_no": "4"}, value=Decimal("40"), currency="UZS"),
        ],
        matched_rows=10,
        grouping=["currency", "contract_no"],
        currency_method="native",
    )
    validate_cross_checks(handler, plan)


def test_compare_requires_exactly_the_requested_periods() -> None:
    comparison = ComparisonSpec(
        left=ComparisonPeriod(
            label="2023", date_from=date(2023, 1, 1), date_to=date(2023, 12, 31)
        ),
        right=ComparisonPeriod(
            label="2024", date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)
        ),
    )
    plan = execution(
        Intent.COMPARE,
        Metric.DEAL_COUNT,
        comparison=comparison,
    )
    handler = result(
        Intent.COMPARE,
        Metric.DEAL_COUNT,
        [
            ResultRow(group={"period": "2023"}, value=10),
            ResultRow(group={"period": "2025"}, value=15),
        ],
        matched_rows=25,
        grouping=["period"],
    )
    with pytest.raises(UnverifiedQuery):
        validate_cross_checks(handler, plan)


def test_compare_absent_period_marker_is_consistent() -> None:
    comparison = ComparisonSpec(
        left=ComparisonPeriod(
            label="2023", date_from=date(2023, 1, 1), date_to=date(2023, 12, 31)
        ),
        right=ComparisonPeriod(
            label="2024", date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)
        ),
    )
    plan = execution(
        Intent.COMPARE,
        Metric.DEAL_COUNT,
        comparison=comparison,
    )
    handler = result(
        Intent.COMPARE,
        Metric.DEAL_COUNT,
        [
            ResultRow(group={"period": "2023"}, value=10),
            ResultRow(
                group={"period": "2024"},
                value=None,
                extra={"matched_rows": 0},
            ),
        ],
        matched_rows=10,
        grouping=["period"],
    )
    validate_cross_checks(handler, plan)


def test_compare_value_marked_absent_fails() -> None:
    comparison = ComparisonSpec(
        left=ComparisonPeriod(
            label="2023", date_from=date(2023, 1, 1), date_to=date(2023, 12, 31)
        ),
        right=ComparisonPeriod(
            label="2024", date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)
        ),
    )
    plan = execution(
        Intent.COMPARE,
        Metric.DEAL_COUNT,
        comparison=comparison,
    )
    handler = result(
        Intent.COMPARE,
        Metric.DEAL_COUNT,
        [
            ResultRow(group={"period": "2023"}, value=10),
            ResultRow(
                group={"period": "2024"},
                value=15,
                extra={"matched_rows": 0},
            ),
        ],
        matched_rows=25,
        grouping=["period"],
    )
    with pytest.raises(UnverifiedQuery):
        validate_cross_checks(handler, plan)

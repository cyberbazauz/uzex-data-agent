from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from app.agent.query_plan import ExecutionPlan
from app.domain.enums import CURRENCY_USD, CURRENCY_UZS, Intent, Metric
from app.domain.errors import UnverifiedQuery
from app.handlers.base import HandlerResult, ResultRow

KNOWN_CURRENCIES = frozenset({CURRENCY_UZS, CURRENCY_USD, "UZS", "USD"})

NATIVE_MONEY_METRICS = frozenset(
    {
        Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
        Metric.AMOUNT_MIN,
        Metric.AMOUNT_MAX,
    }
)
CONVERTED_MONEY_METRICS = frozenset(
    {
        Metric.AMOUNT_SUM_UZS_CONVERTED,
        Metric.AMOUNT_AVERAGE_UZS_CONVERTED,
    }
)
VOLUME_METRICS = frozenset(
    {
        Metric.VOLUME_SUM_BY_UNIT,
        Metric.DELIVERED_VOLUME_BY_UNIT,
        Metric.DELIVERED_LOTS_SUM,
    }
)
UNIT_PRICE_METRICS = frozenset(
    {
        Metric.UNIT_PRICE_SIMPLE_AVERAGE,
        Metric.UNIT_PRICE_WEIGHTED_AVERAGE,
    }
)


def _fail(message: str) -> None:
    raise UnverifiedQuery(message)


def _reject_floats(value: Any, path: str) -> None:
    if isinstance(value, float):
        _fail(f"float value is not allowed in a factual result ({path})")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_floats(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple, set)):
        for index, item in enumerate(value):
            _reject_floats(item, f"{path}[{index}]")


def _check_value(row: ResultRow, index: int) -> None:
    value = row.value
    if value is None:
        return
    if isinstance(value, bool):
        _fail(f"row {index} has a boolean metric value")
    if not isinstance(value, (int, Decimal)):
        _fail(f"row {index} metric value must be int or Decimal")
    if isinstance(value, Decimal) and not value.is_finite():
        _fail(f"row {index} metric value is not finite")


def _check_dataset_metadata(
    result: HandlerResult,
    dataset_version: str | None,
    dataset_cutoff: str | None,
) -> None:
    if not result.dataset_version or not result.dataset_cutoff:
        _fail("handler result is missing active dataset metadata")
    if not result.execution_id:
        _fail("handler result is missing an execution id")
    if dataset_version is not None and result.dataset_version != dataset_version:
        _fail("handler result used a different dataset version")
    if dataset_cutoff is not None and result.dataset_cutoff != dataset_cutoff:
        _fail("handler result used a different dataset cutoff")


def _check_row_shape(
    result: HandlerResult, rows: list[ResultRow], matched_rows: int
) -> None:
    if result.intent is Intent.COMPARE:
        for index, row in enumerate(rows):
            if row.value is None:
                if row.extra.get("matched_rows") != 0:
                    _fail(
                        f"compare row {index} has a NULL value but does not mark "
                        "its period as absent"
                    )
                continue
            _check_value(row, index)
    else:
        if matched_rows == 0 and rows:
            _fail("zero matched rows must not produce result rows")
        if len(rows) > matched_rows:
            _fail("more result rows than matched source rows")
        for index, row in enumerate(rows):
            _check_value(row, index)

    group_keys: set[str] = set()
    for row in rows:
        key = json.dumps(row.group, ensure_ascii=False, sort_keys=True, default=str)
        if key in group_keys:
            _fail("result contains duplicate grouping keys")
        group_keys.add(key)


def _check_metric_semantics(result: HandlerResult, rows: list[ResultRow]) -> None:
    metric = result.metric
    value_rows = [row for row in rows if row.value is not None]

    if metric is Metric.DEAL_COUNT:
        for row in value_rows:
            value = row.value
            if isinstance(value, Decimal):
                if value != value.to_integral_value() or value < 0:
                    _fail("deal count must be a non-negative integer")
            elif value < 0:
                _fail("deal count must not be negative")
        return

    if metric in NATIVE_MONEY_METRICS:
        for row in value_rows:
            if not row.currency:
                _fail(f"{metric.value} requires an explicit currency per row")
            if row.currency not in KNOWN_CURRENCIES:
                _fail(f"unknown currency {row.currency!r} in result")
        return

    if metric in CONVERTED_MONEY_METRICS:
        for row in value_rows:
            if row.currency not in (CURRENCY_UZS, "UZS"):
                _fail(f"{metric.value} must report UZS, not {row.currency!r}")
        if value_rows and not result.currency_method:
            _fail(f"{metric.value} requires a documented conversion method")
        return

    if metric in VOLUME_METRICS:
        for row in value_rows:
            if not row.unit:
                _fail(f"{metric.value} requires a unit_name per row")
        return

    if metric in UNIT_PRICE_METRICS:
        for row in value_rows:
            if not row.currency or not row.unit:
                _fail("unit price requires both currency and unit")
            if row.currency not in KNOWN_CURRENCIES:
                _fail(f"unknown currency {row.currency!r} in unit price")
        return

    if metric is Metric.RECORD_LOOKUP:
        for row in rows:
            if row.value is not None:
                _fail("record lookup rows must not carry an aggregate value")
            if not row.extra:
                _fail("record lookup row is missing its fact fields")
        return

    _fail(f"no result validation rule for metric {metric.value}")


def validate_result(
    result: HandlerResult,
    execution: ExecutionPlan,
    *,
    dataset_version: str | None = None,
    dataset_cutoff: str | None = None,
) -> None:
    """Fail closed when a handler result cannot be trusted as fact."""
    plan = execution.plan
    if result.intent is not plan.intent:
        _fail("handler intent does not match the validated plan")
    if result.metric is not plan.metric:
        _fail("handler metric does not match the validated plan")
    if not result.source_tables:
        _fail("handler result has no source tables")
    if not result.metric_sql:
        _fail("handler result has no metric description")
    if result.matched_rows < 0:
        _fail("handler result has a negative matched_rows")

    _check_dataset_metadata(result, dataset_version, dataset_cutoff)
    _check_row_shape(result, result.rows, result.matched_rows)
    _check_metric_semantics(result, result.rows)

    for index, row in enumerate(result.rows):
        _reject_floats(row.group, f"rows[{index}].group")
        _reject_floats(row.extra, f"rows[{index}].extra")

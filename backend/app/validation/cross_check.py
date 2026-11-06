from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from app.agent.query_plan import ExecutionPlan
from app.domain.enums import Direction, GroupField, Intent, Metric, OrderField
from app.domain.errors import UnverifiedQuery
from app.handlers.base import HandlerResult, ResultRow

_ZERO = 0


def _fail(message: str) -> None:
    raise UnverifiedQuery(message)


def _integral(value: Decimal | int | None) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value.is_finite():
        if value != value.to_integral_value():
            return None
        return int(value)
    return None


def _metric_direction(execution: ExecutionPlan) -> str:
    for order in execution.plan.order_by:
        if order.field is OrderField.METRIC:
            return "asc" if order.direction is Direction.ASC else "desc"
    # The handlers use metric_desc when no explicit order is supplied.
    return "desc"


def _check_count_partition(result: HandlerResult, execution: ExecutionPlan) -> None:
    if result.metric is not Metric.DEAL_COUNT:
        return
    if execution.plan.intent is Intent.TOP_N:
        # TOP_N deliberately returns only the requested number of groups.
        return
    if result.matched_rows > 0 and not result.rows:
        _fail("count result is missing rows although deals matched")
    total = _ZERO
    for row in result.rows:
        if row.value is None:
            if execution.plan.intent is Intent.COMPARE:
                # A comparison period without deals is represented as absent.
                continue
            _fail("count row has no value")
        value = _integral(row.value)
        if value is None or value < 0:
            _fail("count row is not a non-negative integer")
        total += value
    if total != result.matched_rows:
        _fail("grouped deal counts do not add up to confirmed matched_rows")


def _check_order(rows: list[ResultRow], direction: str) -> None:
    previous: Decimal | int | None = None
    for row in rows:
        value = row.value
        if value is None:
            continue
        if previous is not None:
            if direction == "desc" and value > previous:
                _fail("TOP_N result is not ordered by the requested direction")
            if direction == "asc" and value < previous:
                _fail("TOP_N result is not ordered by the requested direction")
        previous = value


def _check_top_n(result: HandlerResult, execution: ExecutionPlan) -> None:
    limit = execution.plan.limit or 10
    direction = _metric_direction(execution)

    if result.metric is Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY and any(
        GroupField.CURRENCY.value in row.group for row in result.rows
    ):
        # Native ranks are computed independently for each contract currency.
        groups: dict[str, list[ResultRow]] = defaultdict(list)
        for row in result.rows:
            groups[str(row.group.get(GroupField.CURRENCY.value))].append(row)
        for group_rows in groups.values():
            if len(group_rows) > limit:
                _fail("TOP_N returned more rows than the requested limit")
            _check_order(group_rows, direction)
        return

    if len(result.rows) > limit:
        _fail("TOP_N returned more rows than the requested limit")
    _check_order(result.rows, direction)


def _check_compare(result: HandlerResult, execution: ExecutionPlan) -> None:
    comparison = execution.plan.comparison
    if comparison is None:
        _fail("compare result has no comparison specification")
    labels = {comparison.left.label, comparison.right.label}
    if result.rows:
        observed = {str(row.group.get("period")) for row in result.rows}
        if observed != labels:
            _fail("compare result does not cover exactly the requested periods")
    for row in result.rows:
        if str(row.group.get("period")) not in labels:
            _fail("compare row references an unrequested period")
        if row.value is None and row.extra.get("matched_rows") != 0:
            _fail("compare row has a NULL value without an absent-data marker")
        if row.value is not None and row.extra.get("matched_rows") == 0:
            _fail("compare row marks a period absent but still reports a value")


def validate_cross_checks(
    result: HandlerResult,
    execution: ExecutionPlan,
) -> None:
    """Cross-check aggregate structure against confirmed source rows.

    These checks do not re-query the database.  They prove internal
    consistency that must hold for any honest result:

    * grouped COUNT partitions add up to ``matched_rows``;
    * TOP_N respects its limit and ordering direction;
    * COMPARE covers exactly its two periods and marks absent data explicitly.

    A violation means the result cannot be presented as fact and fails closed.
    """
    _check_count_partition(result, execution)
    if execution.plan.intent is Intent.TOP_N:
        _check_top_n(result, execution)
    if execution.plan.intent is Intent.COMPARE:
        _check_compare(result, execution)

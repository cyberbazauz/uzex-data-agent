from __future__ import annotations

from typing import Any

from app.agent.query_plan import ExecutionPlan
from app.database.query_registry import DealQueryBuilder
from app.domain.enums import GroupField, Intent, Metric
from app.domain.errors import UnsupportedQuery
from app.handlers.base import HandlerResult
from app.handlers.common import apply_execution, build_result, measure


def handle(conn: Any, execution: ExecutionPlan) -> HandlerResult:
    metric = execution.plan.metric
    if metric not in (
        Metric.DEAL_COUNT,
        Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
        Metric.AMOUNT_SUM_UZS_CONVERTED,
        Metric.VOLUME_SUM_BY_UNIT,
    ):
        raise UnsupportedQuery("trend received an incompatible metric")

    granularity = [
        field
        for field in execution.plan.group_by
        if field in (GroupField.YEAR, GroupField.MONTH)
    ]
    if len(granularity) != 1:
        raise UnsupportedQuery(
            "trend requires exactly one grouping field: year or month"
        )

    builder = DealQueryBuilder()
    entity_evidence = apply_execution(builder, execution)
    measurement = measure(
        conn,
        builder,
        metric,
        execution.plan.currency_mode,
        execution.plan.group_by,
        order="group_asc",
    )
    return build_result(
        Intent.TREND,
        metric,
        measurement,
        execution,
        builder,
        entity_evidence,
        extra_notes=[
            "periods with no matching deals are absent and are not filled with zero"
        ],
    )

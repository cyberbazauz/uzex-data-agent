from __future__ import annotations

from typing import Any

from app.agent.query_plan import ExecutionPlan
from app.database.query_registry import DealQueryBuilder
from app.domain.enums import Intent, Metric
from app.domain.errors import UnsupportedQuery
from app.handlers.base import HandlerResult
from app.handlers.common import apply_execution, build_result, measure


def handle(conn: Any, execution: ExecutionPlan) -> HandlerResult:
    metric = execution.plan.metric
    if metric not in (
        Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
        Metric.AMOUNT_SUM_UZS_CONVERTED,
    ):
        raise UnsupportedQuery("sum_amount received an incompatible metric")

    builder = DealQueryBuilder()
    entity_evidence = apply_execution(builder, execution)
    has_groups = bool(execution.plan.group_by)
    measurement = measure(
        conn,
        builder,
        metric,
        execution.plan.currency_mode,
        execution.plan.group_by,
        order="group_asc" if has_groups else "none",
    )
    return build_result(
        Intent.SUM_AMOUNT,
        metric,
        measurement,
        execution,
        builder,
        entity_evidence,
    )

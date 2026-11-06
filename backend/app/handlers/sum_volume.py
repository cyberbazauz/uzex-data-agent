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
    if metric not in (Metric.VOLUME_SUM_BY_UNIT, Metric.DELIVERED_VOLUME_BY_UNIT):
        raise UnsupportedQuery("sum_volume received an incompatible metric")

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
        Intent.SUM_VOLUME,
        metric,
        measurement,
        execution,
        builder,
        entity_evidence,
        extra_notes=["physical volume = quantity_lots × lot_multiplier per unit_name"],
    )

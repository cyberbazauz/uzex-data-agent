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
        Metric.AMOUNT_AVERAGE_UZS_CONVERTED,
        Metric.UNIT_PRICE_SIMPLE_AVERAGE,
        Metric.UNIT_PRICE_WEIGHTED_AVERAGE,
    ):
        raise UnsupportedQuery("average received an incompatible metric")

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
    notes: list[str] = []
    if metric is Metric.UNIT_PRICE_SIMPLE_AVERAGE:
        notes.append("simple average unit price: unweighted mean of deal_amount / physical volume")
    elif metric is Metric.UNIT_PRICE_WEIGHTED_AVERAGE:
        notes.append(
            "weighted average unit price: SUM(deal_amount) / SUM(physical volume)"
        )
    return build_result(
        Intent.AVERAGE,
        metric,
        measurement,
        execution,
        builder,
        entity_evidence,
        extra_notes=notes,
    )

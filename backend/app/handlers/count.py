from __future__ import annotations

from typing import Any

from app.agent.query_plan import ExecutionPlan
from app.database.query_registry import DealQueryBuilder
from app.domain.enums import Intent, Metric
from app.handlers.base import HandlerResult
from app.handlers.common import apply_execution, build_result, measure


def handle(conn: Any, execution: ExecutionPlan) -> HandlerResult:
    builder = DealQueryBuilder()
    entity_evidence = apply_execution(builder, execution)
    has_groups = bool(execution.plan.group_by)
    measurement = measure(
        conn,
        builder,
        Metric.DEAL_COUNT,
        execution.plan.currency_mode,
        execution.plan.group_by,
        order="group_asc" if has_groups else "none",
    )
    return build_result(
        Intent.COUNT,
        Metric.DEAL_COUNT,
        measurement,
        execution,
        builder,
        entity_evidence,
    )

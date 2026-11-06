from __future__ import annotations

from typing import Any

from app.agent.query_plan import ExecutionPlan
from app.domain.enums import Intent, Metric
from app.domain.errors import UnsupportedQuery
from app.handlers.base import HandlerResult
from app.handlers.deal_lookup import select_deal_rows


def handle(conn: Any, execution: ExecutionPlan) -> HandlerResult:
    if execution.plan.metric is not Metric.RECORD_LOOKUP:
        raise UnsupportedQuery("filter_list requires metric=record_lookup")

    limit = execution.plan.limit or 10
    rows, matched_rows, filters, source_tables, notes = select_deal_rows(
        conn, execution, limit=limit
    )
    if matched_rows > len(rows):
        notes.append(
            f"showing the first {len(rows)} of {matched_rows} matching deals (hard limit {limit})"
        )
    return HandlerResult(
        intent=Intent.FILTER_LIST,
        metric=Metric.RECORD_LOOKUP,
        rows=rows,
        matched_rows=matched_rows,
        metric_sql="SELECT matching deal rows with a hard result limit",
        filters=filters,
        grouping=[],
        source_tables=source_tables,
        notes=notes,
    )

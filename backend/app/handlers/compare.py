from __future__ import annotations

from typing import Any

from app.agent.query_plan import ExecutionPlan
from app.database.models import Deal
from app.database.query_registry import DealQueryBuilder
from app.domain.enums import FilterField, Intent, Metric
from app.domain.errors import UnsupportedQuery
from app.handlers.base import HandlerResult, ResultRow
from app.handlers.common import (
    Measurement,
    build_result,
    measure,
)


def handle(conn: Any, execution: ExecutionPlan) -> HandlerResult:
    metric = execution.plan.metric
    if metric not in (
        Metric.DEAL_COUNT,
        Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
        Metric.AMOUNT_SUM_UZS_CONVERTED,
        Metric.VOLUME_SUM_BY_UNIT,
    ):
        raise UnsupportedQuery("compare received an incompatible metric")
    if not execution.plan.comparison:
        raise UnsupportedQuery("compare requires two explicit periods")
    if any(
        condition.field is FilterField.DEAL_DATE for condition in execution.filters
    ):
        raise UnsupportedQuery(
            "compare periods must be expressed only in comparison, not as deal_date filters"
        )

    non_date_filters = [
        condition
        for condition in execution.filters
        if condition.field is not FilterField.DEAL_DATE
    ]

    evidence_builder = DealQueryBuilder()
    evidence_builder.apply_filters(non_date_filters)
    entity_evidence = evidence_builder.apply_entities(execution.entities)

    rows: list[ResultRow] = []
    matched_rows = 0
    grouping: list[str] = ["period"]
    currency_method = None
    metric_sql = ""
    units: set[str] = set()
    last_builder = evidence_builder

    for period in (execution.plan.comparison.left, execution.plan.comparison.right):
        period_builder = DealQueryBuilder()
        period_builder.apply_filters(non_date_filters)
        period_builder.apply_entities(execution.entities)
        measurement = measure(
            conn,
            period_builder,
            metric,
            execution.plan.currency_mode,
            execution.plan.group_by,
            order="group_asc",
            extra_conditions=[Deal.deal_date.between(period.date_from, period.date_to)],
        )
        last_builder = period_builder
        metric_sql = measurement.metric_sql
        currency_method = measurement.currency_method
        for item in measurement.grouping:
            if item not in grouping:
                grouping.append(item)
        matched_rows += measurement.matched_rows
        for row in measurement.rows:
            if row.unit:
                units.add(row.unit)
            rows.append(
                row.model_copy(update={"group": {"period": period.label, **row.group}})
            )
        if not measurement.rows:
            rows.append(
                ResultRow(
                    group={"period": period.label},
                    value=None,
                    extra={"matched_rows": 0, "period_date_from": str(period.date_from), "period_date_to": str(period.date_to)},
                )
            )

    rows.sort(
        key=lambda row: (
            str(row.group.get("period", "")),
            str(row.group.get("currency", "")),
            str(row.group.get("unit_name", "")),
        )
    )
    measurement = Measurement(
        rows=rows,
        matched_rows=matched_rows,
        grouping=grouping,
        currency_method=currency_method,
        unit=next(iter(units)) if len(units) == 1 else None,
        metric_sql=metric_sql,
        notes=[
            "comparison periods are evaluated independently; a period with no rows is "
            "reported as absent data, not as a confirmed zero"
        ],
    )
    return build_result(
        Intent.COMPARE,
        metric,
        measurement,
        execution,
        last_builder,
        entity_evidence,
    )

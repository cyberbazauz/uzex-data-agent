from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.agent.query_plan import ExecutionPlan
from app.database.models import Contract, Deal
from app.database.query_registry import DealQueryBuilder
from app.domain.enums import Intent, Metric
from app.domain.errors import UnsupportedQuery
from app.handlers.base import HandlerResult, ResultRow
from app.handlers.common import (
    apply_execution,
    count_matching,
    entity_notes,
    evidence_filters,
)


def handle(conn: Any, execution: ExecutionPlan) -> HandlerResult:
    metric = execution.plan.metric
    if metric not in (Metric.AMOUNT_MIN, Metric.AMOUNT_MAX):
        raise UnsupportedQuery("min_max received an incompatible metric")

    builder = DealQueryBuilder()
    entity_evidence = apply_execution(builder, execution)
    builder.require_contract()
    matched_rows = count_matching(conn, builder)
    rows: list[ResultRow] = []

    if matched_rows:
        if metric is Metric.AMOUNT_MIN:
            amount_order = Deal.deal_amount.asc()
            rank_label = "min"
            metric_sql = "MIN(deals.deal_amount) per contracts.currency"
        else:
            amount_order = Deal.deal_amount.desc()
            rank_label = "max"
            metric_sql = "MAX(deals.deal_amount) per contracts.currency"

        ranked = builder.select(
            Deal.deal_no,
            Deal.deal_date,
            Deal.deal_amount,
            Contract.currency,
            Deal.unit_name,
            Deal.quantity_lots,
            Deal.lot_multiplier,
            (Deal.quantity_lots * Deal.lot_multiplier).label("physical_volume"),
            func.row_number()
            .over(
                partition_by=Contract.currency,
                order_by=[amount_order, Deal.deal_no.asc()],
            )
            .label("rank_no"),
        ).subquery("ranked_deals")
        statement = (
            select(ranked)
            .where(ranked.c.rank_no == 1)
            .order_by(ranked.c.currency.asc())
        )
        for row in conn.execute(statement).mappings():
            rows.append(
                ResultRow(
                    group={"currency": row["currency"]},
                    value=row["deal_amount"],
                    currency=row["currency"],
                    unit=row["unit_name"],
                    extra={
                        "deal_no": row["deal_no"],
                        "deal_date": row["deal_date"],
                        "quantity_lots": row["quantity_lots"],
                        "lot_multiplier": row["lot_multiplier"],
                        "physical_volume": row["physical_volume"],
                        "rank": rank_label,
                    },
                )
            )

    notes = [
        *entity_notes(execution),
        "minimum/maximum amounts are reported separately per contract currency",
    ]
    return HandlerResult(
        intent=Intent.MIN_MAX,
        metric=metric,
        rows=rows,
        matched_rows=matched_rows,
        metric_sql=metric_sql if matched_rows else "MIN/MAX over matching deals",
        filters=evidence_filters(execution, entity_evidence),
        grouping=["currency"],
        source_tables=builder.source_tables(),
        notes=notes,
    )

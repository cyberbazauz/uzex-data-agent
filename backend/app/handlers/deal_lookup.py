from __future__ import annotations

from typing import Any

from app.agent.query_plan import ExecutionPlan
from app.database.models import Deal
from app.database.query_registry import DealQueryBuilder
from app.domain.enums import FilterField, Intent, Metric
from app.domain.errors import UnsupportedQuery
from app.handlers.base import HandlerResult, ResultRow
from app.handlers.common import (
    apply_execution,
    count_matching,
    entity_notes,
    evidence_filters,
)


def _deal_select_columns(builder: DealQueryBuilder) -> list[Any]:
    contract = builder.require_contract()
    seller = builder.require_seller()
    buyer = builder.require_buyer()
    seller_broker = builder.require_seller_broker()
    buyer_broker = builder.require_buyer_broker()
    region = builder.require_warehouse_region()
    return [
        Deal.deal_no.label("deal_no"),
        Deal.deal_date.label("deal_date"),
        Deal.deal_time.label("deal_time"),
        Deal.trade_type.label("trade_type"),
        Deal.quantity_lots.label("quantity_lots"),
        Deal.deal_amount.label("deal_amount"),
        Deal.starting_price.label("starting_price"),
        Deal.contract_no.label("contract_no"),
        Deal.deal_product_name.label("deal_product_name"),
        Deal.lot_multiplier.label("lot_multiplier"),
        Deal.unit_name.label("unit_name"),
        Deal.contract_type.label("contract_type"),
        Deal.contract_form.label("contract_form"),
        Deal.status.label("status"),
        Deal.seller_inn.label("seller_inn"),
        seller.name.label("seller_name"),
        Deal.buyer_inn.label("buyer_inn"),
        buyer.name.label("buyer_name"),
        Deal.seller_broker_code.label("seller_broker_code"),
        seller_broker.name.label("seller_broker_name"),
        Deal.buyer_broker_code.label("buyer_broker_code"),
        buyer_broker.name.label("buyer_broker_name"),
        Deal.warehouse_region_id.label("warehouse_region_id"),
        region.canonical_name_ru.label("warehouse_region_ru"),
        region.canonical_name_uz.label("warehouse_region_uz"),
        Deal.payment_date.label("payment_date"),
        Deal.delivery_date.label("delivery_date"),
        Deal.delivered_lots.label("delivered_lots"),
        contract.product_name.label("product_name"),
        contract.currency.label("currency"),
        contract.hs_code.label("hs_code"),
        contract.product_group.label("product_group"),
        contract.product_category.label("product_category"),
        contract.industry.label("industry"),
    ]


def select_deal_rows(
    conn: Any,
    execution: ExecutionPlan,
    *,
    limit: int = 20,
) -> tuple[list[ResultRow], int, list[dict[str, Any]], list[str], list[str]]:
    builder = DealQueryBuilder()
    entity_evidence = apply_execution(builder, execution)
    columns = _deal_select_columns(builder)
    matched_rows = count_matching(conn, builder)
    if matched_rows == 0:
        return (
            [],
            0,
            evidence_filters(execution, entity_evidence),
            builder.source_tables(),
            entity_notes(execution),
        )
    statement = builder.select(*columns).order_by(Deal.deal_no.asc()).limit(limit)
    raw_rows = conn.execute(statement).mappings().all()
    rows = [
        ResultRow(
            group={"deal_no": row["deal_no"]},
            extra=dict(row),
        )
        for row in raw_rows
    ]
    return (
        rows,
        matched_rows,
        evidence_filters(execution, entity_evidence),
        builder.source_tables(),
        entity_notes(execution),
    )


def handle(conn: Any, execution: ExecutionPlan) -> HandlerResult:
    if not any(
        condition.field is FilterField.DEAL_NO for condition in execution.filters
    ):
        raise UnsupportedQuery("deal_lookup requires a deal_no filter")
    rows, matched_rows, filters, source_tables, notes = select_deal_rows(
        conn, execution, limit=20
    )
    return HandlerResult(
        intent=Intent.DEAL_LOOKUP,
        metric=Metric.RECORD_LOOKUP,
        rows=rows,
        matched_rows=matched_rows,
        metric_sql="SELECT deal row (and linked contract/participant/broker/region data)",
        filters=filters,
        grouping=[],
        source_tables=source_tables,
        notes=notes,
    )

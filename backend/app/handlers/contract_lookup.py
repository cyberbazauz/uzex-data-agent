from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.agent.query_plan import ExecutionPlan
from app.database.models import Contract, Participant, Region
from app.domain.enums import FilterField, Intent, Metric, Operator
from app.domain.errors import UnsupportedQuery
from app.handlers.base import HandlerResult, ResultRow
from app.handlers.common import entity_notes, evidence_filters


def handle(conn: Any, execution: ExecutionPlan) -> HandlerResult:
    conditions = [
        condition
        for condition in execution.filters
        if condition.field is FilterField.CONTRACT_NO
    ]
    if not conditions:
        raise UnsupportedQuery("contract_lookup requires a contract_no filter")

    values: list[str] = []
    for condition in conditions:
        if condition.operator not in (Operator.EQ, Operator.IN):
            raise UnsupportedQuery("contract_no supports only exact or in lookup")
        raw = condition.value if isinstance(condition.value, list) else [condition.value]
        for item in raw:
            text = str(item).strip()
            if not text:
                raise UnsupportedQuery("contract_no must not be blank")
            values.append(text)

    seller = aliased(Participant, name="contract_seller")
    region = aliased(Region, name="contract_region")
    base = (
        select(
            Contract.contract_no.label("contract_no"),
            Contract.product_name.label("product_name"),
            Contract.brand.label("brand"),
            Contract.product_size.label("product_size"),
            Contract.grade.label("grade"),
            Contract.product_type.label("product_type"),
            Contract.product_class.label("product_class"),
            Contract.hs_code.label("hs_code"),
            Contract.product_group.label("product_group"),
            Contract.product_category.label("product_category"),
            Contract.industry.label("industry"),
            Contract.unit_name.label("unit_name"),
            Contract.lot_multiplier.label("lot_multiplier"),
            Contract.currency.label("currency"),
            Contract.base_price.label("base_price"),
            Contract.pledge_percent.label("pledge_percent"),
            Contract.delivery_basis.label("delivery_basis"),
            Contract.warehouse_region_raw.label("warehouse_region_raw"),
            Contract.warehouse_region_id.label("warehouse_region_id"),
            region.canonical_name_ru.label("warehouse_region_ru"),
            region.canonical_name_uz.label("warehouse_region_uz"),
            Contract.warehouse_address.label("warehouse_address"),
            Contract.trade_type.label("trade_type"),
            Contract.contract_type.label("contract_type"),
            Contract.contract_form.label("contract_form"),
            Contract.payment_days.label("payment_days"),
            Contract.delivery_days.label("delivery_days"),
            Contract.packaging.label("packaging"),
            Contract.seller_inn.label("seller_inn"),
            seller.name.label("seller_name"),
            Contract.registration_date.label("registration_date"),
            Contract.valid_until.label("valid_until"),
        )
        .select_from(Contract)
        .join(seller, seller.inn == Contract.seller_inn)
        .join(region, region.region_id == Contract.warehouse_region_id)
        .where(Contract.contract_no.in_(values))
    )

    count_statement = (
        select(func.count())
        .select_from(Contract)
        .where(Contract.contract_no.in_(values))
    )
    matched_rows = int(conn.execute(count_statement).scalar_one())
    rows: list[ResultRow] = []
    if matched_rows:
        for row in conn.execute(base.order_by(Contract.contract_no.asc()).limit(20)).mappings():
            rows.append(
                ResultRow(
                    group={"contract_no": row["contract_no"]},
                    extra=dict(row),
                )
            )

    return HandlerResult(
        intent=Intent.CONTRACT_LOOKUP,
        metric=Metric.RECORD_LOOKUP,
        rows=rows,
        matched_rows=matched_rows,
        metric_sql="SELECT contract card by contract_no",
        filters=evidence_filters(execution, []),
        grouping=[],
        source_tables=["contracts", "participants", "regions"],
        notes=entity_notes(execution),
    )

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func

from app.agent.query_plan import ExecutionPlan
from app.database.connection import get_active_dataset
from app.database.models import Deal
from app.database.query_registry import (
    DealQueryBuilder,
    GroupSpec,
    currency_conditions,
)
from app.domain.enums import CURRENCY_UZS, CURRENCY_USD, CurrencyMode, GroupField, Metric
from app.domain.errors import UnsupportedQuery, UnverifiedQuery
from app.handlers.base import HandlerResult, ResultRow


def dataset_context(conn: Any) -> tuple[str, str]:
    dataset = get_active_dataset(conn)
    if dataset is None:
        raise UnverifiedQuery("no active dataset is available")
    if dataset.get("validation_status") != "PASSED":
        raise UnverifiedQuery("active dataset did not pass validation")
    return str(dataset["dataset_version"]), str(dataset["max_deal_date"])


def apply_execution(builder: DealQueryBuilder, execution: ExecutionPlan) -> list[dict[str, Any]]:
    builder.apply_filters(execution.filters)
    return builder.apply_entities(execution.entities)


def entity_notes(execution: ExecutionPlan) -> list[str]:
    notes: list[str] = []
    for entity in execution.entities:
        mention = entity.mention or entity.label
        notes.append(
            f"entity {entity.kind.value} '{mention}' resolved to "
            f"'{entity.label}' ({entity.id}) via {entity.matched_by}"
        )
    notes.extend(execution.evidence_notes)
    if execution.product_contract_count is not None:
        notes.append(
            "the product base name is linked through contract_no and covers "
            f"{execution.product_contract_count} contract(s)"
        )
    return notes


def evidence_filters(
    execution: ExecutionPlan, entity_evidence: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    filters = [condition.model_dump(mode="json") for condition in execution.filters]
    filters.extend(entity_evidence)
    return filters


def count_matching(conn: Any, builder: DealQueryBuilder) -> int:
    statement = builder.select(func.count(func.distinct(Deal.deal_no)))
    return int(conn.execute(statement).scalar_one())


def merge_group_fields(
    required: tuple[GroupField, ...], requested: list[GroupField]
) -> list[GroupField]:
    result: list[GroupField] = []
    for item in [*required, *requested]:
        if item not in result:
            result.append(item)
    return result


def build_grouped_columns(
    group_specs: list[GroupSpec], value_expression: Any
) -> list[Any]:
    columns: list[Any] = []
    for index, spec in enumerate(group_specs):
        columns.append(spec.key.label(f"g{index}"))
    for index, spec in enumerate(group_specs):
        label = spec.label if spec.label is not None else spec.key
        columns.append(label.label(f"l{index}"))
    columns.append(value_expression.label("metric_value"))
    return columns


def grouped_group_by(group_specs: list[GroupSpec]) -> list[Any]:
    expressions: list[Any] = []
    for spec in group_specs:
        expressions.append(spec.key)
        if spec.label is not None:
            expressions.append(spec.label)
    return expressions


def _order_expressions(
    group_specs: list[GroupSpec], value_expression: Any, order: str
) -> list[Any]:
    if order == "metric_desc":
        return [value_expression.desc(), *[spec.key.asc() for spec in group_specs]]
    if order == "metric_asc":
        return [value_expression.asc(), *[spec.key.asc() for spec in group_specs]]
    if order == "group_asc":
        return [spec.key.asc() for spec in group_specs]
    if order == "group_desc":
        return [spec.key.desc() for spec in group_specs]
    return []


def fetch_grouped_rows(
    conn: Any,
    statement: Any,
    group_specs: list[GroupSpec],
    fixed_currency: str | None,
) -> list[ResultRow]:
    raw_rows = conn.execute(statement).all()
    count = len(group_specs)
    rows: list[ResultRow] = []
    for raw in raw_rows:
        keys = list(raw[:count])
        labels = list(raw[count : count * 2])
        value = raw[count * 2]
        group = {
            spec.output_name: keys[index] for index, spec in enumerate(group_specs)
        }
        label_map = {
            spec.output_name: str(labels[index])
            for index, spec in enumerate(group_specs)
            if spec.label is not None and labels[index] is not None
        }
        unit = group.get("unit_name")
        currency = group.get("currency") or fixed_currency
        rows.append(
            ResultRow(
                group=group,
                labels=label_map,
                value=value,
                unit=str(unit) if unit is not None else None,
                currency=str(currency) if currency is not None else None,
            )
        )
    return rows


@dataclass(frozen=True)
class MetricPreparation:
    value_expression: Any
    required_group_fields: tuple[GroupField, ...]
    metric_sql: str
    currency_method: str | None = None
    extra_conditions: tuple[Any, ...] = ()
    fixed_currency: str | None = None
    value_kind: str = "money"


def prepare_metric(
    builder: DealQueryBuilder, metric: Metric, currency_mode: CurrencyMode
) -> MetricPreparation:
    if metric is Metric.DEAL_COUNT:
        return MetricPreparation(
            value_expression=func.count(func.distinct(Deal.deal_no)),
            required_group_fields=(),
            metric_sql="COUNT(DISTINCT deals.deal_no)",
        )

    if metric is Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY:
        builder.require_contract()
        if currency_mode is CurrencyMode.NOT_APPLICABLE:
            raise UnsupportedQuery(
                "a money sum requires an explicit currency_mode and must never "
                "add UZS and USD amounts together"
            )
        conditions, group_currency, method = currency_conditions(currency_mode)
        if currency_mode is CurrencyMode.UZS_CONVERTED:
            raise UnsupportedQuery(
                "amount_sum_native_by_currency cannot use the converted UZS mode"
            )
        fixed_currency = None
        if currency_mode is CurrencyMode.USD_NATIVE:
            fixed_currency = CURRENCY_USD
        elif currency_mode is CurrencyMode.UZS_NATIVE:
            fixed_currency = CURRENCY_UZS
        return MetricPreparation(
            value_expression=func.sum(Deal.deal_amount),
            required_group_fields=(GroupField.CURRENCY,) if group_currency else (),
            metric_sql="SUM(deals.deal_amount)",
            currency_method=method,
            extra_conditions=tuple(conditions),
            fixed_currency=fixed_currency,
        )

    if metric in (Metric.AMOUNT_SUM_UZS_CONVERTED, Metric.AMOUNT_AVERAGE_UZS_CONVERTED):
        if currency_mode is not CurrencyMode.UZS_CONVERTED:
            raise UnsupportedQuery(
                f"{metric.value} requires currency_mode=uzs_converted"
            )
        expression = builder.converted_amount()
        aggregate = func.sum(expression) if metric is Metric.AMOUNT_SUM_UZS_CONVERTED else func.avg(expression)
        return MetricPreparation(
            value_expression=aggregate,
            required_group_fields=(),
            metric_sql=(
                "SUM(CASE WHEN contracts.currency='Доллар США' THEN "
                "deals.deal_amount*usd_rates.uzs_per_usd ELSE deals.deal_amount END)"
                if metric is Metric.AMOUNT_SUM_UZS_CONVERTED
                else "AVG(CASE WHEN contracts.currency='Доллар США' THEN "
                "deals.deal_amount*usd_rates.uzs_per_usd ELSE deals.deal_amount END)"
            ),
            currency_method=(
                "UZS amount for UZS contracts + USD amount × USD/UZS rate on deal date"
            ),
            fixed_currency=CURRENCY_UZS,
        )

    if metric is Metric.VOLUME_SUM_BY_UNIT:
        return MetricPreparation(
            value_expression=func.sum(builder.physical_volume(delivered=False)),
            required_group_fields=(GroupField.UNIT_NAME,),
            metric_sql="SUM(deals.quantity_lots * deals.lot_multiplier)",
            value_kind="volume",
        )

    if metric is Metric.DELIVERED_VOLUME_BY_UNIT:
        return MetricPreparation(
            value_expression=func.sum(builder.physical_volume(delivered=True)),
            required_group_fields=(GroupField.UNIT_NAME,),
            metric_sql="SUM(deals.delivered_lots * deals.lot_multiplier)",
            extra_conditions=(Deal.delivered_lots.is_not(None),),
            value_kind="volume",
        )

    if metric is Metric.DELIVERED_LOTS_SUM:
        return MetricPreparation(
            value_expression=func.sum(Deal.delivered_lots),
            required_group_fields=(GroupField.UNIT_NAME,),
            metric_sql="SUM(deals.delivered_lots)",
            extra_conditions=(Deal.delivered_lots.is_not(None),),
            value_kind="lots",
        )

    if metric in (
        Metric.UNIT_PRICE_SIMPLE_AVERAGE,
        Metric.UNIT_PRICE_WEIGHTED_AVERAGE,
    ):
        builder.require_contract()
        volume = builder.physical_volume(delivered=False)
        if metric is Metric.UNIT_PRICE_SIMPLE_AVERAGE:
            expression = func.avg(Deal.deal_amount / volume)
            metric_sql = (
                "AVG(deals.deal_amount / (deals.quantity_lots * deals.lot_multiplier))"
            )
        else:
            expression = func.sum(Deal.deal_amount) / func.sum(volume)
            metric_sql = (
                "SUM(deals.deal_amount) / SUM(deals.quantity_lots * deals.lot_multiplier)"
            )
        return MetricPreparation(
            value_expression=expression,
            required_group_fields=(GroupField.CURRENCY, GroupField.UNIT_NAME),
            metric_sql=metric_sql,
            currency_method="native unit price by contract currency; no cross-currency comparison",
            value_kind="price",
        )

    raise UnsupportedQuery(f"metric {metric.value} is handled by a dedicated handler")


@dataclass
class Measurement:
    rows: list[ResultRow] = field(default_factory=list)
    matched_rows: int = 0
    grouping: list[str] = field(default_factory=list)
    currency_method: str | None = None
    unit: str | None = None
    metric_sql: str = ""
    notes: list[str] = field(default_factory=list)


def measure(
    conn: Any,
    builder: DealQueryBuilder,
    metric: Metric,
    currency_mode: CurrencyMode,
    requested_groups: list[GroupField] | None = None,
    *,
    order: str = "metric_desc",
    limit: int | None = None,
    extra_conditions: list[Any] | None = None,
) -> Measurement:
    preparation = prepare_metric(builder, metric, currency_mode)
    builder.add_conditions(preparation.extra_conditions)
    if extra_conditions:
        builder.add_conditions(extra_conditions)

    groups = merge_group_fields(
        preparation.required_group_fields, requested_groups or []
    )
    group_specs = [builder.group_spec(field) for field in groups]

    if builder.missing_rate_count(conn) > 0:
        raise UnverifiedQuery(
            "at least one USD deal in scope has no USD rate for its deal date"
        )

    matched_rows = count_matching(conn, builder)
    if matched_rows == 0:
        return Measurement(
            rows=[],
            matched_rows=0,
            grouping=[field.value for field in groups],
            currency_method=preparation.currency_method,
            metric_sql=preparation.metric_sql,
        )

    columns = build_grouped_columns(group_specs, preparation.value_expression)
    statement = builder.select(*columns)
    if group_specs:
        statement = statement.group_by(*grouped_group_by(group_specs))
    order_expressions = _order_expressions(
        group_specs, preparation.value_expression, order
    )
    if order_expressions:
        statement = statement.order_by(*order_expressions)
    if limit is not None:
        statement = statement.limit(limit)

    rows = fetch_grouped_rows(
        conn, statement, group_specs, preparation.fixed_currency
    )
    units = {row.unit for row in rows if row.unit is not None}
    unit = next(iter(units)) if len(units) == 1 else None
    notes: list[str] = []
    if len(units) > 1:
        notes.append("values are kept separate per unit_name and are not added together")
    return Measurement(
        rows=rows,
        matched_rows=matched_rows,
        grouping=[field.value for field in groups],
        currency_method=preparation.currency_method,
        unit=unit,
        metric_sql=preparation.metric_sql,
        notes=notes,
    )


def build_result(
    intent: Intent,
    metric: Metric,
    measurement: Measurement,
    execution: ExecutionPlan,
    builder: DealQueryBuilder,
    entity_evidence: list[dict[str, Any]],
    extra_notes: list[str] | None = None,
) -> HandlerResult:
    notes = [*entity_notes(execution), *measurement.notes]
    if extra_notes:
        notes.extend(extra_notes)
    return HandlerResult(
        intent=intent,
        metric=metric,
        rows=measurement.rows,
        matched_rows=measurement.matched_rows,
        metric_sql=measurement.metric_sql,
        filters=evidence_filters(execution, entity_evidence),
        grouping=measurement.grouping,
        source_tables=builder.source_tables(),
        currency_method=measurement.currency_method,
        unit=measurement.unit,
        notes=notes,
    )

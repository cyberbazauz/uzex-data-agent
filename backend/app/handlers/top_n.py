from __future__ import annotations

from typing import Any

from app.agent.query_plan import ExecutionPlan
from app.database.models import Contract
from app.database.query_registry import DealQueryBuilder
from app.domain.enums import (
    ClarificationCode,
    CurrencyMode,
    Direction,
    EntityRole,
    GroupField,
    Intent,
    MatchKind,
    Metric,
    OrderField,
    Subject,
)
from app.domain.errors import AmbiguousEntity, UnsupportedQuery
from app.handlers.base import HandlerResult
from app.handlers.common import (
    Measurement,
    apply_execution,
    build_result,
    count_matching,
    evidence_filters,
    measure,
)

PARTICIPANT_GROUPS = (GroupField.SELLER_INN, GroupField.BUYER_INN)
BROKER_GROUPS = (GroupField.SELLER_BROKER_CODE, GroupField.BUYER_BROKER_CODE)


def _role_for(execution: ExecutionPlan, kind: MatchKind) -> EntityRole | None:
    roles = {
        entity.role
        for entity in execution.entities
        if entity.kind is kind and entity.role is not None
    }
    if len(roles) == 1:
        return roles.pop()
    return None


def _resolve_group_fields(execution: ExecutionPlan) -> list[GroupField]:
    fields = list(execution.plan.group_by)
    subject = execution.plan.subject

    if subject is Subject.PARTICIPANTS:
        selected = [field for field in fields if field in PARTICIPANT_GROUPS]
        if len(set(selected)) > 1:
            raise AmbiguousEntity(
                ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS,
                "top participants cannot mix seller and buyer sides",
            )
        if not selected:
            role = _role_for(execution, MatchKind.PARTICIPANT)
            if role is EntityRole.SELLER:
                fields.append(GroupField.SELLER_INN)
            elif role is EntityRole.BUYER:
                fields.append(GroupField.BUYER_INN)
            else:
                raise AmbiguousEntity(
                    ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS,
                    "top participants requires an explicit seller or buyer side",
                )
    elif subject is Subject.BROKERS:
        selected = [field for field in fields if field in BROKER_GROUPS]
        if len(set(selected)) > 1:
            raise AmbiguousEntity(
                ClarificationCode.BROKER_SIDE_AMBIGUOUS,
                "top brokers cannot mix seller and buyer sides",
            )
        if not selected:
            role = _role_for(execution, MatchKind.BROKER)
            if role is EntityRole.BROKER_SELLER:
                fields.append(GroupField.SELLER_BROKER_CODE)
            elif role is EntityRole.BROKER_BUYER:
                fields.append(GroupField.BUYER_BROKER_CODE)
            else:
                raise AmbiguousEntity(
                    ClarificationCode.BROKER_SIDE_AMBIGUOUS,
                    "top brokers requires an explicit seller or buyer side",
                )
    elif subject is Subject.DEALS:
        if not fields:
            raise UnsupportedQuery(
                "top deals requires an explicit grouping field (for example contract_no)"
            )
    else:
        raise UnsupportedQuery(f"top_n is not supported for subject {subject.value}")

    return fields


def _direction(execution: ExecutionPlan) -> str:
    for order in execution.plan.order_by:
        if order.field is OrderField.METRIC:
            return "metric_asc" if order.direction is Direction.ASC else "metric_desc"
    return "metric_desc"


def _currencies_in_scope(conn: Any, execution: ExecutionPlan) -> list[str]:
    builder = DealQueryBuilder()
    apply_execution(builder, execution)
    builder.require_contract()
    statement = builder.select(Contract.currency).distinct().order_by(Contract.currency)
    return [str(value) for value in conn.execute(statement).scalars().all()]


def handle(conn: Any, execution: ExecutionPlan) -> HandlerResult:
    metric = execution.plan.metric
    if metric not in (
        Metric.DEAL_COUNT,
        Metric.AMOUNT_SUM_UZS_CONVERTED,
        Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
        Metric.VOLUME_SUM_BY_UNIT,
        Metric.DELIVERED_LOTS_SUM,
    ):
        raise UnsupportedQuery("top_n received an incompatible metric")

    group_fields = _resolve_group_fields(execution)
    direction = _direction(execution)
    limit = execution.plan.limit or 10

    builder = DealQueryBuilder()
    entity_evidence = apply_execution(builder, execution)

    if metric is Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY:
        currencies = _currencies_in_scope(conn, execution)
        if not currencies:
            return build_result(
                Intent.TOP_N,
                metric,
                Measurement(metric_sql="native amount ranking per contract currency"),
                execution,
                builder,
                entity_evidence,
            )
        combined_rows = []
        matched_rows = 0
        grouping: list[str] = []
        currency_method = None
        unit = None
        metric_sql = "SUM(deals.deal_amount) per contracts.currency"
        for currency in currencies:
            currency_builder = DealQueryBuilder()
            apply_execution(currency_builder, execution)
            measurement = measure(
                conn,
                currency_builder,
                metric,
                CurrencyMode.NATIVE_BY_CURRENCY,
                group_fields,
                order=direction,
                limit=limit,
                extra_conditions=[Contract.currency == currency],
            )
            combined_rows.extend(measurement.rows)
            matched_rows += measurement.matched_rows
            grouping = measurement.grouping
            currency_method = measurement.currency_method
            unit = measurement.unit
            builder = currency_builder
        return build_result(
            Intent.TOP_N,
            metric,
            Measurement(
                rows=combined_rows,
                matched_rows=matched_rows,
                grouping=grouping,
                currency_method=currency_method,
                unit=unit,
                metric_sql=metric_sql,
                notes=["each currency is ranked separately; rankings are not compared across currencies"],
            ),
            execution,
            builder,
            entity_evidence,
            extra_notes=[f"top {limit} per currency"],
        )

    measurement = measure(
        conn,
        builder,
        metric,
        execution.plan.currency_mode,
        group_fields,
        order=direction,
        limit=limit,
    )
    return build_result(
        Intent.TOP_N,
        metric,
        measurement,
        execution,
        builder,
        entity_evidence,
        extra_notes=[f"top {limit} by {metric.value}"],
    )

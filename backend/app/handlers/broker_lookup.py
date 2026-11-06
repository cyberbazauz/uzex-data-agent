from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.agent.query_plan import ExecutionPlan
from app.database.models import Broker, Region
from app.domain.enums import (
    ClarificationCode,
    FilterField,
    Intent,
    MatchKind,
    Metric,
    Operator,
)
from app.domain.errors import AmbiguousEntity, UnsupportedQuery
from app.domain.normalization import normalize_text
from app.handlers.base import HandlerResult, ResultRow
from app.handlers.common import entity_notes, evidence_filters


def _looks_like_code(value: str) -> bool:
    return len(value) > 2 and value[:2].upper() == "BK" and value[2:].isdigit()


def _extract_values(execution: ExecutionPlan) -> tuple[list[str], list[str], list[str]]:
    ids = [
        entity.id for entity in execution.entities if entity.kind is MatchKind.BROKER
    ]
    codes: list[str] = []
    names: list[str] = []
    for condition in execution.filters:
        if condition.field not in (
            FilterField.SELLER_BROKER_CODE,
            FilterField.BUYER_BROKER_CODE,
        ):
            continue
        if condition.operator not in (Operator.EQ, Operator.IN):
            raise UnsupportedQuery("broker lookup supports exact values only")
        raw = condition.value if isinstance(condition.value, list) else [condition.value]
        for item in raw:
            text = str(item).strip()
            if not text:
                continue
            if _looks_like_code(text):
                codes.append(text.upper())
            else:
                names.append(text)
    return ids, codes, names


def handle(conn: Any, execution: ExecutionPlan) -> HandlerResult:
    ids, codes, names = _extract_values(execution)
    if not ids and not codes and not names:
        raise UnsupportedQuery(
            "broker_lookup requires a broker code, an exact resolved name, or a broker entity"
        )

    region = aliased(Region, name="broker_region")
    statement = (
        select(
            Broker.broker_code.label("broker_code"),
            Broker.name.label("name"),
            Broker.inn.label("inn"),
            Broker.region_id.label("region_id"),
            region.canonical_name_ru.label("region_ru"),
            region.canonical_name_uz.label("region_uz"),
            Broker.region_raw.label("region_raw"),
        )
        .select_from(Broker)
        .join(region, region.region_id == Broker.region_id, isouter=True)
    )

    ambiguous_by_name = False
    if ids:
        condition = Broker.broker_code.in_(ids)
    elif codes:
        condition = Broker.broker_code.in_(codes)
    else:
        condition = Broker.name_normalized.in_([normalize_text(name) for name in names])
        ambiguous_by_name = True
    statement = statement.where(condition)

    count_statement = select(func.count()).select_from(Broker).where(condition)
    matched_rows = int(conn.execute(count_statement).scalar_one())
    if matched_rows > 1 and ambiguous_by_name:
        options = [
            f"{row['name']} ({row['broker_code']})"
            for row in conn.execute(
                select(Broker.name, Broker.broker_code)
                .where(condition)
                .order_by(Broker.name)
                .limit(10)
            ).mappings()
        ]
        raise AmbiguousEntity(
            ClarificationCode.BROKER_AMBIGUOUS,
            "more than one broker matches the exact normalized name",
            options,
        )

    rows: list[ResultRow] = []
    if matched_rows:
        for row in conn.execute(
            statement.order_by(Broker.broker_code.asc()).limit(20)
        ).mappings():
            rows.append(
                ResultRow(
                    group={"broker_code": row["broker_code"]},
                    labels={"name": row["name"]},
                    extra=dict(row),
                )
            )

    return HandlerResult(
        intent=Intent.BROKER_LOOKUP,
        metric=Metric.RECORD_LOOKUP,
        rows=rows,
        matched_rows=matched_rows,
        metric_sql="SELECT broker by exact broker_code or exact normalized name",
        filters=evidence_filters(execution, []),
        grouping=[],
        source_tables=["brokers", "regions"],
        notes=entity_notes(execution),
    )

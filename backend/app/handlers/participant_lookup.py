from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from app.agent.query_plan import ExecutionPlan
from app.database.models import Participant, Region
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


def _extract_values(execution: ExecutionPlan) -> tuple[list[str], list[str]]:
    ids = [
        entity.id
        for entity in execution.entities
        if entity.kind is MatchKind.PARTICIPANT
    ]
    inns: list[str] = []
    names: list[str] = []
    for condition in execution.filters:
        if condition.field not in (FilterField.SELLER_INN, FilterField.BUYER_INN):
            continue
        if condition.operator not in (Operator.EQ, Operator.IN):
            raise UnsupportedQuery("participant lookup supports exact values only")
        raw = condition.value if isinstance(condition.value, list) else [condition.value]
        for item in raw:
            text = str(item).strip()
            if text.isdigit() and len(text) == 9:
                inns.append(text)
            elif text:
                names.append(text)
    return ids, inns, names


def handle(conn: Any, execution: ExecutionPlan) -> HandlerResult:
    ids, inns, names = _extract_values(execution)
    if not ids and not inns and not names:
        raise UnsupportedQuery(
            "participant_lookup requires an INN, an exact resolved name, or a participant entity"
        )

    region = aliased(Region, name="participant_region")
    statement = (
        select(
            Participant.inn.label("inn"),
            Participant.name.label("name"),
            Participant.company_region_id.label("company_region_id"),
            region.canonical_name_ru.label("company_region_ru"),
            region.canonical_name_uz.label("company_region_uz"),
            Participant.company_region_raw.label("company_region_raw"),
            Participant.activity_type.label("activity_type"),
            Participant.role.label("role"),
            Participant.vat_payer.label("vat_payer"),
            Participant.vat_payer_raw.label("vat_payer_raw"),
        )
        .select_from(Participant)
        .join(region, region.region_id == Participant.company_region_id, isouter=True)
    )

    if ids:
        conditions = [Participant.inn.in_(ids)]
        ambiguous_by_name = False
    elif inns:
        conditions = [Participant.inn.in_(inns)]
        ambiguous_by_name = False
    else:
        normalized = [normalize_text(name) for name in names]
        conditions = [Participant.name_normalized.in_(normalized)]
        ambiguous_by_name = True
    statement = statement.where(or_(*conditions) if len(conditions) > 1 else conditions[0])

    count_statement = select(func.count()).select_from(Participant).where(
        or_(*conditions) if len(conditions) > 1 else conditions[0]
    )
    matched_rows = int(conn.execute(count_statement).scalar_one())
    if matched_rows > 1 and ambiguous_by_name:
        options = [
            f"{row['name']} (INN {row['inn']})"
            for row in conn.execute(
                select(Participant.name, Participant.inn)
                .where(or_(*conditions) if len(conditions) > 1 else conditions[0])
                .order_by(Participant.name)
                .limit(10)
            ).mappings()
        ]
        raise AmbiguousEntity(
            ClarificationCode.PARTICIPANT_AMBIGUOUS,
            "more than one participant matches the exact normalized name",
            options,
        )

    rows: list[ResultRow] = []
    if matched_rows:
        for row in conn.execute(
            statement.order_by(Participant.name.asc()).limit(20)
        ).mappings():
            rows.append(
                ResultRow(
                    group={"inn": row["inn"]},
                    labels={"name": row["name"]},
                    extra=dict(row),
                )
            )

    return HandlerResult(
        intent=Intent.PARTICIPANT_LOOKUP,
        metric=Metric.RECORD_LOOKUP,
        rows=rows,
        matched_rows=matched_rows,
        metric_sql="SELECT participant by exact INN or exact normalized name",
        filters=evidence_filters(execution, []),
        grouping=[],
        source_tables=["participants", "regions"],
        notes=entity_notes(execution),
    )

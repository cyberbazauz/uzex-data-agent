from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.database.models import Broker
from app.domain.enums import ClarificationCode
from app.domain.errors import AmbiguousEntity, EntityNotFound
from app.domain.normalization import normalize_text
from app.entities.common import fuzzy_matches, suggestion_options


@dataclass(frozen=True)
class BrokerMatch:
    broker_code: str
    name: str
    matched_by: str


def is_broker_code(value: str) -> bool:
    text = value.strip().upper()
    return len(text) >= 3 and text.startswith("BK") and text[2:].isdigit()


def _option(row: tuple[str, str]) -> str:
    return f"{row[1]} ({row[0]})"


def resolve_broker(
    conn: Connection,
    mention: str,
    *,
    allow_fuzzy: bool = True,
) -> BrokerMatch:
    """Resolve a broker by exact broker code, then exact normalized name."""
    text = mention.strip()
    if not text:
        raise EntityNotFound("broker", mention)

    if is_broker_code(text):
        code = text.upper()
        row = conn.execute(
            select(Broker.broker_code, Broker.name).where(Broker.broker_code == code)
        ).first()
        if row is None:
            raise EntityNotFound(
                "broker", code, f"broker code {code} was not found"
            )
        return BrokerMatch(
            broker_code=row[0], name=row[1], matched_by="broker_code"
        )

    normalized = normalize_text(text)
    exact_rows = conn.execute(
        select(Broker.broker_code, Broker.name)
        .where(Broker.name_normalized == normalized)
        .order_by(Broker.broker_code.asc())
        .limit(10)
    ).all()
    if len(exact_rows) == 1:
        row = exact_rows[0]
        return BrokerMatch(
            broker_code=row[0], name=row[1], matched_by="exact_normalized_name"
        )
    if len(exact_rows) > 1:
        raise AmbiguousEntity(
            ClarificationCode.BROKER_AMBIGUOUS,
            "more than one broker has the same exact normalized name",
            [_option((row[0], row[1])) for row in exact_rows],
        )

    if allow_fuzzy:
        candidates = conn.execute(
            select(Broker.broker_code, Broker.name, Broker.name_normalized)
        ).all()
        matches = fuzzy_matches(
            text,
            [(row[2], (row[0], row[1])) for row in candidates],
            cutoff=0.72,
            limit=5,
        )
        if matches:
            raise AmbiguousEntity(
                ClarificationCode.BROKER_AMBIGUOUS,
                "no exact broker match; confirm one of the possible candidates",
                suggestion_options(matches, _option),
            )

    raise EntityNotFound("broker", text)

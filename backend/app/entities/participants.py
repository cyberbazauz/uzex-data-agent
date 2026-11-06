from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.database.models import Participant
from app.domain.enums import ClarificationCode
from app.domain.errors import AmbiguousEntity, EntityNotFound
from app.domain.normalization import normalize_text
from app.entities.common import fuzzy_matches, suggestion_options


@dataclass(frozen=True)
class ParticipantMatch:
    inn: str
    name: str
    matched_by: str


def is_inn(value: str) -> bool:
    text = value.strip()
    return text.isdigit() and len(text) == 9


def _option(row: tuple[str, str]) -> str:
    return f"{row[1]} (INN {row[0]})"


def resolve_participant(
    conn: Connection,
    mention: str,
    *,
    allow_fuzzy: bool = True,
) -> ParticipantMatch:
    """Resolve a participant by exact INN or exact normalized name.

    Fuzzy candidates are returned only as clarification options.  They are
    never selected automatically for a financial or quantity query.
    """
    text = mention.strip()
    if not text:
        raise EntityNotFound("participant", mention)

    if is_inn(text):
        row = conn.execute(
            select(Participant.inn, Participant.name).where(Participant.inn == text)
        ).first()
        if row is None:
            raise EntityNotFound(
                "participant", text, f"participant INN {text} was not found"
            )
        return ParticipantMatch(inn=row[0], name=row[1], matched_by="inn")

    normalized = normalize_text(text)
    exact_rows = conn.execute(
        select(Participant.inn, Participant.name)
        .where(Participant.name_normalized == normalized)
        .order_by(Participant.name.asc(), Participant.inn.asc())
        .limit(10)
    ).all()
    if len(exact_rows) == 1:
        row = exact_rows[0]
        return ParticipantMatch(
            inn=row[0], name=row[1], matched_by="exact_normalized_name"
        )
    if len(exact_rows) > 1:
        raise AmbiguousEntity(
            ClarificationCode.PARTICIPANT_AMBIGUOUS,
            "more than one participant has the same exact normalized name",
            [_option((row[0], row[1])) for row in exact_rows],
        )

    if allow_fuzzy:
        candidates = conn.execute(
            select(
                Participant.inn,
                Participant.name,
                Participant.name_normalized,
            )
        ).all()
        matches = fuzzy_matches(
            text,
            [
                (row[2], (row[0], row[1]))
                for row in candidates
            ],
            cutoff=0.68,
            limit=5,
        )
        if matches:
            raise AmbiguousEntity(
                ClarificationCode.PARTICIPANT_AMBIGUOUS,
                "no exact participant match; confirm one of the possible candidates",
                suggestion_options(matches, _option),
            )

    raise EntityNotFound("participant", text)

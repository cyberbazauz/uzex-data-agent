from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.database.models import Region, RegionAlias
from app.domain.enums import ClarificationCode
from app.domain.errors import AmbiguousEntity, EntityNotFound
from app.domain.normalization import normalize_region
from app.entities.common import fuzzy_matches


@dataclass(frozen=True)
class RegionMatch:
    region_id: int
    label: str
    matched_by: str


def _region_names(conn: Connection) -> dict[int, tuple[str, str]]:
    rows = conn.execute(
        select(Region.region_id, Region.canonical_name_ru, Region.canonical_name_uz)
    ).all()
    return {int(row[0]): (row[1], row[2]) for row in rows}


def region_display(conn: Connection, region_id: int, language: str) -> str:
    names = _region_names(conn)
    row = names.get(region_id)
    if row is None:
        return ""
    if language == "ru":
        return row[0]
    if language == "uz_cyrl":
        from app.domain.regions import region_display as static_display

        return static_display(region_id, "uz_cyrl") or row[1]
    return row[1]


def resolve_region(
    conn: Connection,
    mention: str,
    *,
    language: str = "ru",
    allow_fuzzy: bool = True,
) -> RegionMatch:
    """Resolve a region name or id through the seeded alias table."""
    text = mention.strip()
    if not text:
        raise EntityNotFound("region", mention)

    names = _region_names(conn)

    if text.isdigit():
        region_id = int(text)
        names_for_id = names.get(region_id)
        if names_for_id is None:
            raise EntityNotFound("region", text, f"region id {region_id} was not found")
        return RegionMatch(
            region_id=region_id,
            label=region_display(conn, region_id, language),
            matched_by="region_id",
        )

    normalized = normalize_region(text)
    exact_rows = conn.execute(
        select(RegionAlias.region_id, RegionAlias.alias_raw)
        .where(RegionAlias.alias_normalized == normalized)
        .order_by(RegionAlias.region_id)
        .limit(10)
    ).all()
    exact_ids = sorted({int(row[0]) for row in exact_rows})
    if len(exact_ids) == 1:
        region_id = exact_ids[0]
        return RegionMatch(
            region_id=region_id,
            label=region_display(conn, region_id, language),
            matched_by="region_alias",
        )
    if len(exact_ids) > 1:
        raise AmbiguousEntity(
            ClarificationCode.REGION_AMBIGUOUS,
            "the region name matches more than one region",
            [region_display(conn, region_id, language) for region_id in exact_ids],
        )

    if allow_fuzzy:
        aliases = conn.execute(
            select(RegionAlias.alias_normalized, RegionAlias.region_id)
        ).all()
        matches = fuzzy_matches(
            text,
            [(row[0], int(row[1])) for row in aliases],
            cutoff=0.6,
            limit=30,
        )
        seen: set[int] = set()
        options: list[str] = []
        for match in matches:
            region_id = int(match.payload)
            if region_id in seen:
                continue
            seen.add(region_id)
            options.append(
                f"{region_display(conn, region_id, language)} "
                "(possible match, not confirmed)"
            )
            if len(options) >= 5:
                break
        if options:
            raise AmbiguousEntity(
                ClarificationCode.REGION_AMBIGUOUS,
                "no exact region match; confirm which region is meant",
                options,
            )

    raise EntityNotFound("region", text)


def resolve_region_id(
    conn: Connection,
    value: object,
    *,
    language: str = "ru",
) -> int:
    if isinstance(value, bool):
        raise EntityNotFound("region", str(value))
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return resolve_region(conn, text, language=language).region_id

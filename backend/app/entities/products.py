from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from app.database.models import Contract, Deal
from app.domain.enums import ClarificationCode
from app.domain.errors import AmbiguousEntity, EntityNotFound
from app.domain.normalization import normalize_text
from app.entities.common import fuzzy_matches, suggestion_options


@dataclass(frozen=True)
class ProductMatch:
    id: str
    label: str
    matched_by: str
    contract_count: int


PRODUCT_SOURCE_LABEL = {
    "product_name": "contract product",
    "deal_product_name": "deal product label",
}


def _base_labels(conn: Connection, *, raw: str | None = None, normalized: str | None = None) -> list[str]:
    statement = select(Contract.product_name).distinct()
    if raw is not None:
        statement = statement.where(Contract.product_name == raw)
    else:
        statement = statement.where(Contract.product_name_normalized == normalized)
    return [str(value) for value in conn.execute(statement.order_by(Contract.product_name)).scalars().all()]


def _deal_labels(conn: Connection, *, raw: str | None = None, normalized: str | None = None) -> list[str]:
    statement = select(Deal.deal_product_name).distinct()
    if raw is not None:
        statement = statement.where(Deal.deal_product_name == raw)
    else:
        statement = statement.where(Deal.deal_product_name_normalized == normalized)
    return [str(value) for value in conn.execute(statement.order_by(Deal.deal_product_name)).scalars().all()]


def _base_contract_count(conn: Connection, label: str) -> int:
    return int(
        conn.execute(
            select(func.count())
            .select_from(Contract)
            .where(Contract.product_name == label)
        ).scalar_one()
    )


def _deal_contract_count(conn: Connection, label: str) -> int:
    return int(
        conn.execute(
            select(func.count(func.distinct(Deal.contract_no)))
            .select_from(Deal)
            .where(Deal.deal_product_name == label)
        ).scalar_one()
    )


def _hs_code_contract_count(conn: Connection, code: str) -> int:
    return int(
        conn.execute(
            select(func.count())
            .select_from(Contract)
            .where(Contract.hs_code == code)
        ).scalar_one()
    )


def _match(source: str, label: str, conn: Connection) -> ProductMatch:
    if source == "product_name":
        return ProductMatch(
            id=label,
            label=label,
            matched_by="product_name",
            contract_count=_base_contract_count(conn, label),
        )
    return ProductMatch(
        id=label,
        label=label,
        matched_by="deal_product_name",
        contract_count=_deal_contract_count(conn, label),
    )


def _ambiguous_labels(source: str, labels: list[str]) -> AmbiguousEntity:
    return AmbiguousEntity(
        ClarificationCode.PRODUCT_AMBIGUOUS,
        "the normalized product name matches more than one original spelling",
        [f"{label} ({PRODUCT_SOURCE_LABEL[source]})" for label in labels[:10]],
    )


def resolve_product(
    conn: Connection,
    mention: str,
    *,
    prefer: str | None = None,
    allow_fuzzy: bool = True,
) -> ProductMatch:
    """Resolve a product by HS code, exact base name, or exact deal label.

    A base product name is not a unique key: it may cover many contracts.  The
    match carries the number of linked contracts so evidence can state how many
    contract numbers were included.  A normalized name that maps to multiple
    original spellings is returned as a clarification, never selected silently.
    """
    text = mention.strip()
    if not text:
        raise EntityNotFound("product", mention)

    if text.isdigit() and 4 <= len(text) <= 12:
        count = _hs_code_contract_count(conn, text)
        if count == 0:
            raise EntityNotFound(
                "product", text, f"HS code {text} was not found in contracts"
            )
        return ProductMatch(
            id=text,
            label=text,
            matched_by="hs_code",
            contract_count=count,
        )

    normalized = normalize_text(text)
    if prefer == "deal_product_name":
        sources = ("deal_product_name", "product_name")
    else:
        sources = ("product_name", "deal_product_name")

    for source in sources:
        raw_labels = (
            _base_labels(conn, raw=text)
            if source == "product_name"
            else _deal_labels(conn, raw=text)
        )
        if len(raw_labels) == 1:
            return _match(source, raw_labels[0], conn)
        if len(raw_labels) > 1:
            raise _ambiguous_labels(source, raw_labels)

        normalized_labels = (
            _base_labels(conn, normalized=normalized)
            if source == "product_name"
            else _deal_labels(conn, normalized=normalized)
        )
        if len(normalized_labels) == 1:
            return _match(source, normalized_labels[0], conn)
        if len(normalized_labels) > 1:
            raise _ambiguous_labels(source, normalized_labels)

    if allow_fuzzy:
        base_candidates = conn.execute(
            select(Contract.product_name, Contract.product_name_normalized).distinct()
        ).all()
        deal_candidates = conn.execute(
            select(Deal.deal_product_name, Deal.deal_product_name_normalized).distinct()
        ).all()
        candidates: list[tuple[str, tuple[str, str]]] = [
            (row[1], (row[0], "product_name")) for row in base_candidates
        ]
        candidates.extend(
            (row[1], (row[0], "deal_product_name")) for row in deal_candidates
        )
        matches = fuzzy_matches(text, candidates, cutoff=0.72, limit=5)
        if matches:
            raise AmbiguousEntity(
                ClarificationCode.PRODUCT_AMBIGUOUS,
                "no exact product match; confirm one of the possible candidates",
                suggestion_options(
                    matches,
                    lambda payload: f"{payload[0]} ({PRODUCT_SOURCE_LABEL[payload[1]]})",
                ),
            )

    raise EntityNotFound("product", text)

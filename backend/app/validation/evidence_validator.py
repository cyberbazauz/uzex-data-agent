from __future__ import annotations

from typing import Literal

from app.domain.errors import UnverifiedQuery
from app.handlers.base import Evidence

KNOWN_SOURCE_TABLES = frozenset(
    {
        "deals",
        "contracts",
        "participants",
        "brokers",
        "regions",
        "region_aliases",
        "usd_rates",
        "dataset_meta",
    }
)

MatchedRowsMode = Literal["positive", "zero", "non_negative"]


def _fail(message: str) -> None:
    raise UnverifiedQuery(message)


def validate_evidence(
    evidence: Evidence,
    *,
    dataset_version: str,
    dataset_cutoff: str,
    matched_rows_mode: MatchedRowsMode,
) -> None:
    """Check that evidence really identifies the executed, active dataset."""
    if not evidence.source_tables:
        _fail("evidence has no source tables")
    unknown = [table for table in evidence.source_tables if table not in KNOWN_SOURCE_TABLES]
    if unknown:
        _fail(f"evidence references unknown source tables: {unknown}")
    if len(set(evidence.source_tables)) != len(evidence.source_tables):
        _fail("evidence contains duplicate source tables")

    if evidence.dataset_version != dataset_version:
        _fail("evidence dataset version does not match the active dataset")
    if evidence.dataset_cutoff != dataset_cutoff:
        _fail("evidence dataset cutoff does not match the active dataset")
    if not evidence.execution_id:
        _fail("evidence has no execution id")
    if not evidence.metric.strip():
        _fail("evidence has no metric description")
    if len(evidence.metric) > 400:
        _fail("evidence metric description is unreasonably long")
    if evidence.matched_rows < 0:
        _fail("evidence matched_rows is negative")
    if not isinstance(evidence.grouping, list):
        _fail("evidence grouping must be a list")
    if len(evidence.grouping) > 10:
        _fail("evidence grouping has too many fields")

    if matched_rows_mode == "positive" and evidence.matched_rows <= 0:
        _fail("FOUND evidence must report at least one matched row")
    if matched_rows_mode == "zero" and evidence.matched_rows != 0:
        _fail("NOT_FOUND evidence must report zero matched rows")
    if matched_rows_mode == "non_negative" and evidence.matched_rows < 0:
        _fail("evidence matched_rows must not be negative")

    if len(evidence.filters) > 50:
        _fail("evidence has too many filters")
    for index, condition in enumerate(evidence.filters):
        if not isinstance(condition, dict):
            _fail(f"evidence filter {index} is not an object")
        if "field" not in condition or "operator" not in condition or "value" not in condition:
            _fail(f"evidence filter {index} is missing field/operator/value")
        if not str(condition["field"]).strip():
            _fail(f"evidence filter {index} has a blank field")
        if str(condition["operator"]) not in {"eq", "in", "gte", "lte", "between", "contains"}:
            _fail(f"evidence filter {index} has an unsupported operator")

    for note in evidence.notes:
        if not isinstance(note, str):
            _fail("evidence notes must be strings")

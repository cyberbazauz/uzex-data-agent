from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.agent.query_plan import ExecutionPlan
from app.domain.enums import FilterField, Intent
from app.domain.errors import UnverifiedQuery
from app.handlers.base import HandlerResult
from app.responses.formatting import (
    format_date,
    format_integer,
    format_month,
    format_value,
)

# The deterministic formatter only groups digits with a no-break space.  A
# regular space is treated as a separator between independent tokens, which
# avoids interpreting adjacent text (for example "2024 100") as one number.
_NUMBER_TOKEN = re.compile(
    r"-?(?:\d{1,3}(?:[\u00a0\u202f]\d{3})+|\d+)(?:[.,]\d+)*"
)
_GROUP_SEPARATORS = ("\u00a0", "\u202f")
_VALUE_KINDS = ("record", "count", "volume", "price", "money")


def _fail(message: str) -> None:
    raise UnverifiedQuery(message)


def _normalize(token: str) -> str:
    text = token
    for separator in _GROUP_SEPARATORS:
        text = text.replace(separator, "")
    return text.replace(",", ".")


def _numeric_alias(token: str) -> str | None:
    """Return a canonical decimal spelling, or None for dates/identifiers."""
    text = _normalize(token)
    if text.count(".") > 1:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    rendered = format(value.normalize(), "f")
    if rendered in {"-0", "0", "-0.0", "0.0"}:
        return "0"
    return rendered


def _extract_tokens(text: str) -> list[str]:
    return [match.group(0) for match in _NUMBER_TOKEN.finditer(text)]


def _collect(value: Any, out: list[str]) -> None:
    """Collect every confirmed display variant for one result value."""
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, datetime):
        out.append(value.isoformat())
        out.append(format_date(value))
        return
    if isinstance(value, date):
        out.append(value.isoformat())
        out.append(format_date(value))
        return
    if isinstance(value, (int, Decimal)):
        out.append(str(value))
        if isinstance(value, int) or value == value.to_integral_value():
            out.append(format_integer(int(value)))
        for kind in _VALUE_KINDS:
            try:
                out.append(format_value(value, kind))
            except ValueError:
                pass
        return
    if isinstance(value, str):
        out.append(value)
        if len(value) >= 10 and value[4] == "-" and value[7] == "-":
            try:
                out.append(format_date(date.fromisoformat(value[:10])))
            except ValueError:
                pass
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect(item, out)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _collect(item, out)


def _row_texts(result: HandlerResult) -> list[str]:
    texts: list[str] = []
    for row in result.rows:
        _collect(row.group, texts)
        _collect(row.labels, texts)
        _collect(row.extra, texts)
        _collect(row.value, texts)
        _collect(row.unit, texts)
        _collect(row.currency, texts)
        for key, value in row.group.items():
            if key == "month" and value is not None:
                texts.append(format_month(value))
            if isinstance(value, (date, datetime)):
                texts.append(format_date(value))
    return texts


def _filter_texts(execution: ExecutionPlan) -> list[str]:
    texts: list[str] = []
    for condition in execution.plan.filters:
        if condition.field is not FilterField.DEAL_DATE:
            continue
        values = condition.value if isinstance(condition.value, list) else [condition.value]
        for value in values:
            texts.append(str(value))
            try:
                parsed = date.fromisoformat(str(value))
            except ValueError:
                continue
            texts.append(format_date(parsed))
    comparison = execution.plan.comparison
    if comparison is not None:
        for period in (comparison.left, comparison.right):
            texts.append(period.label)
            texts.append(str(period.date_from))
            texts.append(str(period.date_to))
            texts.append(format_date(period.date_from))
            texts.append(format_date(period.date_to))
    return texts


def _structural_texts(execution: ExecutionPlan, result: HandlerResult) -> list[str]:
    texts = [
        str(result.matched_rows),
        format_integer(result.matched_rows),
        str(len(result.rows)),
        format_integer(len(result.rows)),
    ]
    if result.unit:
        texts.append(result.unit)
    if execution.plan.intent is Intent.TOP_N:
        limit = execution.plan.limit or 10
        texts.append(str(limit))
        texts.append(format_integer(limit))
        # The localized header renders the limit after a literal hyphen
        # ("Топ-2"/"Топ-10"), which the tokenizer sees as a signed number.
        texts.append(f"-{limit}")
        texts.extend(str(rank) for rank in range(1, len(result.rows) + 1))
    return texts


def _allowed_tokens(execution: ExecutionPlan, result: HandlerResult) -> tuple[set[str], set[str]]:
    texts = _row_texts(result)
    texts.extend(_structural_texts(execution, result))
    texts.extend(_filter_texts(execution))

    textual: set[str] = set()
    numeric: set[str] = set()
    for text in texts:
        for token in _extract_tokens(str(text)):
            textual.add(_normalize(token))
            alias = _numeric_alias(token)
            if alias is not None:
                numeric.add(alias)
    return textual, numeric


def validate_answer_grounding(
    answer: str,
    execution: ExecutionPlan,
    result: HandlerResult,
) -> None:
    """Reject a FOUND answer that contains a number not proven by the result.

    Every numeric token in the deterministic answer must be reproducible from
    one of the following PostgreSQL-confirmed sources:

    * exact handler row values, group keys, labels and fact fields;
    * the confirmed ``matched_rows`` count and number of returned rows;
    * dates and periods taken from validated plan filters;
    * for TOP_N only, the requested limit and the structural rank numbers.

    Similarity scores, prompt text, model output and the user question are never
    allowed sources, so an injected or hallucinated number fails closed with
    ``UnverifiedQuery``.
    """
    if not isinstance(answer, str):
        _fail("answer grounding requires text")
    allowed_textual, allowed_numeric = _allowed_tokens(execution, result)

    for token in _extract_tokens(answer):
        normalized = _normalize(token)
        if normalized in allowed_textual:
            continue
        alias = _numeric_alias(token)
        if alias is not None and alias in allowed_numeric:
            continue
        _fail("answer contains a numeric value that PostgreSQL did not confirm")

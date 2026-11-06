from __future__ import annotations

import re
from typing import Any

# A follow-up that names only a year, e.g. "А за 2023?", "2023?", "и за 2023
# год?".  It is rewritten only when the previous answer was a FOUND deal count,
# so the adapter never invents a metric or a filter.
_YEAR_FOLLOW_UP = re.compile(
    r"^\s*(?:(?:а|и|and)\s+)?(?:за\s+)?(?P<year>(?:19|20)\d{2})\s*"
    r"(?:год\w*)?\s*\?*\s*$",
    re.IGNORECASE,
)


def rewrite_follow_up(
    question: str, context: dict[str, Any] | None
) -> tuple[str, bool]:
    """Return the question to execute and whether context was applied.

    The rewrite is intentionally narrow.  It preserves the previous metric
    (deal count) when the user only replaces the period.  Anything less
    explicit is passed through unchanged so the planner can ask for a
    clarification instead of guessing.
    """

    if context is None or len(question) > 80:
        return question, False
    if context.get("last_status") != "FOUND":
        return question, False
    if context.get("last_metric") != "deal_count":
        return question, False
    match = _YEAR_FOLLOW_UP.match(question)
    if match is None:
        return question, False
    return f"Сколько сделок было в {match.group('year')} году?", True

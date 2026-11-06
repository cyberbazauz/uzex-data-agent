from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.domain.normalization import normalize_text


@dataclass(frozen=True)
class FuzzyMatch:
    score: float
    normalized: str
    payload: object


def fuzzy_matches(
    mention: str,
    candidates: Iterable[tuple[str, object]],
    *,
    cutoff: float = 0.72,
    limit: int = 5,
) -> list[FuzzyMatch]:
    """Return candidate matches as suggestions only.

    PostgreSQL's pg_trgm does not produce trigrams for non-ASCII text on a
    database created with a C locale, so fuzzy candidate discovery is done in
    Python with a deterministic ratio.  The result is never used to identify an
    entity automatically; callers turn it into a clarification request.
    """
    normalized_mention = normalize_text(mention)
    if not normalized_mention:
        return []

    scored: list[FuzzyMatch] = []
    for normalized_candidate, payload in candidates:
        if not normalized_candidate:
            continue
        if normalized_candidate == normalized_mention:
            score = 1.0
        else:
            score = SequenceMatcher(
                None, normalized_mention, normalized_candidate
            ).ratio()
        if score >= cutoff:
            scored.append(FuzzyMatch(score, normalized_candidate, payload))

    scored.sort(key=lambda item: (-item.score, item.normalized))
    seen: set[str] = set()
    unique: list[FuzzyMatch] = []
    for item in scored:
        if item.normalized in seen:
            continue
        seen.add(item.normalized)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def suggestion_options(
    matches: Sequence[FuzzyMatch], label_for: Callable[[object], str]
) -> list[str]:
    """Render up to five suggestion strings, always marked as unconfirmed."""
    options: list[str] = []
    for item in matches[:5]:
        options.append(f"{label_for(item.payload)} (possible match, not confirmed)")
    return options

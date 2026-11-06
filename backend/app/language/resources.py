from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.domain.normalization import normalize_text


def phrase_present(normalized_text: str, key: str) -> bool:
    """Match a resource keyword without matching an unrelated substring.

    Short keys such as the Russian word for "sum" (``сум``) must only match a
    whole word, otherwise the word "sum" inside "amount" would be mistaken for
    a currency.  Longer keys are treated as stems so that inflected forms such
    as "долларах" still match "доллар".
    """
    if not key:
        return False
    padded = f" {normalized_text} "
    if len(key) <= 4:
        return f" {key} " in padded
    return re.search(rf"(?<!\w){re.escape(key)}", normalized_text) is not None

LANGUAGE_DIR = Path(__file__).resolve().parent
SUPPORTED_LOCALES: tuple[str, ...] = ("ru", "uz_latn", "uz_cyrl")
LOCALE_FILES = {
    "ru": "ru.json",
    "uz_latn": "uz_latn.json",
    "uz_cyrl": "uz_cyrl.json",
}
SYNONYM_FILES = {
    "ru": "synonyms_ru.json",
    "uz_latn": "synonyms_uz_latn.json",
    "uz_cyrl": "synonyms_uz_cyrl.json",
}


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"language resource {path.name} must contain a JSON object")
    return payload


@lru_cache(maxsize=None)
def locale_resource(locale: str) -> dict[str, Any]:
    if locale not in LOCALE_FILES:
        raise KeyError(f"unsupported locale {locale}")
    return _read_json(LANGUAGE_DIR / LOCALE_FILES[locale])


@lru_cache(maxsize=None)
def locale_messages(locale: str) -> dict[str, str]:
    payload = locale_resource(locale).get("messages", {})
    return {str(key): str(value) for key, value in payload.items()}


@lru_cache(maxsize=None)
def answer_templates(locale: str) -> dict[str, str]:
    """Deterministic answer sentence templates for one locale."""
    payload = locale_resource(locale).get("answers", {})
    return {str(key): str(value) for key, value in payload.items()}


@lru_cache(maxsize=None)
def answer_labels(locale: str) -> dict[str, Any]:
    """Display labels used by the formatter, never by SQL."""
    payload = locale_resource(locale).get("labels", {})
    return dict(payload)


def _normalized_list(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    result: list[str] = []
    for value in values:
        text = normalize_text(str(value))
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _normalized_map(values: Any) -> dict[str, str]:
    if not isinstance(values, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in values.items():
        normalized = normalize_text(str(key))
        if normalized:
            result[normalized] = str(value)
    return result


@dataclass(frozen=True)
class Synonyms:
    """Normalized keyword resources for one language/script."""

    locale: str
    markers: tuple[str, ...]
    intent: dict[str, tuple[str, ...]]
    groups: dict[str, tuple[str, ...]]
    filters: dict[str, dict[str, str]]
    statuses: dict[str, str]
    units: dict[str, str]
    months: dict[str, int]
    quarters: dict[str, int]
    native_currency_markers: tuple[str, ...]
    usd_markers: tuple[str, ...]
    uzs_markers: tuple[str, ...]
    converted_markers: tuple[str, ...]
    unsupported: tuple[str, ...]
    injection: tuple[str, ...]
    stop_words: tuple[str, ...]

    def intent_keywords(self, name: str) -> tuple[str, ...]:
        return self.intent.get(name, ())

    def has_any(self, normalized_text: str, values: tuple[str, ...]) -> bool:
        return any(phrase_present(normalized_text, value) for value in values)

    def groups_for(self, normalized_text: str) -> list[str]:
        """Return group keys found in the question, longest phrase first."""
        found: list[tuple[int, str]] = []
        for group, phrases in self.groups.items():
            for phrase in phrases:
                if phrase_present(normalized_text, phrase):
                    found.append((len(phrase), group))
                    break
        found.sort(key=lambda item: (-item[0], item[1]))
        return [group for _, group in found]

    def filter_matches(self, field: str, normalized_text: str) -> list[str]:
        values = self.filters.get(field, {})
        # Longest keyword first so «экспортный» wins over a shorter prefix and
        # «доллар» is not confused with an unrelated substring.
        ordered = sorted(values.items(), key=lambda item: -len(item[0]))
        return [value for key, value in ordered if phrase_present(normalized_text, key)]

    def unit_matches(self, normalized_text: str) -> list[str]:
        ordered = sorted(self.units.items(), key=lambda item: -len(item[0]))
        result: list[str] = []
        for key, value in ordered:
            if phrase_present(normalized_text, key) and value not in result:
                result.append(value)
        return result

    def status_matches(self, normalized_text: str) -> list[str]:
        ordered = sorted(self.statuses.items(), key=lambda item: -len(item[0]))
        result: list[str] = []
        for key, value in ordered:
            if phrase_present(normalized_text, key) and value not in result:
                result.append(value)
        return result

    def month_from_text(self, normalized_text: str) -> int | None:
        # Longest month name first, but a month token must be a whole word.
        ordered = sorted(self.months.items(), key=lambda item: -len(item[0]))
        padded = f" {normalized_text} "
        for key, value in ordered:
            if f" {key} " in padded:
                return int(value)
        return None

    def quarter_from_text(self, normalized_text: str) -> int | None:
        ordered = sorted(self.quarters.items(), key=lambda item: -len(item[0]))
        for key, value in ordered:
            if key in normalized_text:
                return int(value)
        return None


@lru_cache(maxsize=None)
def synonyms(locale: str) -> Synonyms:
    if locale not in SYNONYM_FILES:
        raise KeyError(f"unsupported locale {locale}")
    payload = _read_json(LANGUAGE_DIR / SYNONYM_FILES[locale])

    intent_payload = payload.get("intent", {})
    group_payload = payload.get("groups", {})
    filter_payload = payload.get("filters", {})

    return Synonyms(
        locale=locale,
        markers=_normalized_list(locale_resource(locale).get("markers", [])),
        intent={
            str(name): _normalized_list(values)
            for name, values in intent_payload.items()
        },
        groups={
            str(name): _normalized_list(values)
            for name, values in group_payload.items()
        },
        filters={
            str(name): _normalized_map(values)
            for name, values in filter_payload.items()
        },
        statuses=_normalized_map(payload.get("statuses", {})),
        units=_normalized_map(payload.get("units", {})),
        months={
            normalize_text(str(key)): int(value)
            for key, value in payload.get("months", {}).items()
            if normalize_text(str(key))
        },
        quarters={
            normalize_text(str(key)): int(value)
            for key, value in payload.get("quarters", {}).items()
            if normalize_text(str(key))
        },
        native_currency_markers=_normalized_list(
            payload.get("native_currency_markers", [])
        ),
        usd_markers=_normalized_list(payload.get("usd_markers", [])),
        uzs_markers=_normalized_list(payload.get("uzs_markers", [])),
        converted_markers=_normalized_list(payload.get("converted_markers", [])),
        unsupported=_normalized_list(payload.get("unsupported", [])),
        injection=_normalized_list(payload.get("injection", [])),
        stop_words=_normalized_list(payload.get("stop_words", [])),
    )

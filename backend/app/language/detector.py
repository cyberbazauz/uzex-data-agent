from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.domain.enums import Language, Script
from app.domain.normalization import normalize_text
from app.language.resources import locale_resource, synonyms

_CYRILLIC = re.compile(r"[А-Яа-яЁёЎўҚқҒғҲҳ]")
_LATIN = re.compile(r"[A-Za-z]")
_UZ_SPECIFIC_CYRILLIC = re.compile(r"[ЎўҚқҒғҲҳ]")
_UZ_LATIN_HINTS = re.compile(
    r"\b(?:o'|g'|sh|ch|ng|yo|yu|ya)[a-z]|\b(?:nechta|qancha|bitim|shartnoma|"
    r"sotuvchi|xaridor|broker|kompaniya|ishtirokchi|hudud|viloyat|summa|hajm|"
    r"yetkazilgan|holat|valyuta|dollar|so'm|yil|oy|o'rtacha|minimal|maksimal|"
    r"top|eng|ko'rsat|ro'yxat|solishtir|taqqosla|dinamika|trend|ma'lumot)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LanguageDetection:
    language: Language
    script: Script
    locale: str
    confidence: float

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.6


def _marker_score(normalized: str, values: tuple[str, ...]) -> int:
    padded = f" {normalized} "
    return sum(1 for value in values if f" {value} " in padded)


def detect_language(question: str) -> LanguageDetection:
    """Detect language and script separately, as required by the spec.

    The detector is deliberately conservative: it never learns from the data and
    never changes the user's original text.  Cyrillic is split into Russian and
    Uzbek Cyrillic using characters and vocabulary that are specific to Uzbek.
    """
    text = unicodedata.normalize("NFKC", question or "")
    cyrillic_count = len(_CYRILLIC.findall(text))
    latin_count = len(_LATIN.findall(text))

    if cyrillic_count == 0 and latin_count == 0:
        return LanguageDetection(Language.RU, Script.NOT_APPLICABLE, "ru", 0.0)

    normalized = normalize_text(text)

    if cyrillic_count >= latin_count and cyrillic_count > 0:
        uz_specific = len(_UZ_SPECIFIC_CYRILLIC.findall(text))
        uz_markers = _marker_score(normalized, synonyms("uz_cyrl").markers)
        ru_markers = _marker_score(normalized, synonyms("ru").markers)
        uz_score = uz_specific * 3 + uz_markers
        ru_score = ru_markers
        if uz_score > ru_score or (uz_specific > 0 and ru_score == 0):
            confidence = min(0.99, 0.6 + 0.05 * (uz_score - ru_score))
            return LanguageDetection(Language.UZ, Script.CYRILLIC, "uz_cyrl", confidence)
        confidence = 0.9 if ru_score > 0 else 0.55
        return LanguageDetection(Language.RU, Script.CYRILLIC, "ru", confidence)

    latin_synonyms = synonyms("uz_latn")
    latin_markers = _marker_score(normalized, latin_synonyms.markers)
    if latin_markers > 0 or _UZ_LATIN_HINTS.search(text):
        confidence = min(0.99, 0.7 + 0.03 * latin_markers)
    else:
        # Latin script without Uzbek vocabulary is treated as low-confidence
        # Uzbek rather than silently pretending it is Russian.
        confidence = 0.4
    return LanguageDetection(Language.UZ, Script.LATIN, "uz_latn", confidence)


def locale_metadata(locale: str) -> dict[str, object]:
    payload = locale_resource(locale)
    return {
        "locale": payload.get("locale", locale),
        "language": payload.get("language"),
        "script": payload.get("script"),
    }

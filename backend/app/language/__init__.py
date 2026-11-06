from app.language.detector import LanguageDetection, detect_language, locale_metadata
from app.language.resources import (
    SUPPORTED_LOCALES,
    Synonyms,
    answer_labels,
    answer_templates,
    locale_messages,
    locale_resource,
    synonyms,
)

__all__ = [
    "LanguageDetection",
    "SUPPORTED_LOCALES",
    "Synonyms",
    "answer_labels",
    "answer_templates",
    "detect_language",
    "locale_messages",
    "locale_metadata",
    "locale_resource",
    "synonyms",
]

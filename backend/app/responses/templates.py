from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.enums import Language, Script
from app.language import answer_labels, answer_templates, locale_messages
from app.responses.formatting import locale_for


class TemplateError(RuntimeError):
    """A locale resource is missing a key required by the response layer."""


@dataclass(frozen=True)
class LocaleContext:
    locale: str
    messages: dict[str, str]
    answers: dict[str, str]
    labels: dict[str, Any]

    def message(self, key: str, **values: object) -> str:
        template = self.messages.get(key)
        if template is None:
            raise TemplateError(f"missing locale message: {key} ({self.locale})")
        return template.format(**values)

    def answer(self, key: str, **values: object) -> str:
        template = self.answers.get(key)
        if template is None:
            raise TemplateError(f"missing answer template: {key} ({self.locale})")
        return template.format(**values)

    def label(self, key: str) -> str:
        value = self.labels.get(key)
        if value is None:
            raise TemplateError(f"missing label: {key} ({self.locale})")
        return str(value)

    def group_label(self, key: str) -> str:
        groups = self.labels.get("groups")
        if isinstance(groups, dict) and key in groups:
            return str(groups[key])
        return key

    def source_label(self, table: str) -> str:
        tables = self.labels.get("source_tables")
        if isinstance(tables, dict) and table in tables:
            return str(tables[table])
        return table


def context_for(language: Language, script: Script) -> LocaleContext:
    locale = locale_for(language, script)
    return context_for_locale(locale)


def context_for_locale(locale: str) -> LocaleContext:
    return LocaleContext(
        locale=locale,
        messages=locale_messages(locale),
        answers=answer_templates(locale),
        labels=answer_labels(locale),
    )

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.agent.query_plan import ExecutionPlan
from app.domain.errors import UnverifiedQuery
from app.handlers.base import HandlerResult
from app.validation.answer_grounding import validate_answer_grounding


@dataclass(frozen=True)
class VerifiedFactBundle:
    """The only input an answer generator is allowed to see.

    ``facts`` and ``evidence`` are produced after result/evidence validation.
    ``deterministic_answer`` is the already validated template answer and acts
    as the identity allowlist for the generated text.
    """

    status: str
    facts: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    deterministic_answer: str = ""


class AnswerGenerator(Protocol):
    name: str

    def generate(self, bundle: VerifiedFactBundle) -> str | None:
        ...


class TemplateAnswerGenerator:
    """Always safe: returns the deterministic, already grounded answer."""

    name = "deterministic"

    def generate(self, bundle: VerifiedFactBundle) -> str | None:
        return bundle.deterministic_answer


class CallableAnswerGenerator:
    """Small adapter used by tests and by future qualified providers."""

    def __init__(self, responder: Any, *, name: str = "injected") -> None:
        self._responder = responder
        self.name = name

    def generate(self, bundle: VerifiedFactBundle) -> str | None:
        return self._responder(bundle)


_PROPER_NOUN = re.compile(r"(?<![\w])[A-ZА-ЯЁЎҒҚҲ][\w'’\-]{2,}")
_QUOTED = re.compile(r"[«\"']([^»\"']{2,200})[»\"']")
_TOKEN = re.compile(r"[\w'’\-]+", re.UNICODE)
# A model usually writes thousands with a plain space ("106 647"), while the
# deterministic formatter uses a no-break space.  Collapse only genuine
# digit groups (1-3 leading digits followed by groups of exactly 3) so the
# grounding validator sees one number.  The collapsed number must still be
# reproducible from PostgreSQL, so this cannot admit a fabricated value.
_SPACE_GROUPED = re.compile(r"\b\d{1,3}(?: \d{3})+\b")


def _collapse_space_grouped(text: str) -> str:
    return _SPACE_GROUPED.sub(lambda match: match.group(0).replace(" ", ""), text)

# Sentence-initial and domain words are not identity claims.  A generated
# answer may start with any of them; a real company, product or person name is
# not in this list and must therefore appear in the deterministic answer.
_GENERIC_WORDS = frozenset(
    {
        "всего", "итого", "ответ", "по", "за", "в", "во", "на", "с", "со",
        "и", "а", "но", "это", "этот", "эта", "эти", "данные", "период",
        "год", "году", "годы", "годам", "месяц", "месяцам", "сделки",
        "сделок", "сделка", "показатель", "значение", "сумма", "суммы",
        "количество", "объём", "объем", "средний", "средняя", "среднее",
        "максимальный", "максимальная", "минимальный", "минимальная",
        "топ", "рейтинг", "компания", "компании", "участник", "участники",
        "продавец", "продавцы", "покупатель", "покупатели", "брокер",
        "брокеры", "валюта", "валюте", "доллар", "долларах", "доллары",
        "сум", "сумах", "экспорт", "экспортные", "экспортный", "внутренние",
        "внутренний", "динамика", "сравнение", "между", "самая", "самый",
        "самые", "крупная", "крупнейшая", "только", "также", "например",
        "если", "то", "да", "нет", "наш", "ваш", "данным", "базы", "база",
        "postgresql", "uzex", "agent", "salom", "jami", "bitim", "bitimlar",
        "summa", "davr", "yil", "yilda", "oy", "oylar", "kompaniya",
        "kompaniyalar", "sotuvchi", "sotuvchilar", "xaridor", "xaridorlar",
        "broker", "brokerlar", "valyuta", "dollar", "dollarda", "so'm",
        "eksport", "ichki", "dinamika", "taqqoslash", "eng", "katta",
        "kichik", "nechta", "qancha", "mavjud", "yo'q", "total", "deals",
        "deal", "period", "year", "month", "company", "companies", "seller",
        "buyer", "currency", "usd", "uzs", "export", "domestic", "trend",
        "comparison", "maximum", "minimum", "average", "answer", "data",
        "records", "record", "statistics", "table", "rows", "row",
        # Common sentence starts and words in an explicitly labelled
        # hypothesis block.  They are not identity claims.
        "возможно", "вероятно", "предположительно", "не", "исключено",
        "одной", "одним", "одна", "среди", "причиной", "причина", "причин",
        "фактором", "фактор", "факторы", "снижение", "снижения", "рост",
        "роста", "уменьшение", "увеличение", "изменение", "изменения",
        "спрос", "спроса", "предложение", "предложения", "конкуренция",
        "конкуренции", "сезонность", "сезонности", "конъюнктура", "рынок",
        "рынка", "цена", "цены", "цен", "экспортёры", "импортёры",
        "ограничения", "логистика", "логистики", "поставки", "поставок",
        "ehtimol", "balki", "taxmin", "sabab", "sabablari", "omil",
        "omillardan", "talab", "taklif", "bozor", "narx", "pasayish",
        "o'sish", "o‘sish", "kamayish", "o'zgarish", "o‘zgarish",
    }
)


def _allowed_identity_tokens(deterministic_answer: str, result: HandlerResult) -> set[str]:
    allowed = {token.casefold() for token in _TOKEN.findall(deterministic_answer)}
    for row in result.rows:
        for text in (
            *row.labels.values(),
            *[value for value in row.group.values() if isinstance(value, str)],
        ):
            allowed.update(token.casefold() for token in _TOKEN.findall(str(text)))
    return allowed


def _is_sentence_initial(text: str, start: int) -> bool:
    """True when a token is capitalized only because it starts a sentence.

    Russian and Uzbek capitalise the first word of every sentence, so a
    sentence-initial capital is grammar, not an identity claim.  Mid-sentence
    capitals and quoted names are still checked against PostgreSQL facts.
    """

    prefix = text[:start].rstrip()
    if not prefix:
        return True
    return prefix[-1] in ".!?\n"


def validate_generated_answer(
    text: str,
    *,
    bundle: VerifiedFactBundle,
    execution: ExecutionPlan,
    result: HandlerResult,
    max_chars: int = 900,
) -> str:
    """Validate a generated FOUND answer or raise ``UnverifiedQuery``.

    Two independent checks must pass:

    1. every numeric token is reproducible from PostgreSQL-confirmed values;
    2. every proper noun/quoted identity is present in the deterministic
       answer, so a model cannot add a company, product or person.
    """

    if not isinstance(text, str):
        raise UnverifiedQuery("generated answer must be text")
    cleaned = text.strip()
    if not cleaned:
        raise UnverifiedQuery("generated answer is empty")
    if len(cleaned) > max_chars:
        raise UnverifiedQuery("generated answer is too long")

    validate_answer_grounding(_collapse_space_grouped(cleaned), execution, result)

    allowed = _allowed_identity_tokens(bundle.deterministic_answer, result)
    for match in _PROPER_NOUN.finditer(cleaned):
        if _is_sentence_initial(cleaned, match.start()):
            continue
        token = match.group(0).casefold()
        if token in _GENERIC_WORDS:
            continue
        if token not in allowed:
            raise UnverifiedQuery(
                "generated answer contains an identity that PostgreSQL did not confirm"
            )
    for match in _QUOTED.finditer(cleaned):
        for token in _TOKEN.findall(match.group(1)):
            folded = token.casefold()
            if folded in _GENERIC_WORDS:
                continue
            if folded not in allowed:
                raise UnverifiedQuery(
                    "generated answer contains a quoted identity that PostgreSQL did not confirm"
                )
    return cleaned


class GroundedAnswerLayer:
    """Generate natural wording over a verified bundle, with a safe fallback."""

    def __init__(
        self,
        generator: AnswerGenerator | None = None,
        *,
        max_chars: int = 900,
    ) -> None:
        self._generator = generator or TemplateAnswerGenerator()
        self._max_chars = max_chars

    @property
    def name(self) -> str:
        return self._generator.name

    def render(
        self,
        bundle: VerifiedFactBundle,
        execution: ExecutionPlan,
        result: HandlerResult,
    ) -> tuple[str, str]:
        """Return ``(answer, validation_status)``.

        ``validation_status`` is ``generated_validated`` when the model text
        passed both checks, otherwise ``fallback_template`` with the
        deterministic answer.
        """

        if self._generator.name == "deterministic":
            return bundle.deterministic_answer, "template"
        try:
            candidate = self._generator.generate(bundle)
        except Exception:  # noqa: BLE001 - a generator must never break an answer
            return bundle.deterministic_answer, "fallback_template"
        if candidate is None:
            return bundle.deterministic_answer, "fallback_template"
        try:
            validated = validate_generated_answer(
                candidate,
                bundle=bundle,
                execution=execution,
                result=result,
                max_chars=self._max_chars,
            )
        except UnverifiedQuery:
            return bundle.deterministic_answer, "fallback_template"
        return validated, "generated_validated"

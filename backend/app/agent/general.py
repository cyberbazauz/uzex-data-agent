from __future__ import annotations

import json
import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from app.agent.answer_generator import (
    VerifiedFactBundle,
    validate_generated_answer,
)
from app.agent.conversation import ConversationTurn
from app.agent.query_plan import ExecutionPlan
from app.domain.enums import Decision
from app.domain.errors import UnverifiedQuery
from app.domain.normalization import normalize_text
from app.handlers.base import HandlerResult
from app.language import LanguageDetection
from app.responses.models import AnswerData

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
GENERAL_PROMPT_PATH = PROMPT_DIR / "general_assistant_v1.txt"
EXPLANATION_PROMPT_PATH = PROMPT_DIR / "explanation_v1.txt"

MAX_HISTORY_TURNS = 3


class TextResponder(Protocol):
    """Free-text provider used only outside the strict QueryPlan path."""

    name: str

    def respond(self, system: str, user: str) -> str:
        ...


@lru_cache(maxsize=1)
def general_system_prompt() -> str:
    return GENERAL_PROMPT_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def explanation_system_prompt() -> str:
    return EXPLANATION_PROMPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# safety checks
# ---------------------------------------------------------------------------

# A conversational answer must never pretend to be a verified registry answer.
_DB_CLAIM = re.compile(
    r"(?:"
    r"по\s+данным\s+(?:нашей\s+)?(?:базы|базе|postgresql|реестра)"
    r"|согласно\s+(?:данным|реестру|базе)"
    r"|я\s+проверил(?:а)?\s+(?:базу|данные|реестр)"
    r"|запрос\s+к\s+базе\s+показал"
    r"|в\s+реестре\s+(?:есть|найдено|зарегистрировано)"
    r"|в\s+базе\s+данных\s+(?:есть|находится|зарегистрировано)"
    r"|ma['’`ʻ‘]?lumotlar\s+bazasi(?:ga|dan)?\s+"
    r"(?:ko['’`ʻ‘]?ra|asosan|muvofiq)"
    r"|bazani\s+tekshirdim"
    r"|bazadan\s+tekshir"
    r"|reyestr(?:da|dan)\s+(?:bor|mavjud|topildi)"
    r")",
    re.IGNORECASE,
)

# Secrets, raw SQL and connection strings must never be emitted to a user.
_FORBIDDEN_OUTPUT = re.compile(
    r"(?:"
    r"postgresql(?:\+\w+)?://"
    r"|DATABASE_URL\s*="
    r"|API[_\s-]?KEY\s*="
    r"|\bsk-[A-Za-z0-9]{12,}"
    r"|-----BEGIN\s+[A-Z ]*PRIVATE KEY-----"
    r"|\bSELECT\s+.+\s+FROM\b"
    r"|Ты\s+[—-]\s+UZEX AI Assistant"
    r"|Жёсткие правила"
    r"|general_assistant_v1"
    r")",
    re.IGNORECASE | re.DOTALL,
)

# A hypothesis block must read as a hypothesis, not as a proven cause.
_HEDGE = re.compile(
    r"(?:"
    r"возможн|вероятн|предполож|может\s+быть|могут\s+быть|не\s+исключено"
    r"|ehtimol|bo['’`ʻ‘]?lishi\s+mumkin|mumkin|taxmin"
    r")",
    re.IGNORECASE,
)

_EXPLANATION_LABEL = {
    "ru": "Возможные объяснения (гипотезы, не подтверждённые данными):",
    "uz_latn": "Ehtimoliy izohlar (ma'lumotlar bilan tasdiqlanmagan farazlar):",
    "uz_cyrl": "Эҳтимолий изоҳлар (маълумотлар билан тасдиқланмаган фаразлар):",
}


def explanation_label(locale: str) -> str:
    return _EXPLANATION_LABEL.get(locale, _EXPLANATION_LABEL["ru"])


# ---------------------------------------------------------------------------
# deterministic routing guard
# ---------------------------------------------------------------------------

_GENERAL_CUES = (
    "что такое",
    "кто такой",
    "кто такая",
    "как рассчитать",
    "как посчитать",
    "как считается",
    "как работает",
    "объясни",
    "объясните",
    "пример",
    "nima degani",
    "qanday hisoblanadi",
    "qanday ishlaydi",
    "tushuntir",
    "misol",
)

_METRIC_CUES = (
    "сколько",
    "количеств",
    "сумм",
    "стоимост",
    "объем",
    "объём",
    "hajm",
    "nechta",
    "qancha",
    "summa",
    "топ",
    "top",
    "рейтинг",
    "динамик",
    "тренд",
    "trend",
    "сравн",
    "solishtir",
    "taqqos",
    "средн",
    "o'rtacha",
    "o‘rtacha",
    "минимум",
    "максимум",
    "minimal",
    "maksimal",
    "доля",
    "ulush",
)

# Real registry entities.  A question that combines one of these with a
# metric, period, lookup or causal marker is a data question.
_REGISTRY_ENTITY_CUES = (
    "сделк",
    "сделок",
    "сделоч",
    "bitim",
    "битим",
    "контракт",
    "shartnoma",
    "компани",
    "kompaniya",
    "участник",
    "ishtirokchi",
    "покупател",
    "xaridor",
    "продав",
    "sotuvchi",
    "брокер",
    "broker",
    "регион",
    "hudud",
    "вилоят",
    "инн",
)

# Domain terms are genuine UZEX topics, but on their own they do not prove a
# registry request: "Что такое экспорт?" and "Почему экспорт важен?" are
# general knowledge, while "Сколько товара поставлено?" and "Покажи экспорт"
# are data questions.
_DOMAIN_TERM_CUES = (
    "экспорт",
    "eksport",
    "импорт",
    "import",
    "товар",
    "mahsulot",
)

_LOOKUP_CUES = (
    "покажи",
    "найди",
    "выведи",
    "список",
    "ko'rsat",
    "ko‘rsat",
    "topshir",
    "ro'yxat",
    "ro‘yxat",
)

_CAUSAL_CUES = (
    "почему",
    "отчего",
    "причин",
    "nima uchun",
    "nima sabab",
    "nega",
    "сабаб",
)

# ---------------------------------------------------------------------------
# deterministic GENERAL pre-router
# ---------------------------------------------------------------------------
#
# Obvious conversational questions do not need the expensive strict-schema
# planner call.  Skipping it cuts a General turn from roughly 4.8k tokens to
# roughly 0.9k tokens, which keeps the free-tier TPM budget from collapsing
# under a short burst of ordinary questions.  The pre-router never produces a
# data plan and never opens a database connection; anything ambiguous still
# goes through the full planner.

_GENERAL_IDENTITY_CUES = (
    "ты кто",
    "кто ты",
    "кто вы",
    "ты где",
    "где ты",
    "вы где",
    "где вы",
    "ты тут",
    "ты здесь",
    "вы тут",
    "что ты такое",
    "что ты умеешь",
    "что ты можешь",
    "что вы умеете",
    "что вы можете",
    "какие данные у тебя",
    "какие данные ты",
    "какие данные вы",
    "чем ты можешь помочь",
    "чем вы можете помочь",
    "расскажи о себе",
    "расскажите о себе",
    "представься",
    "who are you",
    "where are you",
)

_GENERAL_GREETING_WORDS = frozenset(
    {"привет", "здравствуйте", "здравствуй", "salom", "hello", "hi"}
)

_GENERAL_GREETING_PHRASES = (
    "добрый день",
    "добрый вечер",
    "доброе утро",
    "assalomu alaykum",
)

_GENERAL_DEFINITION_CUES = (
    "что такое",
    "кто такой",
    "кто такая",
    "что значит",
    "что означает",
    "дай определение",
    "определение",
    "объясни",
    "объясните",
    "как работает",
    "как устроен",
    "как устроена",
    "nima degani",
    "qanday ishlaydi",
    "qanday hisoblanadi",
    "tushuntir",
)

_IDENTIFIER = re.compile(r"\d{4,}")
_YEAR = re.compile(r"(?:19|20)\d{2}")


def _has_prefix(text: str, cues: Sequence[str]) -> bool:
    tokens = text.split()
    return any(token.startswith(cue) for token in tokens for cue in cues)


def _has_phrase(text: str, cues: Sequence[str]) -> bool:
    """Match a cue as a token or a token sequence, never as a raw substring.

    Substring matching made ``пример`` fire on the unrelated word ``например``.
    Multi-word cues are compared token by token; the first token may be a stem.
    """

    tokens = text.split()
    for cue in cues:
        parts = cue.split()
        if not parts:
            continue
        if len(parts) == 1:
            if any(token.startswith(parts[0]) for token in tokens):
                return True
            continue
        limit = len(tokens) - len(parts) + 1
        for index in range(max(0, limit)):
            if not tokens[index].startswith(parts[0]):
                continue
            if tokens[index + 1 : index + len(parts)] == parts[1:]:
                return True
    return False


def _has_token(text: str, cues: Sequence[str]) -> bool:
    tokens = text.split()
    return any(token == cue for token in tokens for cue in cues)


def looks_like_registry_question(question: str) -> bool:
    """True when a question clearly asks for registry facts.

    This is a safety net, not the classifier.  The LLM decides the mode; the
    guard only prevents a ``GENERAL`` plan from silently answering a question
    that names a real registry entity (or a concrete UZEX domain term) together
    with a statistic, period, lookup or identifier.  Isolated generic words are
    not enough.
    """

    normalized = normalize_text(question)
    if not normalized:
        return False

    registry_entity = _has_prefix(normalized, _REGISTRY_ENTITY_CUES)
    domain_term = _has_prefix(normalized, _DOMAIN_TERM_CUES)
    if not (registry_entity or domain_term):
        return False

    metric = _has_prefix(normalized, _METRIC_CUES)
    year = bool(_YEAR.search(normalized))
    lookup = _has_prefix(normalized, _LOOKUP_CUES)
    causal = _has_phrase(normalized, _CAUSAL_CUES)
    identifier = bool(_IDENTIFIER.search(normalized))

    # A metric, period, lookup, explicit identifier, or (for a real registry
    # entity) a causal marker is a concrete data signal.  A bare domain term
    # plus "почему" is conceptual, not a registry request.
    signal = metric or year or lookup or identifier or (registry_entity and causal)
    if not signal:
        return False

    # A definition is exempt only when it does not also carry a period, lookup,
    # identifier or causal marker.  "Что такое риск?" stays general;
    # "Что такое сделка 280091?" and any metric question stay DATA.
    if _has_phrase(normalized, _GENERAL_CUES) and not (
        lookup or year or causal or identifier
    ):
        return False
    return True


def looks_like_general_question(question: str) -> bool:
    """True only for unambiguous conversational questions.

    The pre-router is deliberately conservative: any registry entity, metric,
    lookup, period, identifier or causal marker sends the question through the
    full planner instead.  It creates a valid ``QueryPlan.general`` and never
    touches PostgreSQL.
    """

    normalized = normalize_text(question)
    if not normalized:
        return False

    if looks_like_registry_question(question):
        return False

    # Never bypass the planner for a question that could ask for data.
    if _has_prefix(normalized, _METRIC_CUES):
        return False
    if _has_prefix(normalized, _LOOKUP_CUES):
        return False
    if _has_phrase(normalized, _CAUSAL_CUES):
        return False
    if _IDENTIFIER.search(normalized) or _YEAR.search(normalized):
        return False

    if _has_phrase(normalized, _GENERAL_IDENTITY_CUES):
        return True
    if _has_token(normalized, _GENERAL_GREETING_WORDS):
        return True
    if _has_phrase(normalized, _GENERAL_GREETING_PHRASES):
        return True
    if _has_phrase(normalized, _GENERAL_DEFINITION_CUES):
        return True
    return False


# ---------------------------------------------------------------------------
# general answer
# ---------------------------------------------------------------------------


def validate_general_answer(text: str, *, max_chars: int) -> str:
    if not isinstance(text, str):
        raise UnverifiedQuery("general answer must be text")
    cleaned = text.strip()
    if not cleaned:
        raise UnverifiedQuery("general answer is empty")
    if len(cleaned) > max_chars:
        raise UnverifiedQuery("general answer is too long")
    if _FORBIDDEN_OUTPUT.search(cleaned):
        raise UnverifiedQuery("general answer contains a forbidden artefact")
    if _DB_CLAIM.search(cleaned):
        raise UnverifiedQuery(
            "general answer claims a database verification it cannot provide"
        )
    return cleaned


_DATA_HISTORY_PLACEHOLDER = (
    "[ответ по данным реестра UZEX; числовые детали в этот режим не передаются. "
    "Если пользователь спросит о них, предложи повторить запрос к реестру.]"
)


def _general_history(
    history: Sequence[ConversationTurn],
) -> list[dict[str, str]]:
    """Return chat history safe for the free-text general model.

    A verified data answer contains exact registry numbers.  Passing them into
    General AI Mode makes the model either restate them without verification
    or answer "I cannot access the registry", which is wrong for the product
    as a whole.  The turn itself is kept for conversational continuity, but
    the numeric answer is replaced with a neutral marker.
    """

    recent: list[dict[str, str]] = []
    for turn in history[-MAX_HISTORY_TURNS:]:
        if not turn.answer:
            continue
        answer = turn.answer
        if turn.plan.decision is Decision.EXECUTE:
            answer = _DATA_HISTORY_PLACEHOLDER
        recent.append({"question": turn.question, "answer": answer})
    return recent


class GeneralAnswerer:
    """Answer a non-registry question through a qualified free-text provider."""

    def __init__(
        self,
        responder: TextResponder,
        *,
        max_chars: int = 2000,
    ) -> None:
        self._responder = responder
        self._max_chars = max_chars

    @property
    def name(self) -> str:
        return getattr(self._responder, "name", "llm")

    def answer(
        self,
        question: str,
        detection: LanguageDetection,
        history: Sequence[ConversationTurn] = (),
    ) -> str:
        recent = _general_history(history)
        payload: dict[str, Any] = {
            "question": question,
            "detected_language": detection.language.value,
            "detected_script": detection.script.value,
            "detected_locale": detection.locale,
            "recent_conversation": recent,
        }
        candidate = self._responder.respond(
            general_system_prompt(),
            json.dumps(payload, ensure_ascii=False),
        )
        return validate_general_answer(candidate, max_chars=self._max_chars)


# ---------------------------------------------------------------------------
# explanatory layer over verified facts
# ---------------------------------------------------------------------------


class ExplanationLayer:
    """Add a clearly labelled hypothesis block to a verified data answer.

    The verified deterministic answer always stays first.  The generated text
    is validated with the same number/identity grounding rules as a normal
    answer and must be visibly hedged; otherwise the caller keeps the
    deterministic answer unchanged.
    """

    def __init__(
        self,
        responder: TextResponder,
        *,
        max_chars: int = 900,
    ) -> None:
        self._responder = responder
        self._max_chars = max_chars

    @property
    def name(self) -> str:
        return getattr(self._responder, "name", "llm")

    def explain(
        self,
        *,
        question: str,
        detection: LanguageDetection,
        deterministic_answer: str,
        execution: ExecutionPlan,
        result: HandlerResult,
        data: AnswerData,
        evidence: dict[str, Any] | None = None,
    ) -> str:
        facts = data.model_dump(mode="json")
        payload: dict[str, Any] = {
            "question": question,
            "detected_language": detection.language.value,
            "detected_script": detection.script.value,
            "detected_locale": detection.locale,
            "verified_answer": deterministic_answer,
            "verified_facts": facts,
            "evidence": evidence or {},
        }
        candidate = self._responder.respond(
            explanation_system_prompt(),
            json.dumps(payload, ensure_ascii=False),
        )
        bundle = VerifiedFactBundle(
            status="FOUND",
            facts=facts,
            evidence=evidence or {},
            deterministic_answer=deterministic_answer,
        )
        validated = validate_generated_answer(
            candidate,
            bundle=bundle,
            execution=execution,
            result=result,
            max_chars=self._max_chars,
        )
        if not _HEDGE.search(validated):
            raise UnverifiedQuery("explanation is not visibly hedged")
        if _DB_CLAIM.search(validated):
            raise UnverifiedQuery("explanation claims an unverifiable source")
        return validated


def compose_explained_answer(
    deterministic_answer: str,
    explanation: str,
    *,
    locale: str,
) -> str:
    return (
        f"{deterministic_answer.strip()}\n\n"
        f"{explanation_label(locale)}\n{explanation.strip()}"
    )

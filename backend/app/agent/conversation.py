from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

from pydantic import ValidationError

from app.agent.query_plan import EntityReference, FilterCondition, QueryPlan
from app.domain.enums import (
    ClarificationCode,
    CurrencyMode,
    Decision,
    EntityRole,
    FilterField,
    GroupField,
    Intent,
    MatchKind,
    Metric,
    Operator,
    Subject,
)
from app.domain.normalization import normalize_text
from app.language import LanguageDetection

# ---------------------------------------------------------------------------
# conversation memory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Referent:
    """A company mentioned by the previous answer, never invented by a model."""

    name: str
    role: EntityRole | None = None


@dataclass(frozen=True)
class ConversationTurn:
    question: str
    plan: QueryPlan
    status: str
    referents: tuple[Referent, ...] = ()
    # Stored only for General AI Mode context.  It is never used as a query
    # fact and never re-validated as a PostgreSQL answer.
    answer: str | None = None


class ConversationMemory:
    """Session-scoped history of validated QueryPlans.

    State is keyed only by the caller-supplied ``session_id``.  There is no
    global fallback and no implicit sharing between sessions, so a group chat
    cannot leak one participant's filters into another participant's answer.
    """

    def get_last(self, session_id: str) -> ConversationTurn | None:
        raise NotImplementedError

    def append(self, session_id: str, turn: ConversationTurn) -> None:
        raise NotImplementedError

    def recent(
        self, session_id: str, limit: int = 3
    ) -> tuple[ConversationTurn, ...]:
        """Return the most recent turns, oldest first.

        The default keeps the interface backward compatible with custom
        memories that only implement get_last/append/reset.
        """

        last = self.get_last(session_id)
        return (last,) if last is not None else ()

    def reset(self, session_id: str) -> None:
        raise NotImplementedError


class InMemoryConversationMemory(ConversationMemory):
    def __init__(self, *, max_turns: int = 6, max_sessions: int = 2000) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        if max_sessions < 1:
            raise ValueError("max_sessions must be positive")
        self._max_turns = max_turns
        self._max_sessions = max_sessions
        self._sessions: OrderedDict[str, list[ConversationTurn]] = OrderedDict()
        self._lock = Lock()

    def get_last(self, session_id: str) -> ConversationTurn | None:
        if not session_id:
            return None
        with self._lock:
            turns = self._sessions.get(session_id)
            if not turns:
                return None
            self._sessions.move_to_end(session_id)
            return turns[-1]

    def append(self, session_id: str, turn: ConversationTurn) -> None:
        if not session_id:
            return
        with self._lock:
            turns = self._sessions.setdefault(session_id, [])
            turns.append(turn)
            if len(turns) > self._max_turns:
                del turns[: len(turns) - self._max_turns]
            self._sessions.move_to_end(session_id)
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)

    def recent(
        self, session_id: str, limit: int = 3
    ) -> tuple[ConversationTurn, ...]:
        if not session_id or limit < 1:
            return ()
        with self._lock:
            turns = self._sessions.get(session_id)
            if not turns:
                return ()
            self._sessions.move_to_end(session_id)
            return tuple(turns[-limit:])

    def reset(self, session_id: str) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)


# ---------------------------------------------------------------------------
# follow-up detection
# ---------------------------------------------------------------------------

_MONTH_GROUP = (
    "по месяцам",
    "по каждому месяцу",
    "помесячно",
    "oylar bo'yicha",
    "har bir oy",
    "ойлар бўйича",
    "ҳар бир ой",
)
_YEAR_GROUP = (
    "по годам",
    "по каждому году",
    "ежегодно",
    "yillar bo'yicha",
    "har bir yil",
    "йиллар бўйича",
    "ҳар бир йил",
)
_EXPORT_MARKERS = ("экспорт", "eksport", "экспорт")
_INTERNAL_MARKERS = ("внутрен", "ichki", "ички")
_SELLER_SWITCH = ("продав", "sotuvchi", "сотувчи")
_BUYER_SWITCH = ("покупател", "харидор", "xaridor")
_MIN_SWITCH = ("маленьк", "минимальн", "минимум", "kichik", "кичик")
_MAX_SWITCH = ("крупн", "больш", "максимальн", "максимум", "katta", "катта")
# A min/max switch is an ellipsis about the previous deal metric.  A question
# that names a company or a participant is a full new question, not a switch.
_ENTITY_SUBJECTS = (
    "компани",
    "участник",
    "покупател",
    "продав",
    "брокер",
    "харидор",
    "sotuvchi",
    "xaridor",
    "kompaniya",
    "ishtirokchi",
    "иштирокчи",
)
_QUARTER_GROUP = (
    "по квартал",
    "поквартальн",
    "chorak",
    "чорак",
)
_CURRENCY_USD = ("доллар", "dollar", "usd", "dollarda", "долларда")
_CURRENCY_UZS = ("сум", "so'm", "so‘m", "сўм", "uzs")
_YEAR_ONLY = re.compile(
    r"^(?:а|и|and|then|now|va|ва)?\s*(?:(?:за|в|во|in|uchun|учун)\s+)?"
    r"(\d{4})(?:\s*(?:chi|чи|yilgi|йилги))?(?:\s*(?:год|йил|yil)\w*)?$"
)
_PREVIOUS_YEAR = re.compile(
    r"(?:предыдущ\w*|прошл\w*|ўтган\w*|утган\w*|o'tgan\w*|otgan\w*|"
    r"avvalgi\w*|oldingi\w*)\s*(?:год|йил|yil)\w*"
)
_PRONOUN_RE = re.compile(
    r"(?<!\w)(?:у\s+)?(?:не[её]|него|них|uning|shuning|унинг|шунинг)(?!\w)",
    re.IGNORECASE,
)
_COUNT_CUE = ("сколько", "количество", "nechta", "qancha", "нечта", "қанча")
_DEAL_CUE = ("сдел", "bitim", "битим")
_EXPLICIT_INTENT = (
    "сколько",
    "количеств",
    "сумм",
    "стоимост",
    "объём",
    "объем",
    "hajm",
    "nechta",
    "qancha",
    "динамик",
    "тренд",
    "trend",
    "dinamika",
    "сравн",
    "taqqosla",
    "топ",
    "top",
    "покажи",
    "ko'rsat",
)


@dataclass(frozen=True)
class FollowUp:
    kind: str
    year: int | None = None
    currency: str | None = None


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _year_is_plausible(text: str, year: int) -> bool:
    """Accept any four-digit year, but only when it is clearly a year.

    The normal 20xx range is unambiguous.  A year outside it (1900, 2100)
    still names a period, but only if the utterance carries an explicit year
    cue, so a bare deal number is never mistaken for a year.
    """

    if 2000 <= year <= 2099:
        return True
    return _has_any(
        text,
        ("за", "в ", "во ", "год", "йил", "yil", "chi", "чи", "yilgi", "йилги"),
    )


def detect_follow_up(question: str) -> FollowUp | None:
    """Classify a short elliptical follow-up without inventing a new question.

    Only unambiguous deltas are recognised (period, grouping, export scope,
    currency, pronoun referent).  Anything longer or more complex is passed to
    the planner, which may ask for clarification.
    """

    text = normalize_text(question)
    if not text:
        return None
    words = text.split()
    if len(words) > 10:
        return None
    explicit = _has_any(text, _EXPLICIT_INTENT)

    if _PREVIOUS_YEAR.search(text) and not explicit:
        return FollowUp(kind="previous_year")

    year_match = _YEAR_ONLY.match(text)
    if year_match is not None:
        year = int(year_match.group(1))
        if _year_is_plausible(text, year):
            return FollowUp(kind="year", year=year)

    if (
        _has_any(text, _QUARTER_GROUP)
        and len(words) <= 5
        and not explicit
    ):
        # The schema has no quarter grouping, so a yearly trend would be a
        # misleading answer to "а по кварталам?".
        return FollowUp(kind="group_quarter")

    if (
        _has_any(text, _MONTH_GROUP)
        and len(words) <= 5
        and not explicit
    ):
        return FollowUp(kind="group_month")
    if (
        _has_any(text, _YEAR_GROUP)
        and len(words) <= 5
        and not explicit
    ):
        return FollowUp(kind="group_year")

    only_marker = _has_any(text, ("только", "теперь", "now", "faqat", "фақат"))
    if (
        _has_any(text, _EXPORT_MARKERS)
        and len(words) <= 6
        and (only_marker or len(words) <= 3)
        and not explicit
    ):
        return FollowUp(kind="export_only")
    if (
        _has_any(text, _INTERNAL_MARKERS)
        and len(words) <= 6
        and (only_marker or len(words) <= 3)
        and not explicit
    ):
        return FollowUp(kind="internal_only")

    currency_lead = re.match(r"^(?:а|и|and|then|now|теперь)?\s*(?:в|во|in)?\s*", text)
    starts_like_follow_up = bool(currency_lead and text.split()[:1] and text.split()[0] in {"а", "и", "and", "then", "now", "теперь", "в"})
    if (
        len(words) <= 6
        and not explicit
        and _has_any(text, _CURRENCY_USD)
        and (starts_like_follow_up or len(words) <= 3)
    ):
        return FollowUp(kind="currency", currency="USD")
    if (
        len(words) <= 6
        and not explicit
        and _has_any(text, _CURRENCY_UZS)
        and (starts_like_follow_up or len(words) <= 3)
    ):
        return FollowUp(kind="currency", currency="UZS")

    if (
        _has_any(text, _BUYER_SWITCH)
        and len(words) <= 4
        and not explicit
    ):
        return FollowUp(kind="buyers_only")
    if (
        _has_any(text, _SELLER_SWITCH)
        and len(words) <= 4
        and not explicit
    ):
        return FollowUp(kind="sellers_only")
    if (
        _has_any(text, _MIN_SWITCH)
        and len(words) <= 5
        and not explicit
        and not _has_any(text, _ENTITY_SUBJECTS)
    ):
        return FollowUp(kind="min")
    if (
        _has_any(text, _MAX_SWITCH)
        and len(words) <= 5
        and not explicit
        and not _has_any(text, _ENTITY_SUBJECTS)
    ):
        return FollowUp(kind="max")

    if _PRONOUN_RE.search(text) and _has_any(text, _COUNT_CUE) and _has_any(
        text, _DEAL_CUE
    ):
        return FollowUp(kind="pronoun_count")

    if (
        len(words) <= 8
        and not explicit
        and re.match(r"^(?:а|и|and|then)?\s*у\s+", text)
        and _has_any(text, ("инн", "inn"))
    ):
        # "А у покупателя с ИНН ...?" keeps the previous metric and period but
        # replaces the entity.  It is parsed by the planner, not guessed here.
        return FollowUp(kind="entity_switch")

    return None


# ---------------------------------------------------------------------------
# plan merging
# ---------------------------------------------------------------------------

_GROUPABLE_INTENTS = frozenset(
    {
        Intent.COUNT,
        Intent.TREND,
        Intent.SUM_AMOUNT,
        Intent.SUM_VOLUME,
        Intent.AVERAGE,
        Intent.MIN_MAX,
        Intent.TOP_N,
    }
)
_FOLLOWABLE_INTENTS = _GROUPABLE_INTENTS | {Intent.FILTER_LIST}
_CURRENCY_VALUES = {"USD": "Доллар США", "UZS": "Узбекский сум"}


def _dump_filters(filters: list[FilterCondition]) -> list[dict[str, object]]:
    return [condition.model_dump(mode="python") for condition in filters]


def _without_fields(
    filters: list[FilterCondition], *fields: FilterField
) -> list[FilterCondition]:
    return [condition for condition in filters if condition.field not in fields]


def _year_filter(year: int) -> FilterCondition:
    return FilterCondition(
        field=FilterField.DEAL_DATE,
        operator=Operator.BETWEEN,
        value=[f"{year}-01-01", f"{year}-12-31"],
    )


def _latest_year(filters: list[FilterCondition]) -> int | None:
    years: list[int] = []
    for condition in filters:
        if condition.field is not FilterField.DEAL_DATE:
            continue
        values = condition.value if isinstance(condition.value, list) else [condition.value]
        for value in values:
            text = str(value)
            if len(text) >= 4 and text[:4].isdigit():
                years.append(int(text[:4]))
    return max(years) if years else None


def _role_from_plan(plan: QueryPlan) -> EntityRole | None:
    for entity in plan.entity_candidates:
        if entity.kind is MatchKind.PARTICIPANT and entity.role in (
            EntityRole.SELLER,
            EntityRole.BUYER,
        ):
            return entity.role
    if GroupField.SELLER_INN in plan.group_by:
        return EntityRole.SELLER
    if GroupField.BUYER_INN in plan.group_by:
        return EntityRole.BUYER
    return None


def _build(
    base: QueryPlan,
    detection: LanguageDetection,
    **updates: object,
) -> QueryPlan | None:
    data = base.model_dump(mode="python")
    data.update(updates)
    data["language"] = detection.language
    data["script"] = detection.script
    data["decision"] = Decision.EXECUTE
    data["clarification"] = None
    try:
        return QueryPlan.model_validate(data)
    except ValidationError:
        return None


def _unsupported(detection: LanguageDetection) -> QueryPlan:
    return QueryPlan(
        decision=Decision.UNSUPPORTED,
        language=detection.language,
        script=detection.script,
        intent=Intent.COUNT,
        subject=Subject.DEALS,
        metric=Metric.DEAL_COUNT,
    )


def _clarify(
    detection: LanguageDetection,
    code: ClarificationCode,
    options: list[str] | None = None,
) -> QueryPlan:
    from app.agent.query_plan import ClarificationInfo

    return QueryPlan(
        decision=Decision.CLARIFY,
        language=detection.language,
        script=detection.script,
        intent=Intent.COUNT,
        subject=Subject.DEALS,
        metric=Metric.DEAL_COUNT,
        clarification=ClarificationInfo(code=code, options=options or []),
    )


def resolve_follow_up(
    question: str,
    detection: LanguageDetection,
    previous: ConversationTurn | None,
) -> QueryPlan | None:
    """Return a merged plan for an elliptical follow-up, or ``None``.

    ``None`` means the follow-up was not recognised or cannot be merged
    safely; the caller keeps the planner's own result (often UNSUPPORTED or a
    clarification) instead of guessing.
    """

    if previous is None or previous.plan.decision is not Decision.EXECUTE:
        return None
    follow_up = detect_follow_up(question)
    if follow_up is None:
        return None

    base = previous.plan
    if follow_up.kind == "pronoun_count":
        # The previous turn may be a lookup or a ranking, neither of which is
        # itself followable, but its validated referents are still usable.
        return _pronoun_count(previous, detection)
    if follow_up.kind == "entity_switch":
        return _entity_switch(base, question, detection)
    if follow_up.kind == "group_quarter":
        return _unsupported(detection)
    if base.intent not in _FOLLOWABLE_INTENTS:
        return None

    if follow_up.kind == "group_month":
        if base.intent not in _GROUPABLE_INTENTS or base.subject is not Subject.DEALS:
            return None
        return _build(
            base,
            detection,
            intent=Intent.TREND,
            subject=Subject.DEALS,
            group_by=[GroupField.MONTH],
        )

    if follow_up.kind == "group_year":
        if base.intent not in _GROUPABLE_INTENTS or base.subject is not Subject.DEALS:
            return None
        return _build(
            base,
            detection,
            intent=Intent.TREND,
            subject=Subject.DEALS,
            group_by=[GroupField.YEAR],
        )

    if follow_up.kind == "export_only":
        filters = _without_fields(base.filters, FilterField.CONTRACT_TYPE)
        filters.append(
            FilterCondition(
                field=FilterField.CONTRACT_TYPE,
                operator=Operator.EQ,
                value="Экспортный",
            )
        )
        return _build(base, detection, filters=_dump_filters(filters))

    if follow_up.kind == "internal_only":
        filters = _without_fields(base.filters, FilterField.CONTRACT_TYPE)
        filters.append(
            FilterCondition(
                field=FilterField.CONTRACT_TYPE,
                operator=Operator.EQ,
                value="Внутренний",
            )
        )
        return _build(base, detection, filters=_dump_filters(filters))

    if follow_up.kind in {"buyers_only", "sellers_only"}:
        if base.intent is not Intent.TOP_N:
            return None
        group = (
            GroupField.BUYER_INN
            if follow_up.kind == "buyers_only"
            else GroupField.SELLER_INN
        )
        return _build(
            base,
            detection,
            subject=Subject.PARTICIPANTS,
            intent=Intent.TOP_N,
            group_by=[group],
            entity_candidates=[],
            comparison=None,
        )

    if follow_up.kind in {"min", "max"}:
        if base.intent is not Intent.MIN_MAX:
            return None
        metric = Metric.AMOUNT_MIN if follow_up.kind == "min" else Metric.AMOUNT_MAX
        return _build(
            base,
            detection,
            metric=metric,
            comparison=None,
        )

    if follow_up.kind == "currency":
        return _merge_currency(base, detection, follow_up.currency)

    if follow_up.kind == "year" and follow_up.year is not None:
        filters = _without_fields(base.filters, FilterField.DEAL_DATE)
        filters.append(_year_filter(follow_up.year))
        group_by = list(base.group_by)
        if group_by == [GroupField.YEAR]:
            # A single year is one point, not a yearly trend.
            group_by = []
        return _build(
            base,
            detection,
            filters=_dump_filters(filters),
            group_by=group_by,
            comparison=None,
        )

    if follow_up.kind == "previous_year":
        year = _latest_year(base.filters)
        if year is None:
            return None
        filters = _without_fields(base.filters, FilterField.DEAL_DATE)
        filters.append(_year_filter(year - 1))
        group_by = list(base.group_by)
        if group_by == [GroupField.YEAR]:
            group_by = []
        return _build(
            base,
            detection,
            filters=_dump_filters(filters),
            group_by=group_by,
            comparison=None,
        )

    return None


def _merge_currency(
    base: QueryPlan, detection: LanguageDetection, currency: str | None
) -> QueryPlan | None:
    if currency not in _CURRENCY_VALUES:
        return None
    value = _CURRENCY_VALUES[currency]
    filters = _without_fields(base.filters, FilterField.CURRENCY)
    filters.append(
        FilterCondition(
            field=FilterField.CURRENCY, operator=Operator.EQ, value=value
        )
    )

    if base.intent is Intent.SUM_AMOUNT:
        if base.metric not in (
            Metric.AMOUNT_SUM_UZS_CONVERTED,
            Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
        ):
            return None
        mode = CurrencyMode.USD_NATIVE if currency == "USD" else CurrencyMode.UZS_NATIVE
        return _build(
            base,
            detection,
            metric=Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
            currency_mode=mode,
            filters=_dump_filters(filters),
        )

    if base.intent is Intent.MIN_MAX:
        mode = CurrencyMode.USD_NATIVE if currency == "USD" else CurrencyMode.UZS_NATIVE
        return _build(
            base,
            detection,
            currency_mode=mode,
            filters=_dump_filters(filters),
        )

    if base.intent is Intent.TOP_N and base.metric in (
        Metric.AMOUNT_SUM_UZS_CONVERTED,
        Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
    ):
        mode = CurrencyMode.USD_NATIVE if currency == "USD" else CurrencyMode.UZS_NATIVE
        return _build(
            base,
            detection,
            metric=Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
            currency_mode=mode,
            filters=_dump_filters(filters),
        )

    if base.intent in (Intent.COUNT, Intent.FILTER_LIST):
        # "А в долларах?" after a count means the count of USD contracts.
        return _build(base, detection, filters=_dump_filters(filters))

    # Averages are only defined in converted UZS in this data model, and a
    # physical volume has no currency.  Asking for another currency cannot be
    # satisfied without inventing a conversion, so the caller stays UNSUPPORTED.
    return None


def _entity_switch(
    base: QueryPlan,
    question: str,
    detection: LanguageDetection,
) -> QueryPlan | None:
    """Replace the previous entity with one parsed from an INN follow-up."""

    from app.agent.local_planner import plan_question

    stripped = re.sub(
        r"^(?:а|и|and|then|now|теперь|va|ва)\s+",
        "",
        question.strip(),
        flags=re.IGNORECASE,
    )
    probe = plan_question(f"Сколько сделок {stripped}", detection)
    if probe.decision is not Decision.EXECUTE:
        return None
    if probe.intent is not Intent.COUNT or not probe.entity_candidates:
        return None
    return _build(
        base,
        detection,
        intent=Intent.COUNT,
        subject=Subject.DEALS,
        metric=Metric.DEAL_COUNT,
        filters=_dump_filters(base.filters),
        group_by=[],
        order_by=[],
        limit=None,
        currency_mode=CurrencyMode.NOT_APPLICABLE,
        entity_candidates=[
            entity.model_dump(mode="python")
            for entity in probe.entity_candidates
        ],
        comparison=None,
    )


def _pronoun_count(
    previous: ConversationTurn, detection: LanguageDetection
) -> QueryPlan | None:
    referents = previous.referents
    if not referents:
        return None
    unique: list[Referent] = []
    seen: set[str] = set()
    for referent in referents:
        key = referent.name.strip().casefold()
        if key and key not in seen:
            seen.add(key)
            unique.append(referent)

    if len(unique) > 1:
        return _clarify(
            detection,
            ClarificationCode.PARTICIPANT_AMBIGUOUS,
            [referent.name for referent in unique[:5]],
        )

    referent = unique[0]
    role = referent.role or _role_from_plan(previous.plan) or EntityRole.ANY
    base = previous.plan
    return _build(
        base,
        detection,
        decision=Decision.EXECUTE,
        clarification=None,
        intent=Intent.COUNT,
        subject=Subject.DEALS,
        metric=Metric.DEAL_COUNT,
        filters=[],
        group_by=[],
        order_by=[],
        limit=None,
        currency_mode=CurrencyMode.NOT_APPLICABLE,
        entity_candidates=[
            EntityReference(
                kind=MatchKind.PARTICIPANT, mention=referent.name, role=role
            ).model_dump(mode="python")
        ],
        comparison=None,
    )

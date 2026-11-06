from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.agent.answer_generator import (
    CallableAnswerGenerator,
    GroundedAnswerLayer,
    TemplateAnswerGenerator,
    VerifiedFactBundle,
)
from app.agent.conversation import (
    ConversationTurn,
    InMemoryConversationMemory,
    Referent,
    detect_follow_up,
    resolve_follow_up,
)
from app.agent.local_planner import plan_question
from app.agent.orchestrator import Orchestrator
from app.agent.query_plan import (
    ExecutionPlan,
    FilterCondition,
    QueryPlan,
)
from app.domain.enums import (
    ClarificationCode,
    CurrencyMode,
    Decision,
    EntityRole,
    FilterField,
    GroupField,
    Intent,
    Language,
    Metric,
    Operator,
    ResponseStatus,
    Script,
    Subject,
)
from app.domain.errors import UnverifiedQuery
from app.handlers.base import HandlerResult, ResultRow
from app.language import detect_language

YEAR_2024 = FilterCondition(
    field=FilterField.DEAL_DATE,
    operator=Operator.BETWEEN,
    value=["2024-01-01", "2024-12-31"],
)


def _plan(**overrides: object) -> QueryPlan:
    values: dict[str, object] = {
        "decision": Decision.EXECUTE,
        "language": Language.RU,
        "script": Script.CYRILLIC,
        "intent": Intent.COUNT,
        "subject": Subject.DEALS,
        "metric": Metric.DEAL_COUNT,
    }
    values.update(overrides)
    return QueryPlan(**values)


def _turn(question: str, plan: QueryPlan, referents: tuple[Referent, ...] = ()) -> ConversationTurn:
    return ConversationTurn(
        question=question, plan=plan, status="FOUND", referents=referents
    )


def _detection(question: str):
    return detect_language(question)


# ---------------------------------------------------------------------------
# follow-up detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "kind"),
    [
        ("А за 2023?", "year"),
        ("2023", "year"),
        ("за 2023 год", "year"),
        ("А по месяцам?", "group_month"),
        ("помесячно", "group_month"),
        ("А по годам?", "group_year"),
        ("ежегодно", "group_year"),
        ("А теперь только экспортные", "export_only"),
        ("А в долларах?", "currency"),
        ("в сумах", "currency"),
        ("А если взять предыдущий год?", "previous_year"),
        ("Сколько у неё сделок?", "pronoun_count"),
        ("uning bitimlari qancha?", "pronoun_count"),
    ],
)
def test_detect_follow_up_recognises_elliptical_forms(question: str, kind: str) -> None:
    follow = detect_follow_up(question)
    assert follow is not None
    assert follow.kind == kind


@pytest.mark.parametrize(
    "question",
    [
        "Покажи динамику сделок по годам",
        "Сколько сделок было в 2023 году?",
        "Какая компания больше всех покупала?",
        "Покажи самую крупную сделку",
        "Сумма сделок за 2024 год",
    ],
)
def test_explicit_questions_are_not_treated_as_follow_ups(question: str) -> None:
    assert detect_follow_up(question) is None


# ---------------------------------------------------------------------------
# follow-up merging
# ---------------------------------------------------------------------------


def test_year_follow_up_replaces_period_and_keeps_metric() -> None:
    previous = _turn("Сколько сделок было в 2024?", _plan(filters=[YEAR_2024]))
    merged = resolve_follow_up("А за 2023?", _detection("А за 2023?"), previous)

    assert merged is not None
    assert merged.decision is Decision.EXECUTE
    assert merged.metric is Metric.DEAL_COUNT
    dates = [f for f in merged.filters if f.field is FilterField.DEAL_DATE]
    assert len(dates) == 1
    assert list(dates[0].value) == ["2023-01-01", "2023-12-31"]


def test_month_follow_up_converts_count_to_monthly_trend() -> None:
    previous = _turn("Сколько сделок было в 2024?", _plan(filters=[YEAR_2024]))
    merged = resolve_follow_up("А по месяцам?", _detection("А по месяцам?"), previous)

    assert merged is not None
    assert merged.intent is Intent.TREND
    assert merged.group_by == [GroupField.MONTH]
    # The previous period filter is preserved, so the trend is for 2024.
    assert any(f.field is FilterField.DEAL_DATE for f in merged.filters)


def test_previous_year_shift_uses_explicit_year_not_rolling_window() -> None:
    previous = _turn("Сколько сделок было в 2024?", _plan(filters=[YEAR_2024]))
    merged = resolve_follow_up(
        "А если взять предыдущий год?",
        _detection("А если взять предыдущий год?"),
        previous,
    )

    assert merged is not None
    dates = [f for f in merged.filters if f.field is FilterField.DEAL_DATE]
    assert list(dates[0].value) == ["2023-01-01", "2023-12-31"]


def test_export_follow_up_replaces_contract_type_filter() -> None:
    previous = _turn(
        "Сколько сделок за 2024?",
        _plan(
            filters=[
                YEAR_2024,
                FilterCondition(
                    field=FilterField.CONTRACT_TYPE,
                    operator=Operator.EQ,
                    value="Внутренний",
                ),
            ]
        ),
    )
    merged = resolve_follow_up(
        "А теперь только экспортные",
        _detection("А теперь только экспортные"),
        previous,
    )

    assert merged is not None
    contract = [f for f in merged.filters if f.field is FilterField.CONTRACT_TYPE]
    assert len(contract) == 1
    assert contract[0].value == "Экспортный"


def test_currency_follow_up_switches_amount_scope() -> None:
    previous = _turn(
        "Сумма сделок за 2024",
        _plan(
            intent=Intent.SUM_AMOUNT,
            metric=Metric.AMOUNT_SUM_UZS_CONVERTED,
            currency_mode=CurrencyMode.UZS_CONVERTED,
            filters=[YEAR_2024],
        ),
    )
    merged = resolve_follow_up("А в долларах?", _detection("А в долларах?"), previous)

    assert merged is not None
    assert merged.metric is Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY
    assert merged.currency_mode is CurrencyMode.USD_NATIVE
    currency = [f for f in merged.filters if f.field is FilterField.CURRENCY]
    assert currency and currency[0].value == "Доллар США"


def test_pronoun_with_one_referent_resolves_to_count() -> None:
    previous = _turn(
        "Покажи участника Zoket Invest",
        _plan(
            intent=Intent.PARTICIPANT_LOOKUP,
            subject=Subject.PARTICIPANTS,
            metric=Metric.RECORD_LOOKUP,
        ),
        referents=(Referent(name="Zoket Invest", role=EntityRole.BUYER),),
    )
    merged = resolve_follow_up(
        "Сколько у неё сделок?", _detection("Сколько у неё сделок?"), previous
    )

    assert merged is not None
    assert merged.intent is Intent.COUNT
    assert merged.subject is Subject.DEALS
    assert merged.metric is Metric.DEAL_COUNT
    assert len(merged.entity_candidates) == 1
    assert merged.entity_candidates[0].mention == "Zoket Invest"
    assert merged.entity_candidates[0].role is EntityRole.BUYER


def test_pronoun_with_several_referents_requires_clarification() -> None:
    previous = _turn(
        "Какая компания больше всех покупала?",
        _plan(intent=Intent.TOP_N, subject=Subject.PARTICIPANTS),
        referents=(
            Referent(name="Company A", role=EntityRole.BUYER),
            Referent(name="Company B", role=EntityRole.BUYER),
        ),
    )
    merged = resolve_follow_up(
        "Сколько у неё сделок?", _detection("Сколько у неё сделок?"), previous
    )

    assert merged is not None
    assert merged.decision is Decision.CLARIFY
    assert merged.clarification is not None
    assert merged.clarification.code is ClarificationCode.PARTICIPANT_AMBIGUOUS


def test_follow_up_without_previous_turn_is_not_guessed() -> None:
    assert resolve_follow_up("А за 2023?", _detection("А за 2023?"), None) is None


# ---------------------------------------------------------------------------
# session isolation
# ---------------------------------------------------------------------------


def test_memory_is_strictly_session_scoped() -> None:
    memory = InMemoryConversationMemory()
    plan = _plan(filters=[YEAR_2024])
    memory.append("a", _turn("q-a", plan))

    assert memory.get_last("a") is not None
    assert memory.get_last("b") is None
    assert memory.get_last("") is None

    memory.reset("a")
    assert memory.get_last("a") is None


def test_memory_trims_old_turns_and_evicts_old_sessions() -> None:
    memory = InMemoryConversationMemory(max_turns=2, max_sessions=2)
    plan = _plan()
    for index in range(3):
        memory.append("a", _turn(f"q{index}", plan))
    assert memory.get_last("a").question == "q2"  # only the last two survive

    memory.append("b", _turn("q-b", plan))
    memory.append("c", _turn("q-c", plan))
    # Adding the third session evicts the least recently used one ("a").
    assert memory.get_last("a") is None
    assert memory.get_last("b") is not None
    assert memory.get_last("c") is not None

    memory.append("d", _turn("q-d", plan))
    assert memory.get_last("b") is None
    assert memory.get_last("d") is not None


# ---------------------------------------------------------------------------
# local planner conversational gaps
# ---------------------------------------------------------------------------


def test_presence_question_maps_to_count_with_year_filter() -> None:
    plan = plan_question("Есть данные за 2023 год?", _detection("Есть данные за 2023 год?"))

    assert plan.decision is Decision.EXECUTE
    assert plan.intent is Intent.COUNT
    dates = [f for f in plan.filters if f.field is FilterField.DEAL_DATE]
    assert dates and list(dates[0].value) == ["2023-01-01", "2023-12-31"]


def test_superlative_deal_maps_to_min_max_not_top_n() -> None:
    plan = plan_question(
        "Покажи самую крупную сделку", _detection("Покажи самую крупную сделку")
    )

    assert plan.decision is Decision.EXECUTE
    assert plan.intent is Intent.MIN_MAX
    assert plan.metric is Metric.AMOUNT_MAX


@pytest.mark.parametrize(
    ("question", "group"),
    [
        ("Какая компания больше всех покупала?", GroupField.BUYER_INN),
        ("А кто больше всего продавал?", GroupField.SELLER_INN),
        ("Топ-10 покупателей по количеству сделок", GroupField.BUYER_INN),
    ],
)
def test_ranking_questions_map_to_top_n(question: str, group: GroupField) -> None:
    plan = plan_question(question, _detection(question))

    assert plan.decision is Decision.EXECUTE
    assert plan.intent is Intent.TOP_N
    assert group in plan.group_by


@pytest.mark.parametrize(
    "question",
    ["Сколько у неё сделок?", "uning bitimlari qancha?"],
)
def test_standalone_pronoun_requires_clarification(question: str) -> None:
    plan = plan_question(question, _detection(question))

    assert plan.decision is Decision.CLARIFY
    assert plan.clarification is not None
    assert plan.clarification.code is ClarificationCode.PARTICIPANT_AMBIGUOUS


# ---------------------------------------------------------------------------
# telemetry
# ---------------------------------------------------------------------------


def test_telemetry_is_populated_and_plan_is_recorded() -> None:
    response = Orchestrator().chat("Сколько сделок было в 2024 году?")

    assert response.status is ResponseStatus.FOUND
    telemetry = response.telemetry
    assert telemetry is not None
    assert telemetry.planner_provider == "local"
    assert telemetry.llm_attempted is False
    assert telemetry.query_plan is not None
    assert telemetry.query_plan["intent"] == "count"
    assert telemetry.planner_duration_ms is not None
    assert telemetry.sql_duration_ms is not None
    assert telemetry.answer_validation == "grounded"
    assert telemetry.session_id_present is False


# ---------------------------------------------------------------------------
# grounded answer generation
# ---------------------------------------------------------------------------


def _handler_result() -> HandlerResult:
    return HandlerResult(
        intent=Intent.COUNT,
        metric=Metric.DEAL_COUNT,
        rows=[ResultRow(value=106647)],
        matched_rows=106647,
        metric_sql="COUNT(*)",
        filters=[],
        grouping=[],
        source_tables=["deals"],
        currency_method=None,
        unit=None,
        notes=[],
    )


def _execution() -> ExecutionPlan:
    return ExecutionPlan(plan=_plan(filters=[YEAR_2024]), filters=[YEAR_2024])


def _bundle(answer: str = "За период 2024 год найдено сделок: 106 647.") -> VerifiedFactBundle:
    return VerifiedFactBundle(
        status="FOUND",
        facts={"value": 106647, "period": "2024 год"},
        evidence={"source_tables": ["deals"], "matched_rows": 106647},
        deterministic_answer=answer,
    )


def test_template_generator_returns_deterministic_answer() -> None:
    layer = GroundedAnswerLayer(TemplateAnswerGenerator())
    answer, status = layer.render(_bundle(), _execution(), _handler_result())

    assert answer == "За период 2024 год найдено сделок: 106 647."
    assert status == "template"


def test_generated_answer_with_matching_facts_is_accepted() -> None:
    generator = CallableAnswerGenerator(
        lambda bundle: "В 2024 году зафиксировано 106 647 сделок."
    )
    layer = GroundedAnswerLayer(generator)
    answer, status = layer.render(_bundle(), _execution(), _handler_result())

    assert answer == "В 2024 году зафиксировано 106 647 сделок."
    assert status == "generated_validated"


def test_generated_answer_with_invented_number_falls_back() -> None:
    generator = CallableAnswerGenerator(
        lambda bundle: "В 2024 году зафиксировано 106 648 сделок."
    )
    layer = GroundedAnswerLayer(generator)
    answer, status = layer.render(_bundle(), _execution(), _handler_result())

    assert answer == _bundle().deterministic_answer
    assert status == "fallback_template"


def test_generated_answer_with_invented_company_falls_back() -> None:
    generator = CallableAnswerGenerator(
        lambda bundle: "Больше всех покупала компания Hallucinated Trading."
    )
    layer = GroundedAnswerLayer(generator)
    answer, status = layer.render(_bundle(), _execution(), _handler_result())

    assert answer == _bundle().deterministic_answer
    assert status == "fallback_template"


def test_generator_exception_falls_back_to_template() -> None:
    def explode(bundle: VerifiedFactBundle) -> str:
        raise RuntimeError("provider down")

    layer = GroundedAnswerLayer(CallableAnswerGenerator(explode))
    answer, status = layer.render(_bundle(), _execution(), _handler_result())

    assert answer == _bundle().deterministic_answer
    assert status == "fallback_template"


def test_generated_answer_length_limit_falls_back() -> None:
    generator = CallableAnswerGenerator(lambda bundle: "x" * 2000)
    layer = GroundedAnswerLayer(generator, max_chars=100)
    answer, status = layer.render(_bundle(), _execution(), _handler_result())

    assert answer == _bundle().deterministic_answer
    assert status == "fallback_template"

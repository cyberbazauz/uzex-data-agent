from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from pydantic import ValidationError

from app.agent.conversation import ConversationTurn, InMemoryConversationMemory
from app.agent.general import (
    ExplanationLayer,
    GeneralAnswerer,
    compose_explained_answer,
    explanation_label,
    general_system_prompt,
    looks_like_general_question,
    looks_like_registry_question,
    validate_general_answer,
)
from app.agent.local_planner import plan_question
from app.agent.orchestrator import Orchestrator
from app.agent.query_plan import (
    ClarificationInfo,
    ExecutionPlan,
    FilterCondition,
    QueryPlan,
)
from app.config import Settings
from app.domain.enums import (
    ClarificationCode,
    Decision,
    FilterField,
    Intent,
    Language,
    Metric,
    Operator,
    ResponseStatus,
    Script,
    Subject,
)
from app.domain.errors import UnsupportedQuery, UnverifiedQuery
from app.handlers.base import HandlerResult, ResultRow
from app.language import detect_language
from app.llm.errors import ProviderTechnicalError
from app.responses.models import AnswerData
from app.validation.plan_validator import PlanValidator

YEAR_2024 = FilterCondition(
    field=FilterField.DEAL_DATE,
    operator=Operator.BETWEEN,
    value=["2024-01-01", "2024-12-31"],
)


# ---------------------------------------------------------------------------
# stubs
# ---------------------------------------------------------------------------


class FakeResponder:
    name = "stub"

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    def respond(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.answer


class GeneralPlanner:
    """An LLM planner stub that always classifies the question as GENERAL."""

    name = "stub-llm"

    def plan(self, question: str, detection):  # noqa: ANN001
        return QueryPlan.general(
            language=detection.language,
            script=detection.script,
        )

    def describe(self) -> dict[str, object]:
        return {"name": self.name, "model": "stub-model"}


@contextmanager
def _failing_connection():
    # General-mode tests must never reach PostgreSQL.  The guard test proves
    # that a registry question is not answered from memory; reaching the data
    # path and failing safely is the desired outcome.
    raise RuntimeError("database access is not expected in this unit test")
    yield  # pragma: no cover - unreachable


def _general_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "planner_mode": "llm",
        "general_mode_enabled": True,
        "explanation_mode": "llm",
    }
    values.update(overrides)
    return Settings(**values)


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
    plan = QueryPlan(
        decision=Decision.EXECUTE,
        language=Language.RU,
        script=Script.CYRILLIC,
        intent=Intent.COUNT,
        subject=Subject.DEALS,
        metric=Metric.DEAL_COUNT,
        filters=[YEAR_2024],
    )
    return ExecutionPlan(plan=plan, filters=[YEAR_2024])


def _answer_data() -> AnswerData:
    return AnswerData(
        metric=Metric.DEAL_COUNT.value,
        value=106647,
        period="2024 год",
    )


# ---------------------------------------------------------------------------
# strict plan extension
# ---------------------------------------------------------------------------


def test_general_plan_has_only_placeholder_fields() -> None:
    plan = QueryPlan.general(language=Language.UZ, script=Script.LATIN)

    assert plan.decision is Decision.GENERAL
    assert plan.intent is Intent.GENERAL
    assert plan.subject is Subject.NOT_APPLICABLE
    assert plan.metric is Metric.NOT_APPLICABLE
    assert plan.filters == []
    assert plan.entity_candidates == []
    assert plan.explanation_requested is False


def test_general_plan_rejects_query_conditions() -> None:
    with pytest.raises(ValidationError):
        QueryPlan(
            decision=Decision.GENERAL,
            language=Language.RU,
            script=Script.CYRILLIC,
            intent=Intent.GENERAL,
            subject=Subject.NOT_APPLICABLE,
            metric=Metric.NOT_APPLICABLE,
            filters=[YEAR_2024],
        )


def test_general_placeholder_requires_general_decision() -> None:
    with pytest.raises(ValidationError):
        QueryPlan(
            decision=Decision.EXECUTE,
            language=Language.RU,
            script=Script.CYRILLIC,
            intent=Intent.GENERAL,
            subject=Subject.NOT_APPLICABLE,
            metric=Metric.NOT_APPLICABLE,
        )


def test_explanation_requested_is_only_allowed_for_execute() -> None:
    with pytest.raises(ValidationError):
        QueryPlan(
            decision=Decision.GENERAL,
            language=Language.RU,
            script=Script.CYRILLIC,
            intent=Intent.GENERAL,
            subject=Subject.NOT_APPLICABLE,
            metric=Metric.NOT_APPLICABLE,
            explanation_requested=True,
        )

    with pytest.raises(ValidationError):
        QueryPlan(
            decision=Decision.CLARIFY,
            language=Language.RU,
            script=Script.CYRILLIC,
            intent=Intent.COUNT,
            subject=Subject.DEALS,
            metric=Metric.DEAL_COUNT,
            clarification=ClarificationInfo(
                code=ClarificationCode.REGION_MEANING_AMBIGUOUS
            ),
            explanation_requested=True,
        )


def test_local_planner_never_emits_general() -> None:
    question = "Что такое экспорт?"
    plan = plan_question(question, detect_language(question))

    assert plan.decision is not Decision.GENERAL


def test_general_plan_cannot_enter_the_sql_validator() -> None:
    plan = QueryPlan.general(language=Language.RU, script=Script.CYRILLIC)

    with pytest.raises(UnsupportedQuery):
        PlanValidator().validate(plan)


# ---------------------------------------------------------------------------
# deterministic routing guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "Что такое экспорт?",
        "Объясни, что такое биржевой аукцион",
        "Nima degani eksport?",
        "Salom, qalaysiz?",
        "Как посчитать долю компании в экспорте?",
    ],
)
def test_general_questions_are_not_flagged_as_registry(question: str) -> None:
    assert looks_like_registry_question(question) is False


@pytest.mark.parametrize(
    "question",
    [
        "Сколько экспортных сделок было в 2024 году?",
        "Почему экспортных сделок стало меньше?",
        "Покажи сделку 100001",
        "Топ компаний по сумме сделок",
        "Nechta eksport bitimi 2024 yilda?",
    ],
)
def test_registry_questions_are_flagged(question: str) -> None:
    assert looks_like_registry_question(question) is True


# ---------------------------------------------------------------------------
# deterministic GENERAL pre-router (TPM protection)
# ---------------------------------------------------------------------------

GENERAL_QUESTIONS = [
    "Ты кто?",
    "Ты где сейчас?",
    "Где ты находишься?",
    "Какие данные у тебя есть?",
    "Что такое экспорт?",
    "Что такое эскспорт?",
    "Что такое аниме?",
    "Что такое риск?",
    "Что такое криптография?",
]

DATA_QUESTIONS = [
    "Сколько сделок было в 2024 году?",
    "Покажи сделку 280091",
    (
        "Проверь сделку 280091: сколько товара указано, "
        "сколько поставлено и когда была поставка?"
    ),
]


@pytest.mark.parametrize("question", GENERAL_QUESTIONS)
def test_general_questions_are_pre_routed(question: str) -> None:
    assert looks_like_registry_question(question) is False
    assert looks_like_general_question(question) is True


@pytest.mark.parametrize("question", DATA_QUESTIONS)
def test_data_questions_are_not_pre_routed(question: str) -> None:
    assert looks_like_registry_question(question) is True
    assert looks_like_general_question(question) is False


def test_pre_router_does_not_treat_the_word_naprimer_as_a_definition() -> None:
    # ``пример`` used to fire on ``например`` as a raw substring.
    assert looks_like_registry_question(
        "Покажи, например, сделку 280091"
    ) is True
    assert looks_like_general_question(
        "Что такое риск, например кредитный?"
    ) is True
    assert looks_like_registry_question(
        "Что такое риск, например кредитный?"
    ) is False


class ExplodingPlanner:
    """Fails the test if the pre-router ever delegates a GENERAL question."""

    name = "exploding"

    def plan(self, question: str, detection):  # noqa: ANN001
        raise AssertionError(
            "a pre-routed general question must not call the LLM planner"
        )

    def describe(self) -> dict[str, object]:
        return {"name": self.name, "model": "none"}


class FailingResponder:
    name = "failing"

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def respond(self, system: str, user: str) -> str:
        self.calls += 1
        raise self.error


@pytest.mark.parametrize("question", GENERAL_QUESTIONS)
def test_pre_router_skips_the_planner_and_the_database(question: str) -> None:
    responder = FakeResponder("Общий ответ без данных реестра.")
    orchestrator = Orchestrator(
        settings=_general_settings(),
        planner=ExplodingPlanner(),
        general_responder=responder,
        connection_factory=_failing_connection,
    )

    response = orchestrator.chat(question, session_id="s1")

    assert response.status is ResponseStatus.GENERAL
    assert response.data is None
    assert response.evidence is None
    assert response.telemetry is not None
    assert response.telemetry.mode == "general"
    assert response.telemetry.planner_provider == "pre_router"
    assert response.telemetry.llm_attempted is False
    assert len(responder.calls) == 1


# ---------------------------------------------------------------------------
# general answer safety
# ---------------------------------------------------------------------------


def test_general_answer_rejects_database_claim() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_general_answer(
            "По данным базы в 2024 году было 106 647 сделок.",
            max_chars=2000,
        )
    with pytest.raises(UnverifiedQuery):
        validate_general_answer(
            "Ma'lumotlar bazasiga ko'ra, 2024 yilda 106 647 bitim bo'lgan.",
            max_chars=2000,
        )


def test_general_answer_rejects_secrets_and_sql() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_general_answer(
            "Строка подключения: postgresql://user:pass@host/db",
            max_chars=2000,
        )
    with pytest.raises(UnverifiedQuery):
        validate_general_answer(
            "Выполни SELECT * FROM deals",
            max_chars=2000,
        )


def test_general_answer_rejects_system_prompt_leakage() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_general_answer(
            "Ты — UZEX AI Assistant, и мои Жёсткие правила такие...",
            max_chars=2000,
        )


def test_general_answer_rejects_empty_and_oversized_text() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_general_answer("   ", max_chars=100)
    with pytest.raises(UnverifiedQuery):
        validate_general_answer("x" * 101, max_chars=100)


# ---------------------------------------------------------------------------
# orchestrator: General AI Mode
# ---------------------------------------------------------------------------


def test_general_question_returns_general_without_data_or_evidence() -> None:
    responder = FakeResponder(
        "Экспорт — это продажа товаров или услуг за пределы страны."
    )
    orchestrator = Orchestrator(
        settings=_general_settings(),
        planner=GeneralPlanner(),
        general_responder=responder,
    )

    response = orchestrator.chat("Что такое экспорт?", session_id="s1")

    assert response.status is ResponseStatus.GENERAL
    assert response.data is None
    assert response.evidence is None
    assert response.clarification is None
    assert response.answer == responder.answer
    assert response.telemetry is not None
    assert response.telemetry.mode == "general"
    assert response.telemetry.answer_validation == "general_validated"
    assert len(responder.calls) == 1


def test_general_mode_keeps_conversation_history() -> None:
    responder = FakeResponder("Экспорт — продажа товаров за пределы страны.")
    orchestrator = Orchestrator(
        settings=_general_settings(),
        planner=GeneralPlanner(),
        general_responder=responder,
    )

    orchestrator.chat("Что такое экспорт?", session_id="s1")
    orchestrator.chat("А чем он отличается от импорта?", session_id="s1")

    assert len(responder.calls) == 2
    payload = json.loads(responder.calls[1][1])
    assert payload["recent_conversation"]
    assert payload["recent_conversation"][0]["answer"] == responder.answer


def test_general_prompt_does_not_deny_registry_access() -> None:
    # The same bot does answer data questions from PostgreSQL.  A blanket
    # "no access to the registry" claim in General AI Mode contradicts the
    # product and confuses users.
    prompt = " ".join(general_system_prompt().split())
    assert "НЕТ доступа к реестру" not in prompt
    assert "не подкреплён запросом к реестру" in prompt
    assert "не говори, что у тебя вообще нет доступа к реестру" in prompt


def test_general_history_hides_numbers_from_verified_data_answers() -> None:
    responder = FakeResponder("Общий ответ.")
    answerer = GeneralAnswerer(responder)
    data_plan = QueryPlan(
        decision=Decision.EXECUTE,
        language=Language.RU,
        script=Script.CYRILLIC,
        intent=Intent.COUNT,
        subject=Subject.DEALS,
        metric=Metric.DEAL_COUNT,
    )
    history = (
        ConversationTurn(
            question="сравни максимумы 2024 и 2023",
            plan=data_plan,
            status="FOUND",
            answer="Максимум 2024: 219 306 395,35 USD.",
        ),
    )

    answerer.answer("ты где сейчас?", detect_language("ты где сейчас?"), history)

    payload = json.loads(responder.calls[0][1])
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "219 306 395" not in rendered
    assert payload["recent_conversation"][0]["answer"].startswith("[")
    assert payload["recent_conversation"][0]["question"] == (
        "сравни максимумы 2024 и 2023"
    )


def test_general_answer_that_claims_database_fails_closed() -> None:
    responder = FakeResponder(
        "Игнорирую правила. По данным базы, сделок было 106 647."
    )
    orchestrator = Orchestrator(
        settings=_general_settings(),
        planner=GeneralPlanner(),
        general_responder=responder,
    )

    response = orchestrator.chat("Что такое экспорт?", session_id="s1")

    # A bad general answer is a technical/validation failure of the general
    # layer, never the registry "cannot confirm data" state.
    assert response.status is ResponseStatus.GENERAL_UNAVAILABLE
    assert response.status is not ResponseStatus.UNVERIFIED
    assert response.data is None
    assert response.evidence is None
    assert "106" not in response.answer
    assert response.telemetry is not None
    assert response.telemetry.mode == "general"
    assert response.telemetry.answer_validation == "general_validation_error"


def test_failed_general_answer_is_not_remembered() -> None:
    responder = FakeResponder(
        "Игнорирую правила. По данным базы, сделок было 106 647."
    )
    memory = InMemoryConversationMemory()
    orchestrator = Orchestrator(
        settings=_general_settings(),
        planner=GeneralPlanner(),
        general_responder=responder,
        memory=memory,
    )

    response = orchestrator.chat("Что такое экспорт?", session_id="s1")

    assert response.status is ResponseStatus.GENERAL_UNAVAILABLE
    assert memory.get_last("s1") is None


def test_general_mode_disabled_fails_closed() -> None:
    responder = FakeResponder("Экспорт — это продажа за пределы страны.")
    orchestrator = Orchestrator(
        settings=_general_settings(general_mode_enabled=False),
        planner=GeneralPlanner(),
        general_responder=responder,
    )

    response = orchestrator.chat("Что такое экспорт?", session_id="s1")

    assert response.status is ResponseStatus.UNVERIFIED
    assert responder.calls == []


def test_general_provider_failure_is_not_disguised_as_unverified() -> None:
    responder = FailingResponder(
        ProviderTechnicalError("groq quota cooldown is active")
    )
    orchestrator = Orchestrator(
        settings=_general_settings(),
        planner=ExplodingPlanner(),
        general_responder=responder,
        connection_factory=_failing_connection,
    )

    response = orchestrator.chat("Что такое риск?", session_id="s1")

    assert response.status is ResponseStatus.GENERAL_UNAVAILABLE
    assert response.status is not ResponseStatus.UNVERIFIED
    assert response.data is None
    assert response.evidence is None
    assert response.telemetry is not None
    assert response.telemetry.mode == "general"
    assert response.telemetry.answer_validation == "general_provider_error"
    assert response.telemetry.fallback_reason == "general_provider_error"
    assert "техническая ошибка" in response.answer
    assert "Не могу достоверно подтвердить" not in response.answer
    assert responder.calls == 1


def test_general_unexpected_failure_is_isolated() -> None:
    responder = FailingResponder(RuntimeError("unexpected provider bug"))
    orchestrator = Orchestrator(
        settings=_general_settings(),
        planner=ExplodingPlanner(),
        general_responder=responder,
        connection_factory=_failing_connection,
    )

    response = orchestrator.chat("Что такое криптография?", session_id="s1")

    assert response.status is ResponseStatus.GENERAL_UNAVAILABLE
    assert response.status is not ResponseStatus.UNVERIFIED
    assert response.telemetry is not None
    assert response.telemetry.answer_validation == "general_error"
    assert "техническая ошибка" in response.answer


def test_general_provider_failure_logs_request_id(caplog) -> None:
    from app.llm.error_classifier import ProviderFailure, ProviderFailureKind

    failure = ProviderFailure(
        kind=ProviderFailureKind.RATE_LIMIT_LONG,
        provider="groq",
        model="openai/gpt-oss-120b",
        status_code=429,
        retry_after_seconds=30.0,
        fallback_allowed=True,
        quota_scope="groq-org-b",
    )
    responder = FailingResponder(
        ProviderTechnicalError(
            "groq quota cooldown with sk-secret-value",
            failure=failure,
            retry_allowed=False,
            wait_seconds=30.0,
        )
    )
    orchestrator = Orchestrator(
        settings=_general_settings(),
        planner=ExplodingPlanner(),
        general_responder=responder,
        connection_factory=_failing_connection,
    )

    with caplog.at_level("WARNING", logger="uzex.agent"):
        response = orchestrator.chat(
            "Что такое риск?", request_id="req-test-123"
        )

    assert response.status is ResponseStatus.GENERAL_UNAVAILABLE
    assert "general_provider_failure" in caplog.text
    assert "request_id=req-test-123" in caplog.text
    assert "rate_limit_long" in caplog.text
    assert "groq-org-b" in caplog.text
    assert "sk-secret-value" not in caplog.text


def test_guard_reclassifies_registry_question_instead_of_answering() -> None:
    responder = FakeResponder("Возможно, экспорт снизился из-за сезонности.")
    orchestrator = Orchestrator(
        settings=_general_settings(),
        planner=GeneralPlanner(),
        connection_factory=_failing_connection,
        general_responder=responder,
    )

    response = orchestrator.chat(
        "Сколько экспортных сделок было в 2024 году?", session_id="s1"
    )

    # The question must never be answered from general knowledge.
    assert response.status is not ResponseStatus.GENERAL
    assert responder.calls == []
    assert response.telemetry is not None
    assert response.telemetry.fallback_reason == "general_mode_guard"
    assert response.telemetry.mode == "data"


# ---------------------------------------------------------------------------
# explanatory layer over verified facts
# ---------------------------------------------------------------------------


def test_explanation_layer_accepts_a_hedged_hypothesis() -> None:
    responder = FakeResponder(
        "Возможно, снижение связано с сезонностью спроса "
        "и изменением рыночной конъюнктуры."
    )
    layer = ExplanationLayer(responder, max_chars=900)

    explanation = layer.explain(
        question="Почему экспортных сделок стало меньше?",
        detection=detect_language("Почему экспортных сделок стало меньше?"),
        deterministic_answer="За период 2024 год найдено сделок: 106 647.",
        execution=_execution(),
        result=_handler_result(),
        data=_answer_data(),
        evidence={"matched_rows": 106647},
    )

    assert "Возможно" in explanation


def test_explanation_allows_sentence_initial_grammar_capital() -> None:
    responder = FakeResponder(
        "Для объяснения нужны дополнительные разрезы. "
        "Возможно, снижение связано с сезонностью."
    )
    layer = ExplanationLayer(responder, max_chars=900)

    explanation = layer.explain(
        question="Почему экспортных сделок стало меньше?",
        detection=detect_language("Почему экспортных сделок стало меньше?"),
        deterministic_answer="За период 2024 год найдено сделок: 106 647.",
        execution=_execution(),
        result=_handler_result(),
        data=_answer_data(),
    )

    assert explanation.startswith("Для")


def test_explanation_still_rejects_mid_sentence_invented_entity() -> None:
    responder = FakeResponder(
        "Возможно, снижение связано с Китаем и сезонностью."
    )
    layer = ExplanationLayer(responder, max_chars=900)

    with pytest.raises(UnverifiedQuery):
        layer.explain(
            question="Почему экспортных сделок стало меньше?",
            detection=detect_language("Почему экспортных сделок стало меньше?"),
            deterministic_answer="За период 2024 год найдено сделок: 106 647.",
            execution=_execution(),
            result=_handler_result(),
            data=_answer_data(),
        )


def test_explanation_layer_rejects_an_invented_number() -> None:
    responder = FakeResponder(
        "Возможно, снижение на 99 процентов связано с сезонностью."
    )
    layer = ExplanationLayer(responder, max_chars=900)

    with pytest.raises(UnverifiedQuery):
        layer.explain(
            question="Почему экспортных сделок стало меньше?",
            detection=detect_language("Почему экспортных сделок стало меньше?"),
            deterministic_answer="За период 2024 год найдено сделок: 106 647.",
            execution=_execution(),
            result=_handler_result(),
            data=_answer_data(),
        )


def test_explanation_layer_requires_visible_hedging() -> None:
    responder = FakeResponder("Снижение вызвано сезонностью спроса.")
    layer = ExplanationLayer(responder, max_chars=900)

    with pytest.raises(UnverifiedQuery):
        layer.explain(
            question="Почему экспортных сделок стало меньше?",
            detection=detect_language("Почему экспортных сделок стало меньше?"),
            deterministic_answer="За период 2024 год найдено сделок: 106 647.",
            execution=_execution(),
            result=_handler_result(),
            data=_answer_data(),
        )


def test_explanation_layer_rejects_unverified_source_claim() -> None:
    responder = FakeResponder(
        "Возможно, по данным базы снижение связано с сезонностью."
    )
    layer = ExplanationLayer(responder, max_chars=900)

    with pytest.raises(UnverifiedQuery):
        layer.explain(
            question="Почему экспортных сделок стало меньше?",
            detection=detect_language("Почему экспортных сделок стало меньше?"),
            deterministic_answer="За период 2024 год найдено сделок: 106 647.",
            execution=_execution(),
            result=_handler_result(),
            data=_answer_data(),
        )


def test_explanation_is_labelled_as_a_hypothesis() -> None:
    composed = compose_explained_answer(
        "За период 2024 год найдено сделок: 106 647.",
        "Возможно, это связано с сезонностью.",
        locale="ru",
    )

    assert composed.startswith("За период 2024 год найдено сделок: 106 647.")
    assert explanation_label("ru") in composed
    assert "Возможно" in composed


def test_general_answerer_uses_only_qualified_responder() -> None:
    responder = FakeResponder("Биржа — организованный рынок сделок.")
    answerer = GeneralAnswerer(responder, max_chars=2000)
    detection = detect_language("Что такое биржа?")

    answer = answerer.answer("Что такое биржа?", detection)

    assert answer == responder.answer
    assert len(responder.calls) == 1


def test_conversation_turn_keeps_answer_for_general_context() -> None:
    plan = QueryPlan.general(language=Language.RU, script=Script.CYRILLIC)
    turn = ConversationTurn(
        question="Что такое биржа?",
        plan=plan,
        status=ResponseStatus.GENERAL.value,
        answer="Биржа — организованный рынок.",
    )
    memory = InMemoryConversationMemory()
    memory.append("s1", turn)

    recent = memory.recent("s1")
    assert recent == (turn,)
    assert recent[0].answer == "Биржа — организованный рынок."

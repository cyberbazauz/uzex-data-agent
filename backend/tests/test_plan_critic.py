from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.agent.orchestrator import Orchestrator
from app.agent.plan_critic import (
    CriticDecision,
    CriticIssue,
    PlanCritic,
    clarification_for,
    deterministic_risk,
)
from app.agent.query_plan import (
    EntityReference,
    QueryPlan,
)
from app.config import Settings
from app.domain.enums import (
    ClarificationCode,
    CurrencyMode,
    Decision,
    EntityRole,
    Intent,
    Language,
    MatchKind,
    Metric,
    ResponseStatus,
    Script,
    Subject,
)


def plan(**overrides: object) -> QueryPlan:
    values: dict[str, object] = {
        "decision": Decision.EXECUTE,
        "language": Language.RU,
        "script": Script.CYRILLIC,
        "intent": Intent.COUNT,
        "subject": Subject.DEALS,
        "metric": Metric.DEAL_COUNT,
        "currency_mode": CurrencyMode.NOT_APPLICABLE,
    }
    values.update(overrides)
    return QueryPlan(**values)  # type: ignore[arg-type]


def test_simple_count_has_no_risk_flags_and_passes() -> None:
    simple = plan()
    assert deterministic_risk("Сколько всего сделок?", simple) == []
    result = PlanCritic(enabled=True).review("Сколько всего сделок?", simple)
    assert result.decision is CriticDecision.PASS


def test_participant_side_missing_is_flagged() -> None:
    risky = plan(
        intent=Intent.TOP_N,
        subject=Subject.PARTICIPANTS,
        entity_candidates=[
            EntityReference(kind=MatchKind.PARTICIPANT, mention="Ромашка")
        ],
    )
    issues = deterministic_risk("Топ-5 участников", risky)
    assert CriticIssue.PARTICIPANT_SIDE_MISSING in issues


def test_critic_fails_closed_when_no_independent_critic_is_configured() -> None:
    risky = plan(
        intent=Intent.TOP_N,
        subject=Subject.PARTICIPANTS,
        entity_candidates=[
            EntityReference(kind=MatchKind.PARTICIPANT, mention="Ромашка")
        ],
    )
    result = PlanCritic(enabled=True).review("Топ-5 участников", risky)
    assert result.decision is CriticDecision.CLARIFY
    assert clarification_for(result) is ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS


def test_critic_cannot_override_deterministic_risk_with_pass() -> None:
    risky = plan(
        intent=Intent.TOP_N,
        subject=Subject.PARTICIPANTS,
        entity_candidates=[
            EntityReference(kind=MatchKind.PARTICIPANT, mention="Ромашка")
        ],
    )
    critic = PlanCritic(
        enabled=True, call=lambda question, candidate: '{"decision": "PASS", "issues": []}'
    )
    result = critic.review("Топ-5 участников", risky)
    assert result.decision is CriticDecision.CLARIFY


def test_critic_can_reject_but_never_adds_entities() -> None:
    simple = plan()
    critic = PlanCritic(
        enabled=True, call=lambda question, candidate: '{"decision": "REJECT", "issues": []}'
    )
    # No deterministic risk: critic is not called at all.
    assert critic.review("Сколько всего сделок?", simple).decision is CriticDecision.PASS


class _RiskyPlanner:
    name = "risky"

    def plan(self, question, detection):  # noqa: ANN001
        return plan(
            intent=Intent.TOP_N,
            subject=Subject.PARTICIPANTS,
            entity_candidates=[
                EntityReference(kind=MatchKind.PARTICIPANT, mention="Ромашка")
            ],
        )

    def describe(self) -> dict[str, object]:
        return {"name": self.name}


def test_orchestrator_does_not_run_critic_when_mode_is_off() -> None:
    @contextmanager
    def exploding_connection():
        raise RuntimeError("database must not be used in this test")
        yield  # pragma: no cover

    settings = Settings(llm_plan_critic_mode="off")
    response = Orchestrator(
        settings=settings,
        planner=_RiskyPlanner(),
        connection_factory=exploding_connection,
    ).chat("Топ-5 участников")

    # The critic did not intervene; the database failure is an honest UNVERIFIED.
    assert response.status is ResponseStatus.UNVERIFIED


def test_orchestrator_risk_based_critic_blocks_risky_plan_before_sql() -> None:
    @contextmanager
    def exploding_connection():
        raise AssertionError("critic must block before the database")
        yield  # pragma: no cover

    settings = Settings(llm_plan_critic_mode="risk_based")
    response = Orchestrator(
        settings=settings,
        planner=_RiskyPlanner(),
        connection_factory=exploding_connection,
    ).chat("Топ-5 участников")

    assert response.status is ResponseStatus.NEEDS_CLARIFICATION
    assert response.clarification is not None
    assert response.clarification.code is ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS
    assert response.data is None
    assert response.evidence is None


def test_critic_never_executes_a_clarify_plan() -> None:
    clarify = QueryPlan(
        decision=Decision.CLARIFY,
        language=Language.RU,
        script=Script.CYRILLIC,
        intent=Intent.COUNT,
        subject=Subject.DEALS,
        metric=Metric.DEAL_COUNT,
        clarification={"code": ClarificationCode.PERIOD_AMBIGUOUS},
    )
    assert deterministic_risk("любой вопрос", clarify) == []

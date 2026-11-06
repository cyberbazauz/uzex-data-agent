from __future__ import annotations

import json
from enum import StrEnum
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from app.agent.query_plan import QueryPlan
from app.domain.enums import (
    ClarificationCode,
    CurrencyMode,
    Decision,
    EntityRole,
    Intent,
    MatchKind,
    Metric,
    RegionMeaning,
    Subject,
)


class CriticIssue(StrEnum):
    PARTICIPANT_SIDE_MISSING = "PARTICIPANT_SIDE_MISSING"
    BROKER_SIDE_MISSING = "BROKER_SIDE_MISSING"
    MULTI_CURRENCY_TOTAL = "MULTI_CURRENCY_TOTAL"
    MIXED_UNITS_TOTAL = "MIXED_UNITS_TOTAL"
    AMBIGUOUS_PRODUCT = "AMBIGUOUS_PRODUCT"
    MULTIPLE_COMPANY_CANDIDATES = "MULTIPLE_COMPANY_CANDIDATES"
    COMPLEX_COMPARISON = "COMPLEX_COMPARISON"
    REGION_MEANING_UNCLEAR = "REGION_MEANING_UNCLEAR"
    MULTIPLE_CONDITIONS = "MULTIPLE_CONDITIONS"


class CriticDecision(StrEnum):
    PASS = "PASS"
    CLARIFY = "CLARIFY"
    REJECT = "REJECT"


class CriticResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: CriticDecision
    issues: list[CriticIssue] = Field(default_factory=list, max_length=10)


VIOLUME_METRICS = frozenset(
    {
        Metric.VOLUME_SUM_BY_UNIT,
        Metric.DELIVERED_VOLUME_BY_UNIT,
    }
)


class PlanCritic:
    """Risk-based second opinion for complex plans only.

    The critic never sees database rows and never produces a fact.  It may only
    return ``PASS``, ``CLARIFY`` or ``REJECT`` plus issue codes.  A critic that
    is disabled/unavailable returns a conservative deterministic result; it is
    never treated as proof that a plan is correct.

    Simple exact lookups and counts produce no risk flags and skip the second
    model call entirely, preserving quota.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        call: Callable[[str, QueryPlan], str] | None = None,
    ) -> None:
        self.enabled = enabled
        self._call = call

    def review(self, question: str, plan: QueryPlan) -> CriticResult:
        issues = deterministic_risk(question, plan)
        if not issues:
            return CriticResult(decision=CriticDecision.PASS)
        if not self.enabled or self._call is None:
            # No independent critic available: fail closed to clarification,
            # never to a more permissive execution.
            return CriticResult(decision=CriticDecision.CLARIFY, issues=issues)
        try:
            raw = self._call(question, plan)
            parsed = CriticResult.model_validate(json.loads(raw))
        except (ValueError, TypeError):
            return CriticResult(decision=CriticDecision.CLARIFY, issues=issues)
        if parsed.decision is CriticDecision.PASS and issues:
            # The critic may not override a deterministic risk flag with PASS.
            return CriticResult(decision=CriticDecision.CLARIFY, issues=issues)
        return parsed


def deterministic_risk(question: str, plan: QueryPlan) -> list[CriticIssue]:
    if plan.decision is not Decision.EXECUTE:
        return []
    issues: list[CriticIssue] = []
    text = question.casefold()

    participant_roles = {
        entity.role
        for entity in plan.entity_candidates
        if entity.kind is MatchKind.PARTICIPANT
    }
    if (
        plan.intent is Intent.TOP_N
        and plan.subject in (Subject.PARTICIPANTS, Subject.DEALS)
        and EntityRole.SELLER not in participant_roles
        and EntityRole.BUYER not in participant_roles
    ):
        group_sides = {
            field.value
            for field in plan.group_by
            if field.value in {"seller_inn", "buyer_inn", "seller_broker_code", "buyer_broker_code"}
        }
        if not group_sides:
            issues.append(CriticIssue.PARTICIPANT_SIDE_MISSING)

    if (
        plan.intent is Intent.TOP_N
        and plan.subject is Subject.BROKERS
        and EntityRole.BROKER_SELLER not in participant_roles
        and EntityRole.BROKER_BUYER not in participant_roles
    ):
        issues.append(CriticIssue.BROKER_SIDE_MISSING)

    if (
        plan.metric is Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY
        and plan.currency_mode is CurrencyMode.NATIVE_BY_CURRENCY
        and ("общ" in text or "jami" in text or "umumiy" in text)
        and not any(
            condition.field.value == "currency" for condition in plan.filters
        )
    ):
        # A total across several native currencies is not a single number.
        issues.append(CriticIssue.MULTI_CURRENCY_TOTAL)

    if (
        plan.metric in VIOLUME_METRICS
        and ("общ" in text or "jami" in text or "umumiy" in text)
        and not any(
            condition.field.value == "unit_name" for condition in plan.filters
        )
        and not any(field.value == "unit_name" for field in plan.group_by)
    ):
        issues.append(CriticIssue.MIXED_UNITS_TOTAL)

    for entity in plan.entity_candidates:
        if entity.kind is MatchKind.PRODUCT and len(entity.mention.strip()) < 3:
            issues.append(CriticIssue.AMBIGUOUS_PRODUCT)
        if entity.kind is MatchKind.REGION and entity.meaning is None:
            issues.append(CriticIssue.REGION_MEANING_UNCLEAR)
        if entity.kind is MatchKind.PARTICIPANT and entity.role is None:
            issues.append(CriticIssue.PARTICIPANT_SIDE_MISSING)

    if plan.intent is Intent.COMPARE and len(plan.filters) >= 2:
        issues.append(CriticIssue.COMPLEX_COMPARISON)
    if len(plan.filters) >= 4:
        issues.append(CriticIssue.MULTIPLE_CONDITIONS)

    # Stable order, de-duplicated.
    return list(dict.fromkeys(issues))


CLARIFICATION_BY_ISSUE: dict[CriticIssue, ClarificationCode] = {
    CriticIssue.PARTICIPANT_SIDE_MISSING: ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS,
    CriticIssue.BROKER_SIDE_MISSING: ClarificationCode.BROKER_SIDE_AMBIGUOUS,
    CriticIssue.MULTI_CURRENCY_TOTAL: ClarificationCode.CURRENCY_AMBIGUOUS,
    CriticIssue.MIXED_UNITS_TOTAL: ClarificationCode.UNIT_AMBIGUOUS,
    CriticIssue.AMBIGUOUS_PRODUCT: ClarificationCode.PRODUCT_AMBIGUOUS,
    CriticIssue.MULTIPLE_COMPANY_CANDIDATES: ClarificationCode.PARTICIPANT_AMBIGUOUS,
    CriticIssue.COMPLEX_COMPARISON: ClarificationCode.PERIOD_AMBIGUOUS,
    CriticIssue.REGION_MEANING_UNCLEAR: ClarificationCode.REGION_MEANING_AMBIGUOUS,
    CriticIssue.MULTIPLE_CONDITIONS: ClarificationCode.PERIOD_AMBIGUOUS,
}


def clarification_for(result: CriticResult) -> ClarificationCode:
    if result.issues:
        return CLARIFICATION_BY_ISSUE.get(
            result.issues[0], ClarificationCode.PERIOD_AMBIGUOUS
        )
    return ClarificationCode.PERIOD_AMBIGUOUS

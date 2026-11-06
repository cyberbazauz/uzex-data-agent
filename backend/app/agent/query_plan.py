from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import (
    ClarificationCode,
    CurrencyMode,
    Decision,
    Direction,
    EntityRole,
    FilterField,
    GroupField,
    Intent,
    Language,
    MatchKind,
    Metric,
    Operator,
    OrderField,
    RegionMeaning,
    Script,
    Subject,
)

FilterValue = Annotated[
    Union[str, int, list[str], list[int]],
    Field(union_mode="smart"),
]

DATE_FIELDS = frozenset({FilterField.DEAL_DATE})
IDENTIFIER_FIELDS = frozenset(
    {
        FilterField.DEAL_NO,
        FilterField.CONTRACT_NO,
        FilterField.SELLER_INN,
        FilterField.BUYER_INN,
        FilterField.SELLER_BROKER_CODE,
        FilterField.BUYER_BROKER_CODE,
        FilterField.HS_CODE,
    }
)
NAME_FIELDS = frozenset({FilterField.PRODUCT_NAME, FilterField.DEAL_PRODUCT_NAME})
REGION_FIELDS = frozenset(
    {
        FilterField.WAREHOUSE_REGION,
        FilterField.SELLER_COMPANY_REGION,
        FilterField.BUYER_COMPANY_REGION,
    }
)
CATEGORICAL_FIELDS = frozenset(
    {
        FilterField.TRADE_TYPE,
        FilterField.STATUS,
        FilterField.CONTRACT_TYPE,
        FilterField.CONTRACT_FORM,
        FilterField.CURRENCY,
        FilterField.PRODUCT_GROUP,
        FilterField.PRODUCT_CATEGORY,
        FilterField.INDUSTRY,
        FilterField.UNIT_NAME,
    }
)


class FilterCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: FilterField
    operator: Operator
    value: FilterValue

    @model_validator(mode="after")
    def _validate_value(self) -> "FilterCondition":
        field = self.field
        operator = self.operator
        value = self.value

        if field in DATE_FIELDS:
            if operator not in (Operator.EQ, Operator.GTE, Operator.LTE, Operator.BETWEEN):
                raise ValueError(f"operator {operator} is not allowed for date field {field}")
            if operator is Operator.BETWEEN:
                if not isinstance(value, list) or len(value) != 2:
                    raise ValueError("between requires a list of exactly two ISO dates")
                try:
                    start = date.fromisoformat(str(value[0]))
                    end = date.fromisoformat(str(value[1]))
                except ValueError as exc:
                    raise ValueError("between values must be ISO dates YYYY-MM-DD") from exc
                if start > end:
                    raise ValueError("date range is reversed")
            else:
                if isinstance(value, list):
                    raise ValueError(f"operator {operator} requires a single ISO date")
                try:
                    date.fromisoformat(str(value))
                except ValueError as exc:
                    raise ValueError("date value must be ISO YYYY-MM-DD") from exc
            return self

        if operator is Operator.BETWEEN:
            raise ValueError("between is only allowed for deal_date")

        if operator is Operator.CONTAINS:
            if field not in NAME_FIELDS:
                raise ValueError("contains is only allowed for product name fields")
            if not isinstance(value, str) or not value.strip():
                raise ValueError("contains requires a non-empty text value")
            return self

        if operator is Operator.IN:
            if not isinstance(value, list) or not value:
                raise ValueError("in requires a non-empty list")
            if len(value) > 50:
                raise ValueError("in accepts at most 50 values")
            items = value
        else:
            if isinstance(value, list):
                raise ValueError(f"operator {operator} requires a scalar value")
            items = [value]

        if field in IDENTIFIER_FIELDS:
            for item in items:
                text = str(item).strip()
                if not text or len(text) > 200:
                    raise ValueError(f"invalid identifier value for {field}")
            if field in (FilterField.SELLER_INN, FilterField.BUYER_INN):
                for item in items:
                    text = str(item).strip()
                    if not (text.isdigit() and len(text) == 9):
                        # A company name is allowed here and is resolved by the
                        # Entity Resolver. It is never inserted into SQL as text
                        # except as a parameter after exact resolution.
                        if len(text) < 2:
                            raise ValueError("seller/buyer filter must be a 9-digit INN or a name")
            if field is FilterField.HS_CODE:
                for item in items:
                    text = str(item).strip()
                    if not (text.isdigit() and 4 <= len(text) <= 12):
                        raise ValueError("hs_code must contain 4-12 digits")
            return self

        if field in REGION_FIELDS:
            for item in items:
                if not isinstance(item, int):
                    text = str(item).strip()
                    if not text or len(text) > 120:
                        raise ValueError("invalid region value")
            return self

        for item in items:
            if isinstance(item, int):
                raise ValueError(f"{field} expects text values")
            text = str(item).strip()
            if not text or len(text) > 200:
                raise ValueError(f"invalid value for {field}")
        return self


class EntityReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: MatchKind
    mention: str = Field(min_length=1, max_length=200)
    role: EntityRole | None = None
    meaning: RegionMeaning | None = None

    @model_validator(mode="after")
    def _validate_mention(self) -> "EntityReference":
        if not self.mention.strip():
            raise ValueError("entity mention must not be blank")
        if self.kind is MatchKind.REGION and self.meaning is None:
            # The resolver must ask warehouse/company rather than guess.
            pass
        if self.kind is MatchKind.BROKER and self.role not in (
            None,
            EntityRole.BROKER_SELLER,
            EntityRole.BROKER_BUYER,
            EntityRole.ANY,
        ):
            raise ValueError("broker role must be broker_seller, broker_buyer, any or null")
        if self.kind is MatchKind.PARTICIPANT and self.role not in (
            None,
            EntityRole.SELLER,
            EntityRole.BUYER,
            EntityRole.ANY,
        ):
            raise ValueError("participant role must be seller, buyer, any or null")
        return self


class ClarificationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ClarificationCode
    options: list[str] = Field(default_factory=list, max_length=10)


class ComparisonPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=50)
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def _validate_period(self) -> "ComparisonPeriod":
        if self.date_from > self.date_to:
            raise ValueError("comparison period is reversed")
        return self


class ComparisonSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: ComparisonPeriod
    right: ComparisonPeriod

    @model_validator(mode="after")
    def _validate_comparison(self) -> "ComparisonSpec":
        if self.left.date_from <= self.right.date_to and self.right.date_from <= self.left.date_to:
            raise ValueError("comparison periods must not overlap")
        return self


class OrderCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: OrderField
    direction: Direction = Direction.ASC


class QueryPlan(BaseModel):
    """Strict, server-validated representation of a user question.

    The model contains no SQL, code, tool calls, result values or final answer.
    Unknown keys are rejected.  The LLM may only produce this shape.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    decision: Decision
    language: Language
    script: Script
    intent: Intent
    subject: Subject
    metric: Metric
    filters: list[FilterCondition] = Field(default_factory=list, max_length=20)
    group_by: list[GroupField] = Field(default_factory=list, max_length=3)
    order_by: list[OrderCondition] = Field(default_factory=list, max_length=3)
    limit: int | None = Field(default=None, ge=1, le=20)
    currency_mode: CurrencyMode = CurrencyMode.NOT_APPLICABLE
    entity_candidates: list[EntityReference] = Field(default_factory=list, max_length=10)
    clarification: ClarificationInfo | None = None
    comparison: ComparisonSpec | None = None
    # A causal question ("why did exports fall?") needs verified facts first
    # and only then an explicitly labelled hypothesis.  The plan never carries
    # the explanation itself.
    explanation_requested: bool = False

    @classmethod
    def general(cls, *, language: Language, script: Script) -> "QueryPlan":
        """Build the only valid representation of General AI Mode."""

        return cls(
            decision=Decision.GENERAL,
            language=language,
            script=script,
            intent=Intent.GENERAL,
            subject=Subject.NOT_APPLICABLE,
            metric=Metric.NOT_APPLICABLE,
        )

    @model_validator(mode="after")
    def _validate_plan_consistency(self) -> "QueryPlan":
        if self.decision is Decision.GENERAL:
            if (
                self.intent is not Intent.GENERAL
                or self.subject is not Subject.NOT_APPLICABLE
                or self.metric is not Metric.NOT_APPLICABLE
            ):
                raise ValueError(
                    "decision GENERAL requires intent=general, "
                    "subject=not_applicable and metric=not_applicable"
                )
            if (
                self.filters
                or self.group_by
                or self.order_by
                or self.entity_candidates
                or self.comparison is not None
                or self.limit is not None
                or self.currency_mode is not CurrencyMode.NOT_APPLICABLE
            ):
                raise ValueError(
                    "decision GENERAL must not contain query conditions"
                )
        elif (
            self.decision not in (Decision.UNSUPPORTED, Decision.CLARIFY)
            and (
                self.intent is Intent.GENERAL
                or self.subject is Subject.NOT_APPLICABLE
                or self.metric is Metric.NOT_APPLICABLE
            )
        ):
            # CLARIFY executes nothing, exactly like UNSUPPORTED: the whole
            # payload is the clarification request.  Models routinely mark
            # such a plan with the general sentinels (intent=general,
            # subject/metric=not_applicable); rejecting it turns a legitimate
            # "please clarify" into UNVERIFIED.  EXECUTE plans stay strict.
            raise ValueError(
                "general intent/subject/metric are only allowed with decision "
                "GENERAL (or a non-executable UNSUPPORTED/CLARIFY plan)"
            )

        if self.decision is not Decision.EXECUTE and self.explanation_requested:
            raise ValueError(
                "explanation_requested is only allowed with decision EXECUTE"
            )

        if self.decision is Decision.CLARIFY and self.clarification is None:
            raise ValueError("decision CLARIFY requires clarification info")
        if self.decision is not Decision.CLARIFY and self.clarification is not None:
            raise ValueError("clarification is only allowed with decision CLARIFY")

        seen_filters: set[str] = set()
        for condition in self.filters:
            key = json.dumps(
                [condition.field.value, condition.operator.value, condition.value],
                ensure_ascii=False,
                sort_keys=True,
            )
            if key in seen_filters:
                raise ValueError(f"duplicate filter condition: {key}")
            seen_filters.add(key)

        seen_order: set[str] = set()
        for order in self.order_by:
            if order.field.value in seen_order:
                raise ValueError(f"duplicate order field: {order.field}")
            seen_order.add(order.field.value)

        if self.comparison is not None and self.intent is not Intent.COMPARE:
            raise ValueError("comparison is only allowed for intent compare")
        if self.intent is Intent.COMPARE and self.comparison is None:
            raise ValueError("intent compare requires comparison periods")

        mentions: set[str] = set()
        for entity in self.entity_candidates:
            key = f"{entity.kind.value}:{entity.mention.strip().casefold()}"
            if key in mentions:
                raise ValueError(f"duplicate entity candidate: {entity.mention}")
            mentions.add(key)
        return self

    def filter_by_field(self, field: FilterField) -> list[FilterCondition]:
        return [item for item in self.filters if item.field is field]

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


class ResolvedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: MatchKind
    id: str
    label: str
    mention: str | None = None
    role: EntityRole | None = None
    meaning: RegionMeaning | None = None
    matched_by: str
    contract_count: int | None = None
    candidates_count: int | None = None


class ExecutionPlan(BaseModel):
    """A validated plan plus canonical IDs produced by Entity Resolution."""

    model_config = ConfigDict(extra="forbid")

    plan: QueryPlan
    filters: list[FilterCondition]
    entities: list[ResolvedEntity] = Field(default_factory=list)
    evidence_notes: list[str] = Field(default_factory=list)
    product_contract_count: int | None = None
    implicit_currency_scope: str | None = None

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import ClarificationCode, ResponseStatus
from app.handlers.base import Evidence


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(StrictModel):
    question: str = Field(min_length=1, max_length=5000)
    session_id: str | None = Field(default=None, max_length=128)
    request_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _question_not_blank(self) -> "ChatRequest":
        if not self.question.strip():
            raise ValueError("question must not be blank")
        return self


class DataRow(StrictModel):
    """One factual row returned by a handler.

    ``value`` keeps the exact SQL number.  Decimal values are serialised as
    JSON strings so money never becomes a binary float anywhere on the way to
    the client.
    """

    label: str | None = None
    value: Decimal | int | None = None
    unit: str | None = None
    currency: str | None = None
    group: dict[str, Any] = Field(default_factory=dict)
    fields: dict[str, Any] = Field(default_factory=dict)


class AnswerData(StrictModel):
    metric: str
    value: Decimal | int | None = None
    unit: str | None = None
    currency: str | None = None
    method: str | None = None
    period: str | None = None
    rows: list[DataRow] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ClarificationPayload(StrictModel):
    code: ClarificationCode
    question: str
    options: list[str] = Field(default_factory=list, max_length=10)


class Telemetry(StrictModel):
    """Internal execution telemetry, never rendered to a Telegram user."""

    planner_provider: str
    planner_model: str | None = None
    llm_attempted: bool = False
    llm_duration_ms: int | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None
    cache_hit: bool = False
    query_plan: dict[str, Any] | None = None
    planner_duration_ms: int | None = None
    sql_duration_ms: int | None = None
    answer_validation: str | None = None
    # "data", "data_explanation" or "general".  General answers are never
    # registry facts even though they share the same response envelope.
    mode: str | None = None
    explanation_validation: str | None = None
    context_used: bool = False
    session_id_present: bool = False


class ChatResponse(StrictModel):
    status: ResponseStatus
    language: str
    script: str | None = None
    locale: str | None = None
    answer: str
    data: AnswerData | None = None
    clarification: ClarificationPayload | None = None
    evidence: Evidence | None = None
    duration_ms: int = Field(ge=0)
    request_id: str
    telemetry: Telemetry | None = None

    @model_validator(mode="after")
    def _status_invariants(self) -> "ChatResponse":
        if self.status is ResponseStatus.FOUND:
            if self.data is None:
                raise ValueError("FOUND requires data")
            if self.evidence is None:
                raise ValueError("FOUND requires evidence")
            if self.clarification is not None:
                raise ValueError("FOUND must not carry clarification")
        elif self.status is ResponseStatus.NOT_FOUND:
            if self.data is not None:
                raise ValueError("NOT_FOUND must not carry data")
            if self.clarification is not None:
                raise ValueError("NOT_FOUND must not carry clarification")
            if self.evidence is not None and self.evidence.matched_rows != 0:
                raise ValueError("NOT_FOUND evidence must report zero matched rows")
        elif self.status is ResponseStatus.NEEDS_CLARIFICATION:
            if self.clarification is None:
                raise ValueError("NEEDS_CLARIFICATION requires clarification")
            if self.data is not None or self.evidence is not None:
                raise ValueError("NEEDS_CLARIFICATION must not carry data or evidence")
        elif self.status is ResponseStatus.GENERAL:
            # General AI Mode is conversational general knowledge, never a
            # verified registry answer.  It must not smuggle in facts.
            if self.data is not None or self.evidence is not None:
                raise ValueError("GENERAL must not carry data or evidence")
            if self.clarification is not None:
                raise ValueError("GENERAL must not carry clarification")
        else:
            if self.data is not None or self.clarification is not None:
                raise ValueError(f"{self.status.value} must not carry data or clarification")
            if self.evidence is not None:
                raise ValueError(f"{self.status.value} must not carry evidence")
        return self


class HealthResponse(StrictModel):
    status: Literal["ok"] = "ok"
    version: str
    timestamp: datetime


class ReadinessChecks(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    database: bool
    dataset: bool
    schema_ready: bool = Field(alias="schema", serialization_alias="schema")


class ReadyResponse(StrictModel):
    status: Literal["ready", "not_ready"]
    checks: ReadinessChecks
    dataset_version: str | None = None
    dataset_cutoff: str | None = None


class MetaResponse(StrictModel):
    status: Literal["ready", "unavailable"]
    dataset_version: str | None = None
    dataset_cutoff: str | None = None
    dataset_loaded_at: str | None = None
    total_deals: int | None = None
    contracts: int | None = None
    participants: int | None = None
    brokers: int | None = None
    usd_rates: int | None = None
    supported_languages: list[str] = Field(default_factory=list)
    supported_locales: list[str] = Field(default_factory=list)
    planner_mode: str


class ExampleQuestion(StrictModel):
    question: str
    language: str
    topic: str
    is_example: Literal[True] = True


class ExamplesResponse(StrictModel):
    examples: list[ExampleQuestion]
    note: str

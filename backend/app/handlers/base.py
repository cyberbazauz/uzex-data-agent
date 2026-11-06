from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Intent, Metric


class ResultRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    value: Decimal | int | None = None
    unit: str | None = None
    currency: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_tables: list[str]
    dataset_version: str
    dataset_cutoff: str
    filters: list[dict[str, Any]]
    metric: str
    matched_rows: int
    grouping: list[str]
    currency_method: str | None
    unit: str | None
    execution_id: str
    notes: list[str] = Field(default_factory=list)


class HandlerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    metric: Metric
    rows: list[ResultRow] = Field(default_factory=list)
    matched_rows: int = 0
    metric_sql: str = ""
    filters: list[dict[str, Any]] = Field(default_factory=list)
    grouping: list[str] = Field(default_factory=list)
    source_tables: list[str] = Field(default_factory=list)
    currency_method: str | None = None
    unit: str | None = None
    notes: list[str] = Field(default_factory=list)
    dataset_version: str | None = None
    dataset_cutoff: str | None = None
    execution_id: str | None = None

    @property
    def has_answer(self) -> bool:
        """True only when the executed query produced a factual value.

        A record lookup is a fact as soon as a row exists.  An aggregate is a
        fact only when at least one row has a non-NULL value: SQL NULL is not
        converted to zero.
        """
        if not self.rows:
            return False
        if self.metric is Metric.RECORD_LOOKUP:
            return True
        return any(row.value is not None for row in self.rows)

    def with_dataset(
        self,
        dataset_version: str,
        dataset_cutoff: date | str,
        execution_id: str,
    ) -> "HandlerResult":
        return self.model_copy(
            update={
                "dataset_version": dataset_version,
                "dataset_cutoff": str(dataset_cutoff),
                "execution_id": execution_id,
            }
        )

    def to_evidence(self, *, require_answer: bool = True) -> Evidence | None:
        if require_answer and not self.has_answer:
            return None
        if not self.dataset_version or not self.dataset_cutoff or not self.execution_id:
            raise ValueError("handler result is missing dataset/execution metadata")
        return Evidence(
            source_tables=self.source_tables,
            dataset_version=self.dataset_version,
            dataset_cutoff=self.dataset_cutoff,
            filters=self.filters,
            metric=self.metric_sql,
            matched_rows=self.matched_rows,
            grouping=self.grouping,
            currency_method=self.currency_method,
            unit=self.unit,
            execution_id=self.execution_id,
            notes=self.notes,
        )

from __future__ import annotations

from decimal import Decimal

import pytest

from app.agent.query_plan import ExecutionPlan, QueryPlan
from app.domain.enums import (
    CURRENCY_USD,
    CURRENCY_UZS,
    Decision,
    Intent,
    Language,
    Metric,
    ResponseStatus,
    Script,
    Subject,
)
from app.domain.errors import UnverifiedQuery
from app.handlers.base import Evidence, HandlerResult, ResultRow
from app.validation.result_validator import validate_result

DATASET_VERSION = "sha256:" + "b" * 64
DATASET_CUTOFF = "2026-10-30"


def execution(intent: Intent, metric: Metric, subject: Subject = Subject.DEALS) -> ExecutionPlan:
    plan = QueryPlan(
        decision=Decision.EXECUTE,
        language=Language.RU,
        script=Script.CYRILLIC,
        intent=intent,
        subject=subject,
        metric=metric,
    )
    return ExecutionPlan(plan=plan, filters=[])


def result(
    intent: Intent,
    metric: Metric,
    rows: list[ResultRow],
    *,
    matched_rows: int,
    grouping: list[str] | None = None,
    currency_method: str | None = None,
    source_tables: list[str] | None = None,
    metric_sql: str = "COUNT(DISTINCT deals.deal_no)",
) -> HandlerResult:
    return HandlerResult(
        intent=intent,
        metric=metric,
        rows=rows,
        matched_rows=matched_rows,
        metric_sql=metric_sql,
        grouping=grouping or [],
        source_tables=source_tables or ["deals"],
        currency_method=currency_method,
    ).with_dataset(DATASET_VERSION, DATASET_CUTOFF, "req_test")


def test_valid_count_result_passes() -> None:
    validate_result(
        result(Intent.COUNT, Metric.DEAL_COUNT, [ResultRow(value=106647)], matched_rows=106647),
        execution(Intent.COUNT, Metric.DEAL_COUNT),
        dataset_version=DATASET_VERSION,
        dataset_cutoff=DATASET_CUTOFF,
    )


def test_valid_native_money_passes() -> None:
    validate_result(
        result(
            Intent.SUM_AMOUNT,
            Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
            [ResultRow(value=Decimal("10.50"), currency=CURRENCY_USD)],
            matched_rows=2,
            grouping=["currency"],
        ),
        execution(Intent.SUM_AMOUNT, Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY),
    )


def test_valid_converted_money_passes() -> None:
    validate_result(
        result(
            Intent.SUM_AMOUNT,
            Metric.AMOUNT_SUM_UZS_CONVERTED,
            [ResultRow(value=Decimal("10.50"), currency=CURRENCY_UZS)],
            matched_rows=2,
            currency_method="USD rate on deal date",
        ),
        execution(Intent.SUM_AMOUNT, Metric.AMOUNT_SUM_UZS_CONVERTED),
    )


def test_float_in_fact_fields_fails() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_result(
            result(
                Intent.COUNT,
                Metric.DEAL_COUNT,
                [ResultRow(value=1, extra={"rate": 1.5})],
                matched_rows=1,
            ),
            execution(Intent.COUNT, Metric.DEAL_COUNT),
        )


def test_negative_count_fails() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_result(
            result(Intent.COUNT, Metric.DEAL_COUNT, [ResultRow(value=-1)], matched_rows=1),
            execution(Intent.COUNT, Metric.DEAL_COUNT),
        )


def test_fractional_count_fails() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_result(
            result(
                Intent.COUNT,
                Metric.DEAL_COUNT,
                [ResultRow(value=Decimal("1.5"))],
                matched_rows=1,
            ),
            execution(Intent.COUNT, Metric.DEAL_COUNT),
        )


def test_native_money_without_currency_fails() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_result(
            result(
                Intent.SUM_AMOUNT,
                Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
                [ResultRow(value=Decimal("10"))],
                matched_rows=1,
            ),
            execution(Intent.SUM_AMOUNT, Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY),
        )


def test_converted_money_reported_in_usd_fails() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_result(
            result(
                Intent.SUM_AMOUNT,
                Metric.AMOUNT_SUM_UZS_CONVERTED,
                [ResultRow(value=Decimal("10"), currency=CURRENCY_USD)],
                matched_rows=1,
                currency_method="documented",
            ),
            execution(Intent.SUM_AMOUNT, Metric.AMOUNT_SUM_UZS_CONVERTED),
        )


def test_converted_money_without_method_fails() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_result(
            result(
                Intent.SUM_AMOUNT,
                Metric.AMOUNT_SUM_UZS_CONVERTED,
                [ResultRow(value=Decimal("10"), currency=CURRENCY_UZS)],
                matched_rows=1,
            ),
            execution(Intent.SUM_AMOUNT, Metric.AMOUNT_SUM_UZS_CONVERTED),
        )


def test_volume_without_unit_fails() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_result(
            result(
                Intent.SUM_VOLUME,
                Metric.VOLUME_SUM_BY_UNIT,
                [ResultRow(value=Decimal("10"))],
                matched_rows=1,
            ),
            execution(Intent.SUM_VOLUME, Metric.VOLUME_SUM_BY_UNIT),
        )


def test_compare_null_period_requires_absent_marker() -> None:
    from datetime import date

    from app.agent.query_plan import ComparisonPeriod, ComparisonSpec

    comparison = ComparisonSpec(
        left=ComparisonPeriod(label="2023", date_from=date(2023, 1, 1), date_to=date(2023, 12, 31)),
        right=ComparisonPeriod(label="2024", date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)),
    )
    plan = QueryPlan(
        decision=Decision.EXECUTE,
        language=Language.RU,
        script=Script.CYRILLIC,
        intent=Intent.COMPARE,
        subject=Subject.DEALS,
        metric=Metric.DEAL_COUNT,
        comparison=comparison,
    )
    with pytest.raises(UnverifiedQuery):
        validate_result(
            result(
                Intent.COMPARE,
                Metric.DEAL_COUNT,
                [ResultRow(group={"period": "2024"}, value=None)],
                matched_rows=1,
                grouping=["period"],
            ),
            ExecutionPlan(plan=plan, filters=[]),
        )


def test_zero_rows_with_result_rows_fails() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_result(
            result(Intent.COUNT, Metric.DEAL_COUNT, [ResultRow(value=1)], matched_rows=0),
            execution(Intent.COUNT, Metric.DEAL_COUNT),
        )


def test_dataset_mismatch_fails() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_result(
            result(Intent.COUNT, Metric.DEAL_COUNT, [ResultRow(value=1)], matched_rows=1),
            execution(Intent.COUNT, Metric.DEAL_COUNT),
            dataset_version="sha256:" + "c" * 64,
        )


def test_missing_dataset_metadata_fails() -> None:
    handler = HandlerResult(
        intent=Intent.COUNT,
        metric=Metric.DEAL_COUNT,
        rows=[ResultRow(value=1)],
        matched_rows=1,
        metric_sql="COUNT(*)",
        source_tables=["deals"],
    )
    with pytest.raises(UnverifiedQuery):
        validate_result(handler, execution(Intent.COUNT, Metric.DEAL_COUNT))


def test_duplicate_grouping_keys_fail() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_result(
            result(
                Intent.TREND,
                Metric.DEAL_COUNT,
                [
                    ResultRow(group={"year": 2023}, value=1),
                    ResultRow(group={"year": 2023}, value=2),
                ],
                matched_rows=3,
                grouping=["year"],
            ),
            execution(Intent.TREND, Metric.DEAL_COUNT),
        )


def test_record_lookup_requires_extra_fields() -> None:
    with pytest.raises(UnverifiedQuery):
        validate_result(
            result(
                Intent.DEAL_LOOKUP,
                Metric.RECORD_LOOKUP,
                [ResultRow()],
                matched_rows=1,
                metric_sql="SELECT deal row",
            ),
            execution(Intent.DEAL_LOOKUP, Metric.RECORD_LOOKUP),
        )


def test_evidence_shape_and_dataset_checks() -> None:
    from app.validation.evidence_validator import validate_evidence

    evidence = Evidence(
        source_tables=["deals"],
        dataset_version=DATASET_VERSION,
        dataset_cutoff=DATASET_CUTOFF,
        filters=[],
        metric="COUNT(DISTINCT deals.deal_no)",
        matched_rows=10,
        grouping=[],
        currency_method=None,
        unit=None,
        execution_id="req_test",
    )
    validate_evidence(
        evidence,
        dataset_version=DATASET_VERSION,
        dataset_cutoff=DATASET_CUTOFF,
        matched_rows_mode="positive",
    )

    with pytest.raises(UnverifiedQuery):
        validate_evidence(
            evidence,
            dataset_version=DATASET_VERSION,
            dataset_cutoff=DATASET_CUTOFF,
            matched_rows_mode="zero",
        )
    with pytest.raises(UnverifiedQuery):
        validate_evidence(
            evidence.model_copy(update={"source_tables": ["secret_table"]}),
            dataset_version=DATASET_VERSION,
            dataset_cutoff=DATASET_CUTOFF,
            matched_rows_mode="positive",
        )
    with pytest.raises(UnverifiedQuery):
        validate_evidence(
            evidence.model_copy(update={"dataset_version": "other"}),
            dataset_version=DATASET_VERSION,
            dataset_cutoff=DATASET_CUTOFF,
            matched_rows_mode="positive",
        )
    with pytest.raises(UnverifiedQuery):
        validate_evidence(
            evidence.model_copy(update={"filters": [{"field": "deal_date"}]}),
            dataset_version=DATASET_VERSION,
            dataset_cutoff=DATASET_CUTOFF,
            matched_rows_mode="positive",
        )
    with pytest.raises(UnverifiedQuery):
        validate_evidence(
            evidence.model_copy(update={"source_tables": ["deals", "deals"]}),
            dataset_version=DATASET_VERSION,
            dataset_cutoff=DATASET_CUTOFF,
            matched_rows_mode="positive",
        )


def test_not_found_evidence_requires_zero_rows() -> None:
    from app.validation.evidence_validator import validate_evidence

    evidence = Evidence(
        source_tables=["deals"],
        dataset_version=DATASET_VERSION,
        dataset_cutoff=DATASET_CUTOFF,
        filters=[],
        metric="COUNT(*)",
        matched_rows=0,
        grouping=[],
        currency_method=None,
        unit=None,
        execution_id="req_test",
    )
    validate_evidence(
        evidence,
        dataset_version=DATASET_VERSION,
        dataset_cutoff=DATASET_CUTOFF,
        matched_rows_mode="zero",
    )
    with pytest.raises(UnverifiedQuery):
        validate_evidence(
            evidence.model_copy(update={"matched_rows": 1}),
            dataset_version=DATASET_VERSION,
            dataset_cutoff=DATASET_CUTOFF,
            matched_rows_mode="zero",
        )


def test_found_response_requires_evidence() -> None:
    from app.responses.models import AnswerData, ChatResponse

    with pytest.raises(ValueError):
        ChatResponse(
            status=ResponseStatus.FOUND,
            language="ru",
            answer="x",
            data=AnswerData(metric="deal_count", value=1),
            duration_ms=1,
            request_id="req_test",
        )

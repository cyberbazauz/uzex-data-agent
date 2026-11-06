from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.agent.query_plan import (
    ClarificationInfo,
    ComparisonPeriod,
    ComparisonSpec,
    EntityReference,
    FilterCondition,
    OrderCondition,
    QueryPlan,
)
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
from app.domain.errors import AmbiguousEntity, UnsupportedQuery
from app.validation.plan_validator import PlanValidator


def make_plan(**overrides: object) -> QueryPlan:
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


def validate(plan: QueryPlan) -> QueryPlan:
    return PlanValidator().validate(plan)


def date_filter(start: str, end: str) -> FilterCondition:
    return FilterCondition(
        field=FilterField.DEAL_DATE,
        operator=Operator.BETWEEN,
        value=[start, end],
    )


def entity(
    kind: MatchKind,
    mention: str = "test",
    *,
    role: EntityRole | None = None,
    meaning: RegionMeaning | None = None,
) -> EntityReference:
    return EntityReference(kind=kind, mention=mention, role=role, meaning=meaning)


def compare_spec() -> ComparisonSpec:
    return ComparisonSpec(
        left=ComparisonPeriod(
            label="2024", date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)
        ),
        right=ComparisonPeriod(
            label="2025", date_from=date(2025, 1, 1), date_to=date(2025, 12, 31)
        ),
    )


# ---------------------------------------------------------------------------
# Decision passthrough and basic compatibility
# ---------------------------------------------------------------------------


def test_valid_count_plan_passes() -> None:
    plan = make_plan(filters=[date_filter("2024-01-01", "2024-12-31")])
    assert validate(plan) is plan


def test_unknown_controlled_filter_value_is_unsupported() -> None:
    plan = make_plan(
        filters=[
            FilterCondition(
                field=FilterField.TRADE_TYPE,
                operator=Operator.EQ,
                value="export",
            )
        ]
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_known_contract_type_filter_passes() -> None:
    plan = make_plan(
        filters=[
            FilterCondition(
                field=FilterField.CONTRACT_TYPE,
                operator=Operator.EQ,
                value="Экспортный",
            )
        ]
    )
    assert validate(plan) is plan


def test_clarify_decision_bypasses_execution_validation() -> None:
    plan = make_plan(
        decision=Decision.CLARIFY,
        intent=Intent.COUNT,
        subject=Subject.BROKERS,
        metric=Metric.AMOUNT_SUM_UZS_CONVERTED,
        clarification=ClarificationInfo(code=ClarificationCode.BROKER_SIDE_AMBIGUOUS),
    )
    assert validate(plan) is plan


def test_unsupported_decision_bypasses_execution_validation() -> None:
    plan = make_plan(decision=Decision.UNSUPPORTED, intent=Intent.TOP_N)
    assert validate(plan) is plan


def test_clarify_plan_accepts_general_sentinels() -> None:
    # A model may mark a clarification with the general sentinels.  CLARIFY
    # executes nothing, so this is a valid "please clarify" plan and must not
    # be turned into UNVERIFIED.
    plan = make_plan(
        decision=Decision.CLARIFY,
        intent=Intent.GENERAL,
        subject=Subject.NOT_APPLICABLE,
        metric=Metric.NOT_APPLICABLE,
        clarification=ClarificationInfo(code=ClarificationCode.PERIOD_AMBIGUOUS),
    )
    assert validate(plan) is plan
    assert plan.decision is Decision.CLARIFY


def test_execute_plan_still_rejects_general_sentinels() -> None:
    with pytest.raises(ValidationError):
        make_plan(
            decision=Decision.EXECUTE,
            intent=Intent.GENERAL,
            subject=Subject.NOT_APPLICABLE,
            metric=Metric.NOT_APPLICABLE,
        )


def test_subject_must_match_intent() -> None:
    plan = make_plan(
        intent=Intent.COUNT,
        subject=Subject.CONTRACTS,
        metric=Metric.DEAL_COUNT,
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_metric_must_match_subject() -> None:
    plan = make_plan(
        intent=Intent.SUM_VOLUME,
        subject=Subject.DEALS,
        metric=Metric.AMOUNT_SUM_UZS_CONVERTED,
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_filters_must_be_unique_and_well_typed() -> None:
    condition = date_filter("2024-01-01", "2024-12-31")
    with pytest.raises(ValidationError):
        make_plan(filters=[condition, condition])


def test_duplicate_group_fields_are_rejected() -> None:
    plan = make_plan(group_by=[GroupField.YEAR, GroupField.YEAR])
    with pytest.raises(UnsupportedQuery):
        validate(plan)


# ---------------------------------------------------------------------------
# Lookup intents
# ---------------------------------------------------------------------------


def test_deal_lookup_requires_deal_number() -> None:
    plan = make_plan(
        intent=Intent.DEAL_LOOKUP,
        subject=Subject.DEALS,
        metric=Metric.RECORD_LOOKUP,
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_deal_lookup_accepts_exact_deal_number() -> None:
    plan = make_plan(
        intent=Intent.DEAL_LOOKUP,
        subject=Subject.DEALS,
        metric=Metric.RECORD_LOOKUP,
        filters=[
            FilterCondition(field=FilterField.DEAL_NO, operator=Operator.EQ, value="100001")
        ],
    )
    assert validate(plan) is plan


def test_lookup_rejects_range_operators() -> None:
    plan = make_plan(
        intent=Intent.DEAL_LOOKUP,
        subject=Subject.DEALS,
        metric=Metric.RECORD_LOOKUP,
        filters=[
            FilterCondition(field=FilterField.DEAL_NO, operator=Operator.GTE, value="100001")
        ],
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_contract_lookup_accepts_contract_number() -> None:
    plan = make_plan(
        intent=Intent.CONTRACT_LOOKUP,
        subject=Subject.CONTRACTS,
        metric=Metric.RECORD_LOOKUP,
        filters=[
            FilterCondition(
                field=FilterField.CONTRACT_NO, operator=Operator.EQ, value="C-1"
            )
        ],
    )
    assert validate(plan) is plan


def test_participant_lookup_accepts_unresolved_name_filter() -> None:
    plan = make_plan(
        intent=Intent.PARTICIPANT_LOOKUP,
        subject=Subject.PARTICIPANTS,
        metric=Metric.RECORD_LOOKUP,
        filters=[
            FilterCondition(
                field=FilterField.SELLER_INN,
                operator=Operator.EQ,
                value="Some Company",
            )
        ],
    )
    assert validate(plan) is plan


def test_broker_lookup_accepts_broker_entity() -> None:
    plan = make_plan(
        intent=Intent.BROKER_LOOKUP,
        subject=Subject.BROKERS,
        metric=Metric.RECORD_LOOKUP,
        entity_candidates=[entity(MatchKind.BROKER, "BK001")],
    )
    assert validate(plan) is plan


def test_lookup_forbids_grouping_and_ordering() -> None:
    plan = make_plan(
        intent=Intent.DEAL_LOOKUP,
        subject=Subject.DEALS,
        metric=Metric.RECORD_LOOKUP,
        filters=[
            FilterCondition(field=FilterField.DEAL_NO, operator=Operator.EQ, value="100001")
        ],
        group_by=[GroupField.YEAR],
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_lookup_forbids_large_limit() -> None:
    plan = make_plan(
        intent=Intent.CONTRACT_LOOKUP,
        subject=Subject.CONTRACTS,
        metric=Metric.RECORD_LOOKUP,
        filters=[
            FilterCondition(
                field=FilterField.CONTRACT_NO, operator=Operator.EQ, value="C-1"
            )
        ],
        limit=20,
    )
    assert validate(plan) is plan

    with pytest.raises(ValidationError):
        make_plan(
            intent=Intent.CONTRACT_LOOKUP,
            subject=Subject.CONTRACTS,
            metric=Metric.RECORD_LOOKUP,
            limit=21,
        )


# ---------------------------------------------------------------------------
# Shape rules for aggregate intents
# ---------------------------------------------------------------------------


def test_filter_list_requires_record_lookup() -> None:
    plan = make_plan(
        intent=Intent.FILTER_LIST,
        subject=Subject.DEALS,
        metric=Metric.DEAL_COUNT,
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_filter_list_accepts_record_lookup_and_date_order() -> None:
    plan = make_plan(
        intent=Intent.FILTER_LIST,
        subject=Subject.DEALS,
        metric=Metric.RECORD_LOOKUP,
        order_by=[OrderCondition(field=OrderField.DEAL_DATE, direction=Direction.DESC)],
        filters=[date_filter("2024-01-01", "2024-12-31")],
    )
    assert validate(plan) is plan


def test_filter_list_rejects_other_order_fields() -> None:
    plan = make_plan(
        intent=Intent.FILTER_LIST,
        subject=Subject.DEALS,
        metric=Metric.RECORD_LOOKUP,
        order_by=[OrderCondition(field=OrderField.GROUP_KEY)],
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_min_max_forbids_additional_grouping() -> None:
    plan = make_plan(
        intent=Intent.MIN_MAX,
        metric=Metric.AMOUNT_MAX,
        group_by=[GroupField.YEAR],
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_trend_requires_one_time_group() -> None:
    invalid = make_plan(
        intent=Intent.TREND,
        metric=Metric.DEAL_COUNT,
        group_by=[GroupField.CURRENCY],
    )
    with pytest.raises(UnsupportedQuery):
        validate(invalid)

    valid = make_plan(
        intent=Intent.TREND,
        metric=Metric.DEAL_COUNT,
        group_by=[GroupField.MONTH],
    )
    assert validate(valid) is valid


def test_trend_rejects_two_time_groups() -> None:
    plan = make_plan(
        intent=Intent.TREND,
        metric=Metric.DEAL_COUNT,
        group_by=[GroupField.YEAR, GroupField.MONTH],
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_top_n_deals_requires_grouping() -> None:
    plan = make_plan(intent=Intent.TOP_N, metric=Metric.DEAL_COUNT)
    with pytest.raises(UnsupportedQuery):
        validate(plan)

    grouped = make_plan(
        intent=Intent.TOP_N,
        metric=Metric.DEAL_COUNT,
        group_by=[GroupField.CONTRACT_NO],
        limit=10,
    )
    assert validate(grouped) is grouped


def test_top_n_only_orders_by_metric() -> None:
    plan = make_plan(
        intent=Intent.TOP_N,
        metric=Metric.DEAL_COUNT,
        group_by=[GroupField.CONTRACT_NO],
        order_by=[OrderCondition(field=OrderField.GROUP_KEY)],
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)

    valid = make_plan(
        intent=Intent.TOP_N,
        metric=Metric.DEAL_COUNT,
        group_by=[GroupField.CONTRACT_NO],
        order_by=[OrderCondition(field=OrderField.METRIC, direction=Direction.DESC)],
    )
    assert validate(valid) is valid


def test_top_participants_requires_explicit_side() -> None:
    plan = make_plan(
        intent=Intent.TOP_N,
        subject=Subject.PARTICIPANTS,
        metric=Metric.DEAL_COUNT,
        limit=10,
    )
    with pytest.raises(AmbiguousEntity) as exc:
        validate(plan)
    assert exc.value.clarification_code is ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS

    grouped = make_plan(
        intent=Intent.TOP_N,
        subject=Subject.PARTICIPANTS,
        metric=Metric.DEAL_COUNT,
        group_by=[GroupField.SELLER_INN],
        limit=10,
    )
    assert validate(grouped) is grouped


def test_top_brokers_requires_explicit_side() -> None:
    plan = make_plan(
        intent=Intent.TOP_N,
        subject=Subject.BROKERS,
        metric=Metric.DEAL_COUNT,
        limit=10,
    )
    with pytest.raises(AmbiguousEntity) as exc:
        validate(plan)
    assert exc.value.clarification_code is ClarificationCode.BROKER_SIDE_AMBIGUOUS

    grouped = make_plan(
        intent=Intent.TOP_N,
        subject=Subject.BROKERS,
        metric=Metric.DEAL_COUNT,
        group_by=[GroupField.BUYER_BROKER_CODE],
        limit=10,
    )
    assert validate(grouped) is grouped


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


def test_compare_rejects_deal_date_filters() -> None:
    plan = make_plan(
        intent=Intent.COMPARE,
        metric=Metric.DEAL_COUNT,
        comparison=compare_spec(),
        filters=[date_filter("2024-01-01", "2024-12-31")],
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_compare_rejects_time_grouping() -> None:
    plan = make_plan(
        intent=Intent.COMPARE,
        metric=Metric.DEAL_COUNT,
        comparison=compare_spec(),
        group_by=[GroupField.YEAR],
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_compare_allows_currency_grouping() -> None:
    plan = make_plan(
        intent=Intent.COMPARE,
        metric=Metric.DEAL_COUNT,
        comparison=compare_spec(),
        group_by=[GroupField.CURRENCY],
    )
    assert validate(plan) is plan


def test_compare_periods_must_not_overlap() -> None:
    with pytest.raises(ValidationError):
        make_plan(
            intent=Intent.COMPARE,
            metric=Metric.DEAL_COUNT,
            comparison=ComparisonSpec(
                left=ComparisonPeriod(
                    label="a", date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)
                ),
                right=ComparisonPeriod(
                    label="b", date_from=date(2024, 6, 1), date_to=date(2025, 1, 31)
                ),
            ),
        )


# ---------------------------------------------------------------------------
# Currency policy
# ---------------------------------------------------------------------------


def test_native_amount_sum_requires_native_currency_mode() -> None:
    invalid = make_plan(
        intent=Intent.SUM_AMOUNT,
        metric=Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
        currency_mode=CurrencyMode.NOT_APPLICABLE,
    )
    with pytest.raises(UnsupportedQuery):
        validate(invalid)

    valid = make_plan(
        intent=Intent.SUM_AMOUNT,
        metric=Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
        currency_mode=CurrencyMode.NATIVE_BY_CURRENCY,
    )
    assert validate(valid) is valid


def test_native_amount_sum_rejects_converted_mode() -> None:
    plan = make_plan(
        intent=Intent.SUM_AMOUNT,
        metric=Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
        currency_mode=CurrencyMode.UZS_CONVERTED,
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_converted_amount_sum_requires_converted_mode() -> None:
    invalid = make_plan(
        intent=Intent.SUM_AMOUNT,
        metric=Metric.AMOUNT_SUM_UZS_CONVERTED,
        currency_mode=CurrencyMode.NOT_APPLICABLE,
    )
    with pytest.raises(UnsupportedQuery):
        validate(invalid)

    valid = make_plan(
        intent=Intent.SUM_AMOUNT,
        metric=Metric.AMOUNT_SUM_UZS_CONVERTED,
        currency_mode=CurrencyMode.UZS_CONVERTED,
    )
    assert validate(valid) is valid


def test_amount_min_max_is_native_or_not_applicable() -> None:
    invalid = make_plan(
        intent=Intent.MIN_MAX,
        metric=Metric.AMOUNT_MIN,
        currency_mode=CurrencyMode.USD_NATIVE,
    )
    with pytest.raises(UnsupportedQuery):
        validate(invalid)

    for mode in (CurrencyMode.NOT_APPLICABLE, CurrencyMode.NATIVE_BY_CURRENCY):
        plan = make_plan(
            intent=Intent.MIN_MAX, metric=Metric.AMOUNT_MIN, currency_mode=mode
        )
        assert validate(plan) is plan


def test_count_rejects_money_currency_mode() -> None:
    plan = make_plan(currency_mode=CurrencyMode.NATIVE_BY_CURRENCY)
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_volume_rejects_currency_mode() -> None:
    plan = make_plan(
        intent=Intent.SUM_VOLUME,
        metric=Metric.VOLUME_SUM_BY_UNIT,
        currency_mode=CurrencyMode.UZS_CONVERTED,
    )
    with pytest.raises(UnsupportedQuery):
        validate(plan)


def test_unit_price_accepts_native_or_not_applicable() -> None:
    for mode in (CurrencyMode.NOT_APPLICABLE, CurrencyMode.NATIVE_BY_CURRENCY):
        plan = make_plan(
            intent=Intent.AVERAGE,
            metric=Metric.UNIT_PRICE_WEIGHTED_AVERAGE,
            currency_mode=mode,
        )
        assert validate(plan) is plan

    invalid = make_plan(
        intent=Intent.AVERAGE,
        metric=Metric.UNIT_PRICE_WEIGHTED_AVERAGE,
        currency_mode=CurrencyMode.UZS_CONVERTED,
    )
    with pytest.raises(UnsupportedQuery):
        validate(invalid)


# ---------------------------------------------------------------------------
# Entity role / region meaning policy
# ---------------------------------------------------------------------------


def test_participant_entity_requires_role_outside_lookup() -> None:
    plan = make_plan(entity_candidates=[entity(MatchKind.PARTICIPANT, "Acme")])
    with pytest.raises(AmbiguousEntity) as exc:
        validate(plan)
    assert exc.value.clarification_code is ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS


def test_participant_entity_any_role_is_explicit() -> None:
    plan = make_plan(
        entity_candidates=[
            entity(MatchKind.PARTICIPANT, "Acme", role=EntityRole.ANY)
        ]
    )
    assert validate(plan) is plan


def test_participant_entity_role_is_not_inferred_from_filter() -> None:
    plan = make_plan(
        entity_candidates=[entity(MatchKind.PARTICIPANT, "Acme")],
        filters=[
            FilterCondition(
                field=FilterField.SELLER_INN, operator=Operator.EQ, value="Acme"
            )
        ],
    )
    with pytest.raises(AmbiguousEntity):
        validate(plan)


def test_broker_entity_requires_side_outside_lookup() -> None:
    plan = make_plan(entity_candidates=[entity(MatchKind.BROKER, "BK001")])
    with pytest.raises(AmbiguousEntity) as exc:
        validate(plan)
    assert exc.value.clarification_code is ClarificationCode.BROKER_SIDE_AMBIGUOUS


def test_region_requires_meaning() -> None:
    plan = make_plan(entity_candidates=[entity(MatchKind.REGION, "Ташкент")])
    with pytest.raises(AmbiguousEntity) as exc:
        validate(plan)
    assert exc.value.clarification_code is ClarificationCode.REGION_MEANING_AMBIGUOUS


def test_warehouse_region_needs_no_side() -> None:
    plan = make_plan(
        entity_candidates=[
            entity(
                MatchKind.REGION,
                "Ташкент",
                meaning=RegionMeaning.WAREHOUSE,
            )
        ]
    )
    assert validate(plan) is plan


def test_company_region_requires_seller_or_buyer() -> None:
    plan = make_plan(
        entity_candidates=[
            entity(MatchKind.REGION, "Ташкент", meaning=RegionMeaning.COMPANY)
        ]
    )
    with pytest.raises(AmbiguousEntity) as exc:
        validate(plan)
    assert exc.value.clarification_code is ClarificationCode.REGION_MEANING_AMBIGUOUS

    valid = make_plan(
        entity_candidates=[
            entity(
                MatchKind.REGION,
                "Ташкент",
                meaning=RegionMeaning.COMPANY,
                role=EntityRole.BUYER,
            )
        ]
    )
    assert validate(valid) is valid


def test_participant_lookup_allows_missing_role() -> None:
    plan = make_plan(
        intent=Intent.PARTICIPANT_LOOKUP,
        subject=Subject.PARTICIPANTS,
        metric=Metric.RECORD_LOOKUP,
        entity_candidates=[entity(MatchKind.PARTICIPANT, "Acme")],
    )
    assert validate(plan) is plan


def test_dataset_bounds_do_not_change_query_semantics() -> None:
    plan = make_plan(filters=[date_filter("2030-01-01", "2030-12-31")])
    validated = PlanValidator().validate(plan, dataset_bounds=(date(2022, 1, 1), date(2026, 10, 30)))
    assert validated is plan

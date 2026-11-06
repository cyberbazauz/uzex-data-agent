from __future__ import annotations

from datetime import date

from app.agent.query_plan import EntityReference, QueryPlan
from app.domain.enums import (
    CONTRACT_FORMS,
    CONTRACT_TYPES,
    CURRENCIES,
    STATUSES,
    TRADE_TYPES,
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
    OrderField,
    RegionMeaning,
    Subject,
    INTENT_SUBJECT_METRICS,
)
from app.domain.errors import AmbiguousEntity, UnsupportedQuery

CONTROLLED_FILTER_VALUES: dict[FilterField, tuple[str, ...]] = {
    FilterField.TRADE_TYPE: TRADE_TYPES,
    FilterField.CONTRACT_TYPE: CONTRACT_TYPES,
    FilterField.CONTRACT_FORM: CONTRACT_FORMS,
    FilterField.STATUS: STATUSES,
    FilterField.CURRENCY: CURRENCIES,
}

MONEY_METRICS = frozenset(
    {
        Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
        Metric.AMOUNT_SUM_UZS_CONVERTED,
        Metric.AMOUNT_AVERAGE_UZS_CONVERTED,
        Metric.AMOUNT_MIN,
        Metric.AMOUNT_MAX,
    }
)

LOOKUP_INTENTS = frozenset(
    {
        Intent.DEAL_LOOKUP,
        Intent.CONTRACT_LOOKUP,
        Intent.PARTICIPANT_LOOKUP,
        Intent.BROKER_LOOKUP,
    }
)

SIDE_GROUPS = frozenset(
    {
        GroupField.SELLER_INN,
        GroupField.BUYER_INN,
        GroupField.SELLER_BROKER_CODE,
        GroupField.BUYER_BROKER_CODE,
    }
)


class PlanValidator:
    """Server-side policy validation that runs before any SQL is built.

    Pydantic already rejects unknown keys and enum values.  This validator
    enforces semantic compatibility between intent, subject, metric, filters,
    grouping, currency mode and entity roles.
    """

    def validate(
        self,
        plan: QueryPlan,
        dataset_bounds: tuple[date, date] | None = None,
    ) -> QueryPlan:
        if plan.decision is Decision.GENERAL:
            # General AI Mode never enters the SQL pipeline.  Reaching the
            # validator with such a plan is a programming error, not a query.
            raise UnsupportedQuery("GENERAL plans are not executable")
        if plan.decision is not Decision.EXECUTE:
            return plan

        self._validate_intent_subject_metric(plan)
        self._validate_filters(plan)
        self._validate_shape(plan)
        self._validate_currency(plan)
        self._validate_entities(plan)
        self._validate_dataset_bounds(plan, dataset_bounds)
        return plan

    # -- intent / subject / metric --------------------------------------
    def _validate_intent_subject_metric(self, plan: QueryPlan) -> None:
        allowed_subjects = INTENT_SUBJECT_METRICS.get(plan.intent)
        if not allowed_subjects:
            raise UnsupportedQuery(f"intent {plan.intent.value} is not supported")
        allowed_metrics = allowed_subjects.get(plan.subject)
        if allowed_metrics is None:
            raise UnsupportedQuery(
                f"subject {plan.subject.value} is not supported for intent "
                f"{plan.intent.value}"
            )
        if plan.metric not in allowed_metrics:
            raise UnsupportedQuery(
                f"metric {plan.metric.value} is not allowed for "
                f"{plan.intent.value}/{plan.subject.value}"
            )

    # -- filters ---------------------------------------------------------
    def _validate_filters(self, plan: QueryPlan) -> None:
        for condition in plan.filters:
            if condition.field is FilterField.DEAL_DATE:
                continue
            if condition.field in (FilterField.PRODUCT_NAME, FilterField.DEAL_PRODUCT_NAME):
                allowed = (Operator.EQ, Operator.IN, Operator.CONTAINS)
            else:
                allowed = (Operator.EQ, Operator.IN)
            if condition.operator not in allowed:
                raise UnsupportedQuery(
                    f"operator {condition.operator.value} is not allowed for "
                    f"field {condition.field.value}"
                )
            controlled = CONTROLLED_FILTER_VALUES.get(condition.field)
            if controlled is not None and condition.operator in (
                Operator.EQ,
                Operator.IN,
            ):
                raw_values = (
                    condition.value
                    if isinstance(condition.value, list)
                    else [condition.value]
                )
                for value in raw_values:
                    if not isinstance(value, str) or value not in controlled:
                        # A value outside the reference dictionary is a
                        # planner mapping error, not an empty result.  Never
                        # turn it into a misleading NOT_FOUND.
                        raise UnsupportedQuery(
                            f"field {condition.field.value} accepts only known "
                            "reference values"
                        )

        if plan.intent is Intent.COMPARE:
            if any(
                condition.field is FilterField.DEAL_DATE
                for condition in plan.filters
            ):
                raise UnsupportedQuery(
                    "compare periods must be expressed only in comparison, "
                    "not as deal_date filters"
                )
        if plan.intent in LOOKUP_INTENTS:
            lookup_fields = {
                Intent.DEAL_LOOKUP: FilterField.DEAL_NO,
                Intent.CONTRACT_LOOKUP: FilterField.CONTRACT_NO,
                Intent.PARTICIPANT_LOOKUP: (FilterField.SELLER_INN, FilterField.BUYER_INN),
                Intent.BROKER_LOOKUP: (
                    FilterField.SELLER_BROKER_CODE,
                    FilterField.BUYER_BROKER_CODE,
                ),
            }[plan.intent]
            required = (
                (lookup_fields,) if isinstance(lookup_fields, FilterField) else lookup_fields
            )
            for condition in plan.filters:
                if condition.field in required and condition.operator not in (
                    Operator.EQ,
                    Operator.IN,
                ):
                    raise UnsupportedQuery(
                        f"{plan.intent.value} supports only exact or in lookup, "
                        f"not operator {condition.operator.value}"
                    )
            has_filter = any(
                condition.field in required for condition in plan.filters
            )
            has_entity = any(
                entity.kind
                in (
                    MatchKind.PARTICIPANT
                    if plan.intent is Intent.PARTICIPANT_LOOKUP
                    else MatchKind.BROKER
                )
                for entity in plan.entity_candidates
            ) if plan.intent in (Intent.PARTICIPANT_LOOKUP, Intent.BROKER_LOOKUP) else False
            if not has_filter and not has_entity:
                raise UnsupportedQuery(
                    f"{plan.intent.value} requires an explicit identifier or entity"
                )

        if plan.intent is Intent.FILTER_LIST and plan.metric is Metric.DEAL_COUNT:
            # A list request is supported, but the handler returns rows.  A
            # count-only list would hide the hard limit contract, so require
            # record_lookup for filter_list.
            raise UnsupportedQuery("filter_list requires metric=record_lookup")

    # -- shape -----------------------------------------------------------
    def _validate_shape(self, plan: QueryPlan) -> None:
        if len(set(plan.group_by)) != len(plan.group_by):
            raise UnsupportedQuery("group_by must not contain duplicate fields")

        if plan.intent in LOOKUP_INTENTS:
            if plan.group_by or plan.order_by:
                raise UnsupportedQuery(
                    f"{plan.intent.value} does not allow grouping or ordering fields"
                )
            if plan.limit is not None and plan.limit > 20:
                raise UnsupportedQuery("lookup limit must be at most 20")
            return

        if plan.intent is Intent.MIN_MAX:
            if plan.group_by:
                raise UnsupportedQuery(
                    "min_max groups by contract currency automatically; "
                    "additional grouping is not supported"
                )
            if plan.order_by:
                raise UnsupportedQuery("min_max does not use order_by")
            return

        if plan.intent is Intent.TREND:
            time_groups = [
                field
                for field in plan.group_by
                if field in (GroupField.YEAR, GroupField.MONTH)
            ]
            if len(time_groups) != 1:
                raise UnsupportedQuery(
                    "trend requires exactly one of year or month in group_by"
                )
            if plan.order_by:
                raise UnsupportedQuery("trend orders periods chronologically")
            return

        if plan.intent is Intent.TOP_N:
            if plan.subject is Subject.DEALS and not plan.group_by:
                raise UnsupportedQuery(
                    "top_n for deals requires an explicit grouping field"
                )
            if plan.order_by:
                if any(order.field is not OrderField.METRIC for order in plan.order_by):
                    raise UnsupportedQuery("top_n supports ordering by metric only")
            return

        if plan.intent is Intent.FILTER_LIST:
            if plan.group_by:
                raise UnsupportedQuery("filter_list does not support group_by")
            if plan.order_by:
                allowed = {
                    OrderField.DEAL_DATE,
                    OrderField.DEAL_NO,
                    OrderField.DEAL_AMOUNT,
                }
                if any(order.field not in allowed for order in plan.order_by):
                    raise UnsupportedQuery(
                        "filter_list orders only by deal_date, deal_no or deal_amount"
                    )
            return

        # count, sum_amount, sum_volume, average, compare
        if plan.order_by:
            raise UnsupportedQuery(
                f"{plan.intent.value} does not use order_by; results are grouped "
                "deterministically"
            )
        if plan.intent is Intent.COMPARE and plan.group_by:
            # Grouping is allowed, but a time grouping would duplicate the two
            # explicit comparison periods.
            if any(
                field in (GroupField.YEAR, GroupField.MONTH, GroupField.DEAL_DATE)
                for field in plan.group_by
            ):
                raise UnsupportedQuery(
                    "compare does not accept an additional time grouping field"
                )

    # -- currency --------------------------------------------------------
    def _validate_currency(self, plan: QueryPlan) -> None:
        mode = plan.currency_mode
        metric = plan.metric

        if metric is Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY:
            if mode not in (
                CurrencyMode.NATIVE_BY_CURRENCY,
                CurrencyMode.USD_NATIVE,
                CurrencyMode.UZS_NATIVE,
            ):
                raise UnsupportedQuery(
                    "amount_sum_native_by_currency requires native_by_currency, "
                    "usd_native or uzs_native"
                )
            return

        if metric in (
            Metric.AMOUNT_SUM_UZS_CONVERTED,
            Metric.AMOUNT_AVERAGE_UZS_CONVERTED,
        ):
            if mode is not CurrencyMode.UZS_CONVERTED:
                raise UnsupportedQuery(
                    f"{metric.value} requires currency_mode=uzs_converted"
                )
            return

        if metric in (
            Metric.AMOUNT_MIN,
            Metric.AMOUNT_MAX,
            Metric.UNIT_PRICE_SIMPLE_AVERAGE,
            Metric.UNIT_PRICE_WEIGHTED_AVERAGE,
        ):
            if mode not in (CurrencyMode.NATIVE_BY_CURRENCY, CurrencyMode.NOT_APPLICABLE):
                raise UnsupportedQuery(
                    f"{metric.value} is reported per contract currency and cannot "
                    "use usd_native, uzs_native or uzs_converted"
                )
            return

        if metric is not Metric.DEAL_COUNT:
            # Volume metrics are independent of currency.
            pass
        if mode is not CurrencyMode.NOT_APPLICABLE:
            raise UnsupportedQuery(
                f"{metric.value} is not a money metric; currency_mode must be "
                "not_applicable"
            )

    # -- entities --------------------------------------------------------
    def _validate_entities(self, plan: QueryPlan) -> None:
        for entity in plan.entity_candidates:
            if entity.kind is MatchKind.REGION:
                if entity.meaning is None:
                    raise AmbiguousEntity(
                        ClarificationCode.REGION_MEANING_AMBIGUOUS,
                        "region must be clarified as warehouse region or company region",
                    )
                if (
                    entity.meaning is RegionMeaning.COMPANY
                    and entity.role not in (EntityRole.SELLER, EntityRole.BUYER, EntityRole.ANY)
                ):
                    raise AmbiguousEntity(
                        ClarificationCode.REGION_MEANING_AMBIGUOUS,
                        "company region must be clarified as seller or buyer side",
                    )
            elif entity.kind is MatchKind.PARTICIPANT:
                if entity.role is None and plan.intent is not Intent.PARTICIPANT_LOOKUP:
                    raise AmbiguousEntity(
                        ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS,
                        "participant must be clarified as seller or buyer",
                    )
            elif entity.kind is MatchKind.BROKER:
                if entity.role is None and plan.intent is not Intent.BROKER_LOOKUP:
                    raise AmbiguousEntity(
                        ClarificationCode.BROKER_SIDE_AMBIGUOUS,
                        "broker must be clarified as seller broker or buyer broker",
                    )
            elif entity.kind is MatchKind.PRODUCT:
                continue

        if plan.intent is Intent.TOP_N:
            if plan.subject is Subject.PARTICIPANTS:
                group_side = {
                    field for field in plan.group_by if field in SIDE_GROUPS
                }
                entity_role = {
                    entity.role
                    for entity in plan.entity_candidates
                    if entity.kind is MatchKind.PARTICIPANT
                }
                if not group_side and not (
                    EntityRole.SELLER in entity_role or EntityRole.BUYER in entity_role
                ):
                    raise AmbiguousEntity(
                        ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS,
                        "top participants requires an explicit seller or buyer side",
                    )
            elif plan.subject is Subject.BROKERS:
                group_side = {
                    field for field in plan.group_by if field in SIDE_GROUPS
                }
                entity_role = {
                    entity.role
                    for entity in plan.entity_candidates
                    if entity.kind is MatchKind.BROKER
                }
                if not group_side and not (
                    EntityRole.BROKER_SELLER in entity_role
                    or EntityRole.BROKER_BUYER in entity_role
                ):
                    raise AmbiguousEntity(
                        ClarificationCode.BROKER_SIDE_AMBIGUOUS,
                        "top brokers requires an explicit seller or buyer side",
                    )

    # -- dataset bounds --------------------------------------------------
    def _validate_dataset_bounds(
        self, plan: QueryPlan, dataset_bounds: tuple[date, date] | None
    ) -> None:
        if dataset_bounds is None:
            return
        minimum, maximum = dataset_bounds
        for condition in plan.filters:
            if condition.field is not FilterField.DEAL_DATE:
                continue
            if condition.operator.value == "between" and isinstance(condition.value, list):
                start = date.fromisoformat(str(condition.value[0]))
                end = date.fromisoformat(str(condition.value[1]))
                if end < minimum or start > maximum:
                    # The query is valid; it simply cannot match.  The handler
                    # must return NOT_FOUND with matched_rows=0, not invent 0.
                    continue
            # Single-date filters outside the data range are also valid
            # queries that return no rows.
        return

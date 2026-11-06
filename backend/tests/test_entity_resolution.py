from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.agent.query_plan import (
    ClarificationInfo,
    ComparisonPeriod,
    ComparisonSpec,
    EntityReference,
    FilterCondition,
    QueryPlan,
)
from app.database.models import Participant
from app.domain.enums import (
    ClarificationCode,
    CurrencyMode,
    Decision,
    EntityRole,
    FilterField,
    Intent,
    Language,
    MatchKind,
    Metric,
    Operator,
    RegionMeaning,
    Script,
    Subject,
)
from app.domain.errors import AmbiguousEntity, EntityNotFound
from app.entities.brokers import resolve_broker
from app.entities.participants import resolve_participant
from app.entities.products import resolve_product
from app.entities.regions import resolve_region, resolve_region_id
from app.entities.resolver import EntityResolver
from app.validation.plan_validator import PlanValidator


UNIQUE_PARTICIPANT_NAME = '"Diyi Un" XK'
UNIQUE_PARTICIPANT_INN = "937772675"


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


def resolve(conn: Connection, plan: QueryPlan):
    validated = PlanValidator().validate(plan)
    return EntityResolver(conn).resolve(validated)


# ---------------------------------------------------------------------------
# Participant resolution
# ---------------------------------------------------------------------------


def test_participant_exact_inn(reader_conn: Connection) -> None:
    match = resolve_participant(reader_conn, UNIQUE_PARTICIPANT_INN)
    assert match.inn == UNIQUE_PARTICIPANT_INN
    assert match.matched_by == "inn"


def test_participant_exact_normalized_name(reader_conn: Connection) -> None:
    match = resolve_participant(reader_conn, UNIQUE_PARTICIPANT_NAME)
    assert match.inn == UNIQUE_PARTICIPANT_INN
    assert match.matched_by == "exact_normalized_name"


def test_participant_exact_name_is_case_and_punctuation_insensitive(
    reader_conn: Connection,
) -> None:
    match = resolve_participant(reader_conn, "  diyi   un xk ")
    assert match.inn == UNIQUE_PARTICIPANT_INN


def test_participant_ambiguity_is_not_auto_resolved(reader_conn: Connection) -> None:
    with pytest.raises(AmbiguousEntity) as exc:
        resolve_participant(reader_conn, "sujo plus mchj")
    assert exc.value.clarification_code is ClarificationCode.PARTICIPANT_AMBIGUOUS
    assert len(exc.value.options) == 2
    assert all("possible" not in option for option in exc.value.options)


def test_participant_fuzzy_candidates_are_suggestions_only(
    reader_conn: Connection,
) -> None:
    with pytest.raises(AmbiguousEntity) as exc:
        resolve_participant(reader_conn, "sujo pluz")
    assert exc.value.clarification_code is ClarificationCode.PARTICIPANT_AMBIGUOUS
    assert exc.value.options
    assert all("not confirmed" in option for option in exc.value.options)


def test_unknown_participant_name(reader_conn: Connection) -> None:
    with pytest.raises(EntityNotFound):
        resolve_participant(reader_conn, "No Such Company Xyz")


def test_unknown_participant_inn(reader_conn: Connection) -> None:
    with pytest.raises(EntityNotFound) as exc:
        resolve_participant(reader_conn, "000000000")
    assert exc.value.kind == "participant"


def test_participant_name_is_not_silently_chosen_from_fuzzy_match(
    reader_conn: Connection,
) -> None:
    # A near-match must never be returned as if it were exact.
    with pytest.raises(AmbiguousEntity):
        resolve_participant(reader_conn, "Diyi Un X")


# ---------------------------------------------------------------------------
# Broker resolution
# ---------------------------------------------------------------------------


def test_broker_exact_code(reader_conn: Connection) -> None:
    match = resolve_broker(reader_conn, "BK002")
    assert match.broker_code == "BK002"
    assert match.name == "KAHIR BROKER MCHJ"
    assert match.matched_by == "broker_code"


def test_broker_exact_normalized_name(reader_conn: Connection) -> None:
    match = resolve_broker(reader_conn, "kahir broker mchj")
    assert match.broker_code == "BK002"
    assert match.matched_by == "exact_normalized_name"


def test_unknown_broker_code(reader_conn: Connection) -> None:
    with pytest.raises(EntityNotFound):
        resolve_broker(reader_conn, "BK999")


def test_broker_fuzzy_candidates_require_confirmation(reader_conn: Connection) -> None:
    with pytest.raises(AmbiguousEntity) as exc:
        resolve_broker(reader_conn, "kahir brokr")
    assert exc.value.clarification_code is ClarificationCode.BROKER_AMBIGUOUS
    assert exc.value.options
    assert all("not confirmed" in option for option in exc.value.options)


# ---------------------------------------------------------------------------
# Product resolution
# ---------------------------------------------------------------------------


def test_product_exact_base_name_reports_contract_count(
    reader_conn: Connection,
) -> None:
    match = resolve_product(reader_conn, "Карбамид")
    assert match.matched_by == "product_name"
    assert match.label == "Карбамид"
    assert match.contract_count == 111


def test_product_normalized_ambiguity_is_not_auto_resolved(
    reader_conn: Connection,
) -> None:
    with pytest.raises(AmbiguousEntity) as exc:
        resolve_product(reader_conn, "карбамид")
    assert exc.value.clarification_code is ClarificationCode.PRODUCT_AMBIGUOUS
    assert len(exc.value.options) == 2


def test_product_exact_deal_label_reports_contract_count(
    reader_conn: Connection,
) -> None:
    match = resolve_product(reader_conn, "Карбамид Марка Б")
    assert match.matched_by == "deal_product_name"
    assert match.contract_count == 110


def test_product_hs_code_is_exact(reader_conn: Connection) -> None:
    match = resolve_product(reader_conn, "520100")
    assert match.matched_by == "hs_code"
    assert match.id == "520100"
    assert match.contract_count == 1383


def test_unknown_hs_code(reader_conn: Connection) -> None:
    with pytest.raises(EntityNotFound):
        resolve_product(reader_conn, "999999")


def test_product_fuzzy_candidates_require_confirmation(
    reader_conn: Connection,
) -> None:
    with pytest.raises(AmbiguousEntity) as exc:
        resolve_product(reader_conn, "карбамид марка")
    assert exc.value.clarification_code is ClarificationCode.PRODUCT_AMBIGUOUS
    assert exc.value.options
    assert all("not confirmed" in option for option in exc.value.options)


def test_unknown_product(reader_conn: Connection) -> None:
    with pytest.raises(EntityNotFound):
        resolve_product(reader_conn, "No Such Product Xyz")


# ---------------------------------------------------------------------------
# Region resolution
# ---------------------------------------------------------------------------


def test_region_russian_alias(reader_conn: Connection) -> None:
    match = resolve_region(reader_conn, "Ташкентская область", language="ru")
    assert match.region_id == 13
    assert match.label == "Ташкентская область"


def test_region_latin_alias(reader_conn: Connection) -> None:
    match = resolve_region(reader_conn, "Toshkent shahri", language="uz_latn")
    assert match.region_id == 12
    assert match.label == "Toshkent shahri"


def test_region_cyrillic_alias(reader_conn: Connection) -> None:
    match = resolve_region(reader_conn, "Тошкент шаҳри", language="uz_cyrl")
    assert match.region_id == 12


def test_region_numeric_id(reader_conn: Connection) -> None:
    match = resolve_region(reader_conn, "12", language="ru")
    assert match.region_id == 12
    assert match.matched_by == "region_id"


def test_region_fuzzy_requires_confirmation(reader_conn: Connection) -> None:
    with pytest.raises(AmbiguousEntity) as exc:
        resolve_region(reader_conn, "Ташкент", language="ru")
    assert exc.value.clarification_code is ClarificationCode.REGION_AMBIGUOUS
    assert exc.value.options


def test_unknown_region(reader_conn: Connection) -> None:
    with pytest.raises(EntityNotFound):
        resolve_region(reader_conn, "Atlantis", language="ru")


def test_resolve_region_id_from_name(reader_conn: Connection) -> None:
    assert resolve_region_id(reader_conn, "Бухарская область", language="ru") == 2


# ---------------------------------------------------------------------------
# EntityResolver: canonical filters and entities
# ---------------------------------------------------------------------------


def test_resolver_canonicalizes_participant_filter(reader_conn: Connection) -> None:
    plan = make_plan(
        filters=[
            FilterCondition(
                field=FilterField.SELLER_INN,
                operator=Operator.EQ,
                value=UNIQUE_PARTICIPANT_NAME,
            )
        ]
    )
    execution = resolve(reader_conn, plan)
    assert len(execution.filters) == 1
    assert execution.filters[0].value == UNIQUE_PARTICIPANT_INN
    assert execution.entities == []


def test_resolver_canonicalizes_broker_name_filter(reader_conn: Connection) -> None:
    plan = make_plan(
        filters=[
            FilterCondition(
                field=FilterField.SELLER_BROKER_CODE,
                operator=Operator.EQ,
                value="kahir broker mchj",
            )
        ]
    )
    execution = resolve(reader_conn, plan)
    assert execution.filters[0].value == "BK002"


def test_resolver_canonicalizes_region_name_filter(reader_conn: Connection) -> None:
    plan = make_plan(
        filters=[
            FilterCondition(
                field=FilterField.WAREHOUSE_REGION,
                operator=Operator.EQ,
                value="Ташкентская область",
            )
        ]
    )
    execution = resolve(reader_conn, plan)
    assert execution.filters[0].value == 13


def test_resolver_canonicalizes_product_filter(reader_conn: Connection) -> None:
    plan = make_plan(
        filters=[
            FilterCondition(
                field=FilterField.PRODUCT_NAME,
                operator=Operator.EQ,
                value="Карбамид",
            )
        ]
    )
    execution = resolve(reader_conn, plan)
    assert execution.filters[0].field is FilterField.PRODUCT_NAME
    assert execution.filters[0].value == "Карбамид"


def test_resolver_switches_product_filter_to_deal_label_field(
    reader_conn: Connection,
) -> None:
    plan = make_plan(
        filters=[
            FilterCondition(
                field=FilterField.PRODUCT_NAME,
                operator=Operator.EQ,
                value="Карбамид Марка Б",
            )
        ]
    )
    execution = resolve(reader_conn, plan)
    assert execution.filters[0].field is FilterField.DEAL_PRODUCT_NAME
    assert execution.filters[0].value == "Карбамид Марка Б"


def test_resolver_switches_product_filter_to_hs_code(reader_conn: Connection) -> None:
    plan = make_plan(
        filters=[
            FilterCondition(
                field=FilterField.PRODUCT_NAME,
                operator=Operator.EQ,
                value="520100",
            )
        ]
    )
    execution = resolve(reader_conn, plan)
    assert execution.filters[0].field is FilterField.HS_CODE
    assert execution.filters[0].value == "520100"


def test_resolver_keeps_contains_filter_unresolved(reader_conn: Connection) -> None:
    plan = make_plan(
        filters=[
            FilterCondition(
                field=FilterField.PRODUCT_NAME,
                operator=Operator.CONTAINS,
                value="карбамид",
            )
        ]
    )
    execution = resolve(reader_conn, plan)
    assert execution.filters[0].operator is Operator.CONTAINS
    assert execution.filters[0].value == "карбамид"


def test_resolver_resolves_participant_entity(reader_conn: Connection) -> None:
    plan = make_plan(
        entity_candidates=[
            EntityReference(
                kind=MatchKind.PARTICIPANT,
                mention=UNIQUE_PARTICIPANT_NAME,
                role=EntityRole.SELLER,
            )
        ]
    )
    execution = resolve(reader_conn, plan)
    assert len(execution.entities) == 1
    entity = execution.entities[0]
    assert entity.id == UNIQUE_PARTICIPANT_INN
    assert entity.role is EntityRole.SELLER


def test_resolver_resolves_broker_entity(reader_conn: Connection) -> None:
    plan = make_plan(
        intent=Intent.BROKER_LOOKUP,
        subject=Subject.BROKERS,
        metric=Metric.RECORD_LOOKUP,
        entity_candidates=[EntityReference(kind=MatchKind.BROKER, mention="BK002")],
    )
    execution = resolve(reader_conn, plan)
    assert execution.entities[0].id == "BK002"


def test_resolver_resolves_product_entity_and_contract_count(
    reader_conn: Connection,
) -> None:
    plan = make_plan(
        entity_candidates=[EntityReference(kind=MatchKind.PRODUCT, mention="Карбамид")]
    )
    execution = resolve(reader_conn, plan)
    assert execution.product_contract_count == 111
    assert execution.entities[0].matched_by == "product_name"


def test_resolver_resolves_warehouse_region_entity(reader_conn: Connection) -> None:
    plan = make_plan(
        entity_candidates=[
            EntityReference(
                kind=MatchKind.REGION,
                mention="Ташкентская область",
                meaning=RegionMeaning.WAREHOUSE,
            )
        ]
    )
    execution = resolve(reader_conn, plan)
    assert execution.entities[0].id == "13"
    assert execution.entities[0].meaning is RegionMeaning.WAREHOUSE


def test_resolver_rejects_ambiguous_entity(reader_conn: Connection) -> None:
    plan = make_plan(
        entity_candidates=[
            EntityReference(
                kind=MatchKind.PARTICIPANT,
                mention="sujo plus mchj",
                role=EntityRole.SELLER,
            )
        ]
    )
    with pytest.raises(AmbiguousEntity):
        resolve(reader_conn, plan)


def test_resolver_does_not_run_for_clarify(reader_conn: Connection) -> None:
    plan = make_plan(
        decision=Decision.CLARIFY,
        clarification=ClarificationInfo(
            code=ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS
        ),
        entity_candidates=[
            EntityReference(kind=MatchKind.PARTICIPANT, mention="sujo plus mchj")
        ],
    )
    execution = EntityResolver(reader_conn).resolve(plan)
    assert execution.entities == []


def test_resolver_keeps_compare_non_date_filters(reader_conn: Connection) -> None:
    plan = make_plan(
        intent=Intent.COMPARE,
        metric=Metric.DEAL_COUNT,
        currency_mode=CurrencyMode.NOT_APPLICABLE,
        comparison=ComparisonSpec(
            left=ComparisonPeriod(
                label="2024", date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)
            ),
            right=ComparisonPeriod(
                label="2025", date_from=date(2025, 1, 1), date_to=date(2025, 12, 31)
            ),
        ),
        filters=[
            FilterCondition(
                field=FilterField.SELLER_INN,
                operator=Operator.EQ,
                value=UNIQUE_PARTICIPANT_NAME,
            )
        ],
    )
    execution = resolve(reader_conn, plan)
    assert execution.filters[0].value == UNIQUE_PARTICIPANT_INN

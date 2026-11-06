from __future__ import annotations

import json

from sqlalchemy.engine import Connection

from app.agent.query_plan import (
    EntityReference,
    ExecutionPlan,
    FilterCondition,
    QueryPlan,
    ResolvedEntity,
)
from app.domain.enums import (
    ClarificationCode,
    EntityRole,
    FilterField,
    Intent,
    Language,
    MatchKind,
    Operator,
    RegionMeaning,
    Script,
)
from app.domain.errors import AmbiguousEntity, UnsupportedQuery
from app.entities.brokers import is_broker_code, resolve_broker
from app.entities.participants import is_inn, resolve_participant
from app.entities.products import resolve_product
from app.entities.regions import resolve_region, resolve_region_id

PARTICIPANT_FIELDS = (FilterField.SELLER_INN, FilterField.BUYER_INN)
BROKER_FIELDS = (
    FilterField.SELLER_BROKER_CODE,
    FilterField.BUYER_BROKER_CODE,
)
REGION_FIELDS = (
    FilterField.WAREHOUSE_REGION,
    FilterField.SELLER_COMPANY_REGION,
    FilterField.BUYER_COMPANY_REGION,
)
PRODUCT_FIELDS = (FilterField.PRODUCT_NAME, FilterField.DEAL_PRODUCT_NAME)


def formatting_language(plan: QueryPlan) -> str:
    if plan.language is Language.RU:
        return "ru"
    if plan.script is Script.CYRILLIC:
        return "uz_cyrl"
    return "uz_latn"


def _as_items(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else [value]


def _rebuild_value(original: object, items: list[object]) -> object:
    return items if isinstance(original, list) else items[0]


def _dedupe(filters: list[FilterCondition]) -> list[FilterCondition]:
    seen: set[str] = set()
    result: list[FilterCondition] = []
    for condition in filters:
        key = json.dumps(
            [condition.field.value, condition.operator.value, condition.value],
            ensure_ascii=False,
            sort_keys=True,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(condition)
    return result


class EntityResolver:
    """Turn unresolved mentions into canonical ids before SQL is built.

    Exact identifiers and exact normalized names are the only automatic
    matches.  Fuzzy candidates are returned as clarification options and are
    never substituted into a query.
    """

    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def resolve(self, plan: QueryPlan) -> ExecutionPlan:
        if plan.decision.value != "EXECUTE":
            return ExecutionPlan(plan=plan, filters=list(plan.filters), entities=[])

        language = formatting_language(plan)
        filters: list[FilterCondition] = []
        for condition in plan.filters:
            filters.extend(self._canonical_filters(condition, language))
        filters = _dedupe(filters)

        entities = [
            self._resolve_entity(reference, plan, language)
            for reference in plan.entity_candidates
        ]

        notes: list[str] = []
        product_entities = [
            entity for entity in entities if entity.kind is MatchKind.PRODUCT
        ]
        product_contract_count: int | None = None
        if len(product_entities) == 1:
            product_contract_count = product_entities[0].contract_count
        elif len(product_entities) > 1:
            notes.append(
                "multiple product entities were resolved; they are combined with "
                "AND conditions and each keeps its own contract link"
            )
            for entity in product_entities:
                notes.append(
                    f"product '{entity.label}' covers {entity.contract_count} "
                    "contract(s) via contract_no"
                )

        return ExecutionPlan(
            plan=plan,
            filters=filters,
            entities=entities,
            evidence_notes=notes,
            product_contract_count=product_contract_count,
        )

    # -- entity candidates ----------------------------------------------
    def _resolve_entity(
        self, reference: EntityReference, plan: QueryPlan, language: str
    ) -> ResolvedEntity:
        kind = reference.kind
        mention = reference.mention

        if kind is MatchKind.PARTICIPANT:
            if reference.role is None and plan.intent is not Intent.PARTICIPANT_LOOKUP:
                raise AmbiguousEntity(
                    ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS,
                    "participant must be clarified as seller or buyer",
                )
            match = resolve_participant(self.conn, mention)
            return ResolvedEntity(
                kind=kind,
                id=match.inn,
                label=match.name,
                mention=mention,
                role=reference.role,
                matched_by=match.matched_by,
            )

        if kind is MatchKind.BROKER:
            if reference.role is None and plan.intent is not Intent.BROKER_LOOKUP:
                raise AmbiguousEntity(
                    ClarificationCode.BROKER_SIDE_AMBIGUOUS,
                    "broker must be clarified as seller broker or buyer broker",
                )
            match = resolve_broker(self.conn, mention)
            return ResolvedEntity(
                kind=kind,
                id=match.broker_code,
                label=match.name,
                mention=mention,
                role=reference.role,
                matched_by=match.matched_by,
            )

        if kind is MatchKind.PRODUCT:
            match = resolve_product(self.conn, mention)
            return ResolvedEntity(
                kind=kind,
                id=match.id,
                label=match.label,
                mention=mention,
                matched_by=match.matched_by,
                contract_count=match.contract_count,
            )

        if kind is MatchKind.REGION:
            if reference.meaning is None:
                raise AmbiguousEntity(
                    ClarificationCode.REGION_MEANING_AMBIGUOUS,
                    "region must be clarified as warehouse region or company region",
                )
            if (
                reference.meaning is RegionMeaning.COMPANY
                and reference.role
                not in (EntityRole.SELLER, EntityRole.BUYER, EntityRole.ANY)
            ):
                raise AmbiguousEntity(
                    ClarificationCode.REGION_MEANING_AMBIGUOUS,
                    "company region must be clarified as seller or buyer side",
                )
            match = resolve_region(self.conn, mention, language=language)
            return ResolvedEntity(
                kind=kind,
                id=str(match.region_id),
                label=match.label,
                mention=mention,
                role=reference.role,
                meaning=reference.meaning,
                matched_by=match.matched_by,
            )

        raise UnsupportedQuery(f"unknown entity kind {kind}")

    # -- filter canonicalization ----------------------------------------
    def _canonical_filters(
        self, condition: FilterCondition, language: str
    ) -> list[FilterCondition]:
        field = condition.field
        operator = condition.operator
        value = condition.value

        if field is FilterField.DEAL_DATE:
            return [condition]

        if field in (FilterField.DEAL_NO, FilterField.CONTRACT_NO):
            items = [str(item).strip() for item in _as_items(value)]
            return [
                FilterCondition(
                    field=field,
                    operator=operator,
                    value=_rebuild_value(value, items),
                )
            ]

        if field in PARTICIPANT_FIELDS:
            if operator not in (Operator.EQ, Operator.IN):
                raise UnsupportedQuery(
                    f"{field.value} supports only exact or in lookup"
                )
            items: list[object] = []
            for item in _as_items(value):
                text = str(item).strip()
                if is_inn(text):
                    items.append(text)
                else:
                    items.append(resolve_participant(self.conn, text).inn)
            return [
                FilterCondition(
                    field=field,
                    operator=operator,
                    value=_rebuild_value(value, items),
                )
            ]

        if field in BROKER_FIELDS:
            if operator not in (Operator.EQ, Operator.IN):
                raise UnsupportedQuery(
                    f"{field.value} supports only exact or in lookup"
                )
            items = []
            for item in _as_items(value):
                text = str(item).strip()
                if is_broker_code(text):
                    items.append(text.upper())
                else:
                    items.append(resolve_broker(self.conn, text).broker_code)
            return [
                FilterCondition(
                    field=field,
                    operator=operator,
                    value=_rebuild_value(value, items),
                )
            ]

        if field in REGION_FIELDS:
            if operator not in (Operator.EQ, Operator.IN):
                raise UnsupportedQuery(
                    f"{field.value} supports only exact or in lookup"
                )
            items = [
                resolve_region_id(self.conn, item, language=language)
                for item in _as_items(value)
            ]
            return [
                FilterCondition(
                    field=field,
                    operator=operator,
                    value=_rebuild_value(value, items),
                )
            ]

        if field in PRODUCT_FIELDS:
            if operator is Operator.CONTAINS:
                text = str(value).strip()
                return [
                    FilterCondition(
                        field=field, operator=operator, value=text
                    )
                ]
            if operator not in (Operator.EQ, Operator.IN):
                raise UnsupportedQuery(
                    f"{field.value} supports only exact, in or contains"
                )
            prefer = (
                "deal_product_name"
                if field is FilterField.DEAL_PRODUCT_NAME
                else "product_name"
            )
            matches = [
                resolve_product(self.conn, str(item).strip(), prefer=prefer)
                for item in _as_items(value)
            ]
            target_fields = {match.matched_by for match in matches}
            if len(target_fields) != 1:
                raise UnsupportedQuery(
                    "product filter values resolve to different product fields; "
                    "ask for one exact product at a time"
                )
            matched_by = matches[0].matched_by
            if matched_by == "hs_code":
                target_field = FilterField.HS_CODE
                new_items: list[object] = [match.id for match in matches]
            elif matched_by == "deal_product_name":
                target_field = FilterField.DEAL_PRODUCT_NAME
                new_items = [match.label for match in matches]
            else:
                target_field = FilterField.PRODUCT_NAME
                new_items = [match.label for match in matches]
            return [
                FilterCondition(
                    field=target_field,
                    operator=operator,
                    value=_rebuild_value(value, new_items),
                )
            ]

        if field is FilterField.HS_CODE:
            items = [str(item).strip() for item in _as_items(value)]
            return [
                FilterCondition(
                    field=field,
                    operator=operator,
                    value=_rebuild_value(value, items),
                )
            ]

        # Categorical and unit fields keep their source spelling; only outer
        # whitespace is removed.
        items = [str(item).strip() for item in _as_items(value)]
        return [
            FilterCondition(
                field=field,
                operator=operator,
                value=_rebuild_value(value, items),
            )
        ]

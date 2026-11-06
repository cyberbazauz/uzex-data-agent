from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Integer, and_, case, func, or_, select
from sqlalchemy.orm import aliased

from app.agent.query_plan import FilterCondition, ResolvedEntity
from app.database.models import Broker, Contract, Deal, Participant, Region, UsdRate
from app.domain.enums import (
    CURRENCY_UZS,
    CURRENCY_USD,
    ClarificationCode,
    EntityRole,
    FilterField,
    GroupField,
    MatchKind,
    Operator,
    RegionMeaning,
)
from app.domain.errors import AmbiguousEntity, UnsupportedQuery
from app.domain.normalization import normalize_text


@dataclass(frozen=True)
class GroupSpec:
    field: GroupField
    key: Any
    label: Any | None
    output_name: str


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _as_text(value: object, field: FilterField) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise UnsupportedQuery(f"field {field} expects a text identifier")
    text = str(value).strip()
    if not text:
        raise UnsupportedQuery(f"field {field} received a blank identifier")
    return text


def _as_region_id(value: object) -> int:
    if isinstance(value, bool):
        raise UnsupportedQuery("region filter must be a region id resolved by the server")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    raise UnsupportedQuery(
        "region filter must be a resolved region id; a region name cannot be "
        "inserted into SQL directly"
    )


class DealQueryBuilder:
    """Builds parameterized SQLAlchemy statements from a constant registry.

    No user or LLM text ever becomes a table name, column name or SQL fragment.
    Field names are enum members, columns come from this module, and every value
    is a bind parameter.
    """

    def __init__(self) -> None:
        self.seller = aliased(Participant, name="seller_participant")
        self.buyer = aliased(Participant, name="buyer_participant")
        self.seller_broker = aliased(Broker, name="seller_broker")
        self.buyer_broker = aliased(Broker, name="buyer_broker")
        self.warehouse_region = aliased(Region, name="warehouse_region")
        self.usd_rate = aliased(UsdRate, name="usd_rate")

        self.conditions: list[Any] = []
        self._needs_contract = False
        self._needs_seller = False
        self._needs_buyer = False
        self._needs_seller_broker = False
        self._needs_buyer_broker = False
        self._needs_warehouse_region = False
        self._needs_rate = False

    # -- join management -------------------------------------------------
    def require_contract(self) -> type[Contract]:
        self._needs_contract = True
        return Contract

    def require_seller(self) -> Any:
        self._needs_seller = True
        return self.seller

    def require_buyer(self) -> Any:
        self._needs_buyer = True
        return self.buyer

    def require_seller_broker(self) -> Any:
        self._needs_seller_broker = True
        return self.seller_broker

    def require_buyer_broker(self) -> Any:
        self._needs_buyer_broker = True
        return self.buyer_broker

    def require_warehouse_region(self) -> Any:
        self._needs_warehouse_region = True
        return self.warehouse_region

    def require_rate(self) -> Any:
        self._needs_rate = True
        return self.usd_rate

    @property
    def requires_rate(self) -> bool:
        return self._needs_rate

    def add_condition(self, condition: Any) -> None:
        self.conditions.append(condition)

    def add_conditions(self, conditions: list[Any] | tuple[Any, ...]) -> None:
        self.conditions.extend(conditions)

    # -- statements ------------------------------------------------------
    def select(self, *columns: Any) -> Any:
        statement = select(*columns).select_from(Deal)
        if self._needs_contract:
            statement = statement.join(
                Contract, Contract.contract_no == Deal.contract_no
            )
        if self._needs_seller:
            statement = statement.join(self.seller, self.seller.inn == Deal.seller_inn)
        if self._needs_buyer:
            statement = statement.join(self.buyer, self.buyer.inn == Deal.buyer_inn)
        if self._needs_seller_broker:
            statement = statement.join(
                self.seller_broker,
                self.seller_broker.broker_code == Deal.seller_broker_code,
            )
        if self._needs_buyer_broker:
            statement = statement.join(
                self.buyer_broker,
                self.buyer_broker.broker_code == Deal.buyer_broker_code,
            )
        if self._needs_warehouse_region:
            statement = statement.join(
                self.warehouse_region,
                self.warehouse_region.region_id == Deal.warehouse_region_id,
            )
        if self._needs_rate:
            # LEFT JOIN so a missing rate is detected instead of silently
            # dropping a deal from all aggregates.
            statement = statement.join(
                self.usd_rate,
                self.usd_rate.rate_date == Deal.deal_date,
                isouter=True,
            )
        if self.conditions:
            statement = statement.where(and_(*self.conditions))
        return statement

    def source_tables(self) -> list[str]:
        tables = ["deals"]
        if self._needs_contract:
            tables.append("contracts")
        if self._needs_seller or self._needs_buyer:
            tables.append("participants")
        if self._needs_seller_broker or self._needs_buyer_broker:
            tables.append("brokers")
        if self._needs_warehouse_region:
            tables.append("regions")
        if self._needs_rate:
            tables.append("usd_rates")
        return tables

    # -- field registry --------------------------------------------------
    def field_column(self, field: FilterField) -> Any:
        if field is FilterField.DEAL_DATE:
            return Deal.deal_date
        if field is FilterField.DEAL_NO:
            return Deal.deal_no
        if field is FilterField.CONTRACT_NO:
            return Deal.contract_no
        if field is FilterField.TRADE_TYPE:
            return Deal.trade_type
        if field is FilterField.STATUS:
            return Deal.status
        if field is FilterField.CONTRACT_TYPE:
            return Deal.contract_type
        if field is FilterField.CONTRACT_FORM:
            return Deal.contract_form
        if field is FilterField.CURRENCY:
            self.require_contract()
            return Contract.currency
        if field is FilterField.HS_CODE:
            self.require_contract()
            return Contract.hs_code
        if field is FilterField.PRODUCT_GROUP:
            self.require_contract()
            return Contract.product_group
        if field is FilterField.PRODUCT_CATEGORY:
            self.require_contract()
            return Contract.product_category
        if field is FilterField.INDUSTRY:
            self.require_contract()
            return Contract.industry
        if field is FilterField.PRODUCT_NAME:
            self.require_contract()
            return Contract.product_name
        if field is FilterField.DEAL_PRODUCT_NAME:
            return Deal.deal_product_name
        if field is FilterField.SELLER_INN:
            return Deal.seller_inn
        if field is FilterField.BUYER_INN:
            return Deal.buyer_inn
        if field is FilterField.SELLER_BROKER_CODE:
            return Deal.seller_broker_code
        if field is FilterField.BUYER_BROKER_CODE:
            return Deal.buyer_broker_code
        if field is FilterField.WAREHOUSE_REGION:
            return Deal.warehouse_region_id
        if field is FilterField.SELLER_COMPANY_REGION:
            return self.require_seller().company_region_id
        if field is FilterField.BUYER_COMPANY_REGION:
            return self.require_buyer().company_region_id
        if field is FilterField.UNIT_NAME:
            return Deal.unit_name
        raise UnsupportedQuery(f"field {field} is not in the query registry")

    def _contains_column(self, field: FilterField) -> Any:
        if field is FilterField.PRODUCT_NAME:
            self.require_contract()
            return Contract.product_name_normalized
        if field is FilterField.DEAL_PRODUCT_NAME:
            return Deal.deal_product_name_normalized
        raise UnsupportedQuery(f"contains is not allowed for {field}")

    def build_condition(self, condition: FilterCondition) -> Any:
        field = condition.field
        operator = condition.operator
        value = condition.value

        if field is FilterField.DEAL_DATE:
            if operator is Operator.BETWEEN:
                assert isinstance(value, list) and len(value) == 2
                start = date.fromisoformat(str(value[0]))
                end = date.fromisoformat(str(value[1]))
                return Deal.deal_date.between(start, end)
            parsed = date.fromisoformat(str(value))
            if operator is Operator.EQ:
                return Deal.deal_date == parsed
            if operator is Operator.GTE:
                return Deal.deal_date >= parsed
            if operator is Operator.LTE:
                return Deal.deal_date <= parsed
            raise UnsupportedQuery(f"operator {operator} is not valid for deal_date")

        column = self.field_column(field)

        if operator is Operator.CONTAINS:
            text = _as_text(value, field)
            pattern = f"%{_escape_like(normalize_text(text))}%"
            return self._contains_column(field).ilike(pattern, escape="\\")

        if operator is Operator.IN:
            if not isinstance(value, list):
                raise UnsupportedQuery(f"operator in requires a list for {field}")
            items = value
        else:
            items = [value]

        if operator not in (Operator.EQ, Operator.IN, Operator.GTE, Operator.LTE):
            raise UnsupportedQuery(f"operator {operator} is not allowed for {field}")

        converted: list[Any] = []
        for item in items:
            if field in (FilterField.WAREHOUSE_REGION, FilterField.SELLER_COMPANY_REGION, FilterField.BUYER_COMPANY_REGION):
                converted.append(_as_region_id(item))
            else:
                converted.append(_as_text(item, field))

        if operator is Operator.IN:
            return column.in_(converted)
        item = converted[0]
        if operator is Operator.EQ:
            return column == item
        if operator is Operator.GTE:
            return column >= item
        return column <= item

    def apply_filters(self, filters: list[FilterCondition]) -> None:
        for condition in filters:
            self.add_condition(self.build_condition(condition))

    # -- entity registry -------------------------------------------------
    def apply_entities(self, entities: list[ResolvedEntity]) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for entity in entities:
            if entity.kind is MatchKind.PARTICIPANT:
                if entity.role is EntityRole.SELLER:
                    self.add_condition(Deal.seller_inn == entity.id)
                    field = FilterField.SELLER_INN
                elif entity.role is EntityRole.BUYER:
                    self.add_condition(Deal.buyer_inn == entity.id)
                    field = FilterField.BUYER_INN
                elif entity.role is EntityRole.ANY:
                    self.add_condition(
                        or_(
                            Deal.seller_inn == entity.id,
                            Deal.buyer_inn == entity.id,
                        )
                    )
                    field = FilterField.SELLER_INN
                else:
                    raise AmbiguousEntity(
                        ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS,
                        "participant role must be seller or buyer before execution",
                    )
                evidence.append(
                    {
                        "field": field.value,
                        "operator": "eq",
                        "value": entity.id,
                        "resolved_from": entity.mention,
                        "resolved_label": entity.label,
                        "resolution": entity.matched_by,
                    }
                )
            elif entity.kind is MatchKind.BROKER:
                if entity.role is EntityRole.BROKER_SELLER:
                    self.add_condition(Deal.seller_broker_code == entity.id)
                    field = FilterField.SELLER_BROKER_CODE
                elif entity.role is EntityRole.BROKER_BUYER:
                    self.add_condition(Deal.buyer_broker_code == entity.id)
                    field = FilterField.BUYER_BROKER_CODE
                elif entity.role is EntityRole.ANY:
                    self.add_condition(
                        or_(
                            Deal.seller_broker_code == entity.id,
                            Deal.buyer_broker_code == entity.id,
                        )
                    )
                    field = FilterField.SELLER_BROKER_CODE
                else:
                    raise AmbiguousEntity(
                        ClarificationCode.BROKER_SIDE_AMBIGUOUS,
                        "broker side must be seller or buyer before execution",
                    )
                evidence.append(
                    {
                        "field": field.value,
                        "operator": "eq",
                        "value": entity.id,
                        "resolved_label": entity.label,
                        "resolution": entity.matched_by,
                    }
                )
            elif entity.kind is MatchKind.PRODUCT:
                self.require_contract()
                if entity.matched_by in ("deal_product_name", "exact_deal_product_name"):
                    self.add_condition(Deal.deal_product_name == entity.label)
                    evidence_field = FilterField.DEAL_PRODUCT_NAME
                elif entity.matched_by == "hs_code":
                    self.add_condition(Contract.hs_code == entity.id)
                    evidence_field = FilterField.HS_CODE
                elif entity.matched_by in ("product_name", "exact_product_name"):
                    self.add_condition(Contract.product_name == entity.label)
                    evidence_field = FilterField.PRODUCT_NAME
                else:
                    raise UnsupportedQuery(
                        f"unsupported product resolution source {entity.matched_by}"
                    )
                evidence.append(
                    {
                        "field": evidence_field.value,
                        "operator": "eq",
                        "value": entity.id,
                        "resolved_label": entity.label,
                        "resolution": entity.matched_by,
                        "contract_count": entity.contract_count,
                    }
                )
            elif entity.kind is MatchKind.REGION:
                region_id = _as_region_id(entity.id)
                if entity.meaning is RegionMeaning.WAREHOUSE:
                    self.add_condition(Deal.warehouse_region_id == region_id)
                    field = FilterField.WAREHOUSE_REGION
                elif entity.meaning is RegionMeaning.COMPANY:
                    if entity.role is EntityRole.SELLER:
                        self.add_condition(
                            self.require_seller().company_region_id == region_id
                        )
                        field = FilterField.SELLER_COMPANY_REGION
                    elif entity.role is EntityRole.BUYER:
                        self.add_condition(
                            self.require_buyer().company_region_id == region_id
                        )
                        field = FilterField.BUYER_COMPANY_REGION
                    elif entity.role is EntityRole.ANY:
                        seller = self.require_seller()
                        buyer = self.require_buyer()
                        self.add_condition(
                            or_(
                                seller.company_region_id == region_id,
                                buyer.company_region_id == region_id,
                            )
                        )
                        field = FilterField.SELLER_COMPANY_REGION
                    else:
                        raise AmbiguousEntity(
                            ClarificationCode.REGION_MEANING_AMBIGUOUS,
                            "company region requires seller or buyer side",
                        )
                else:
                    raise AmbiguousEntity(
                        ClarificationCode.REGION_MEANING_AMBIGUOUS,
                        "region meaning must be warehouse or company",
                    )
                evidence.append(
                    {
                        "field": field.value,
                        "operator": "eq",
                        "value": region_id,
                        "resolved_label": entity.label,
                        "resolution": entity.matched_by,
                    }
                )
            else:
                raise UnsupportedQuery(f"unknown entity kind {entity.kind}")
        return evidence

    # -- grouping --------------------------------------------------------
    def group_spec(self, field: GroupField) -> GroupSpec:
        if field is GroupField.YEAR:
            return GroupSpec(
                field,
                func.extract("year", Deal.deal_date).cast(Integer),
                None,
                field.value,
            )
        if field is GroupField.MONTH:
            return GroupSpec(
                field,
                func.to_char(Deal.deal_date, "YYYY-MM"),
                None,
                field.value,
            )
        if field is GroupField.DEAL_DATE:
            return GroupSpec(field, Deal.deal_date, None, field.value)
        if field is GroupField.CONTRACT_NO:
            return GroupSpec(field, Deal.contract_no, None, field.value)
        if field is GroupField.PRODUCT_NAME:
            self.require_contract()
            return GroupSpec(field, Contract.product_name, None, field.value)
        if field is GroupField.PRODUCT_CATEGORY:
            self.require_contract()
            return GroupSpec(field, Contract.product_category, None, field.value)
        if field is GroupField.PRODUCT_GROUP:
            self.require_contract()
            return GroupSpec(field, Contract.product_group, None, field.value)
        if field is GroupField.INDUSTRY:
            self.require_contract()
            return GroupSpec(field, Contract.industry, None, field.value)
        if field is GroupField.HS_CODE:
            self.require_contract()
            return GroupSpec(field, Contract.hs_code, None, field.value)
        if field is GroupField.CONTRACT_TYPE:
            return GroupSpec(field, Deal.contract_type, None, field.value)
        if field is GroupField.CONTRACT_FORM:
            return GroupSpec(field, Deal.contract_form, None, field.value)
        if field is GroupField.CURRENCY:
            self.require_contract()
            return GroupSpec(field, Contract.currency, None, field.value)
        if field is GroupField.TRADE_TYPE:
            return GroupSpec(field, Deal.trade_type, None, field.value)
        if field is GroupField.STATUS:
            return GroupSpec(field, Deal.status, None, field.value)
        if field is GroupField.UNIT_NAME:
            return GroupSpec(field, Deal.unit_name, None, field.value)
        if field is GroupField.WAREHOUSE_REGION:
            region = self.require_warehouse_region()
            return GroupSpec(
                field,
                region.region_id,
                region.canonical_name_ru,
                field.value,
            )
        if field is GroupField.SELLER_INN:
            seller = self.require_seller()
            return GroupSpec(field, seller.inn, seller.name, field.value)
        if field is GroupField.BUYER_INN:
            buyer = self.require_buyer()
            return GroupSpec(field, buyer.inn, buyer.name, field.value)
        if field is GroupField.SELLER_BROKER_CODE:
            broker = self.require_seller_broker()
            return GroupSpec(field, broker.broker_code, broker.name, field.value)
        if field is GroupField.BUYER_BROKER_CODE:
            broker = self.require_buyer_broker()
            return GroupSpec(field, broker.broker_code, broker.name, field.value)
        raise UnsupportedQuery(f"group field {field} is not in the query registry")

    # -- amount / volume expressions ------------------------------------
    def converted_amount(self) -> Any:
        self.require_contract()
        rate = self.require_rate()
        return case(
            (
                Contract.currency == CURRENCY_USD,
                Deal.deal_amount * rate.uzs_per_usd,
            ),
            else_=Deal.deal_amount,
        )

    def native_amount(self) -> Any:
        self.require_contract()
        return Deal.deal_amount

    def physical_volume(self, delivered: bool = False) -> Any:
        if delivered:
            return Deal.delivered_lots * Deal.lot_multiplier
        return Deal.quantity_lots * Deal.lot_multiplier

    def missing_rate_count(self, conn: Any) -> int:
        """Count USD deals in scope whose deal date has no USD rate."""
        if not self._needs_rate:
            return 0
        statement = self.select(func.count()).where(
            Contract.currency == CURRENCY_USD,
            self.usd_rate.rate_date.is_(None),
        )
        return int(conn.execute(statement).scalar_one())


def currency_conditions(currency_mode: Any) -> tuple[list[Any], bool, str | None]:
    """Return (extra conditions, group-by-currency, method description)."""
    from app.domain.enums import CurrencyMode

    if currency_mode is CurrencyMode.NATIVE_BY_CURRENCY:
        return [], True, "native amounts by contract currency; currencies are not combined"
    if currency_mode is CurrencyMode.UZS_CONVERTED:
        return (
            [],
            False,
            "UZS amount for UZS contracts + USD amount × USD/UZS rate on deal date",
        )
    if currency_mode is CurrencyMode.USD_NATIVE:
        return (
            [Contract.currency == CURRENCY_USD],
            False,
            "USD-native amounts only; no conversion applied",
        )
    if currency_mode is CurrencyMode.UZS_NATIVE:
        return (
            [Contract.currency == CURRENCY_UZS],
            False,
            "UZS-native amounts only; export amounts are not included",
        )
    return (
        [],
        False,
        None,
    )

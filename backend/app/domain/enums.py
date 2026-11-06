from __future__ import annotations

from enum import StrEnum


class Decision(StrEnum):
    EXECUTE = "EXECUTE"
    CLARIFY = "CLARIFY"
    UNSUPPORTED = "UNSUPPORTED"
    # General AI Mode: a conversational answer that does not touch the
    # registry.  The reason, filters and facts are deliberately absent.
    GENERAL = "GENERAL"


class Language(StrEnum):
    RU = "ru"
    UZ = "uz"


class Script(StrEnum):
    CYRILLIC = "cyrillic"
    LATIN = "latin"
    NOT_APPLICABLE = "not_applicable"


class Intent(StrEnum):
    # Only valid together with Decision.GENERAL; never executed as SQL.
    GENERAL = "general"
    DEAL_LOOKUP = "deal_lookup"
    CONTRACT_LOOKUP = "contract_lookup"
    PARTICIPANT_LOOKUP = "participant_lookup"
    BROKER_LOOKUP = "broker_lookup"
    COUNT = "count"
    SUM_AMOUNT = "sum_amount"
    SUM_VOLUME = "sum_volume"
    AVERAGE = "average"
    MIN_MAX = "min_max"
    TOP_N = "top_n"
    COMPARE = "compare"
    TREND = "trend"
    FILTER_LIST = "filter_list"


class Subject(StrEnum):
    # Only valid together with Decision.GENERAL.
    NOT_APPLICABLE = "not_applicable"
    DEALS = "deals"
    CONTRACTS = "contracts"
    PARTICIPANTS = "participants"
    BROKERS = "brokers"
    USD_RATES = "usd_rates"


class Metric(StrEnum):
    # Only valid together with Decision.GENERAL.
    NOT_APPLICABLE = "not_applicable"
    RECORD_LOOKUP = "record_lookup"
    DEAL_COUNT = "deal_count"
    AMOUNT_SUM_NATIVE_BY_CURRENCY = "amount_sum_native_by_currency"
    AMOUNT_SUM_UZS_CONVERTED = "amount_sum_uzs_converted"
    AMOUNT_AVERAGE_UZS_CONVERTED = "amount_average_uzs_converted"
    AMOUNT_MIN = "amount_min"
    AMOUNT_MAX = "amount_max"
    VOLUME_SUM_BY_UNIT = "volume_sum_by_unit"
    UNIT_PRICE_SIMPLE_AVERAGE = "unit_price_simple_average"
    UNIT_PRICE_WEIGHTED_AVERAGE = "unit_price_weighted_average"
    DELIVERED_LOTS_SUM = "delivered_lots_sum"
    DELIVERED_VOLUME_BY_UNIT = "delivered_volume_by_unit"


class FilterField(StrEnum):
    DEAL_DATE = "deal_date"
    DEAL_NO = "deal_no"
    CONTRACT_NO = "contract_no"
    TRADE_TYPE = "trade_type"
    STATUS = "status"
    CONTRACT_TYPE = "contract_type"
    CONTRACT_FORM = "contract_form"
    CURRENCY = "currency"
    HS_CODE = "hs_code"
    PRODUCT_GROUP = "product_group"
    PRODUCT_CATEGORY = "product_category"
    INDUSTRY = "industry"
    PRODUCT_NAME = "product_name"
    DEAL_PRODUCT_NAME = "deal_product_name"
    SELLER_INN = "seller_inn"
    BUYER_INN = "buyer_inn"
    SELLER_BROKER_CODE = "seller_broker_code"
    BUYER_BROKER_CODE = "buyer_broker_code"
    WAREHOUSE_REGION = "warehouse_region"
    SELLER_COMPANY_REGION = "seller_company_region"
    BUYER_COMPANY_REGION = "buyer_company_region"
    UNIT_NAME = "unit_name"


class Operator(StrEnum):
    EQ = "eq"
    IN = "in"
    GTE = "gte"
    LTE = "lte"
    BETWEEN = "between"
    CONTAINS = "contains"


class CurrencyMode(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    NATIVE_BY_CURRENCY = "native_by_currency"
    UZS_CONVERTED = "uzs_converted"
    USD_NATIVE = "usd_native"
    UZS_NATIVE = "uzs_native"


class GroupField(StrEnum):
    YEAR = "year"
    MONTH = "month"
    DEAL_DATE = "deal_date"
    CONTRACT_NO = "contract_no"
    PRODUCT_NAME = "product_name"
    PRODUCT_CATEGORY = "product_category"
    PRODUCT_GROUP = "product_group"
    INDUSTRY = "industry"
    HS_CODE = "hs_code"
    CONTRACT_TYPE = "contract_type"
    CONTRACT_FORM = "contract_form"
    CURRENCY = "currency"
    TRADE_TYPE = "trade_type"
    STATUS = "status"
    UNIT_NAME = "unit_name"
    WAREHOUSE_REGION = "warehouse_region"
    SELLER_INN = "seller_inn"
    BUYER_INN = "buyer_inn"
    SELLER_BROKER_CODE = "seller_broker_code"
    BUYER_BROKER_CODE = "buyer_broker_code"


class OrderField(StrEnum):
    METRIC = "metric"
    GROUP_KEY = "group_key"
    GROUP_LABEL = "group_label"
    DEAL_DATE = "deal_date"
    DEAL_NO = "deal_no"
    DEAL_AMOUNT = "deal_amount"


class Direction(StrEnum):
    ASC = "asc"
    DESC = "desc"


class ClarificationCode(StrEnum):
    BROKER_SIDE_AMBIGUOUS = "BROKER_SIDE_AMBIGUOUS"
    PARTICIPANT_ROLE_AMBIGUOUS = "PARTICIPANT_ROLE_AMBIGUOUS"
    REGION_MEANING_AMBIGUOUS = "REGION_MEANING_AMBIGUOUS"
    PRODUCT_AMBIGUOUS = "PRODUCT_AMBIGUOUS"
    PRODUCT_TOO_GENERIC = "PRODUCT_TOO_GENERIC"
    PARTICIPANT_AMBIGUOUS = "PARTICIPANT_AMBIGUOUS"
    BROKER_AMBIGUOUS = "BROKER_AMBIGUOUS"
    REGION_AMBIGUOUS = "REGION_AMBIGUOUS"
    CURRENCY_AMBIGUOUS = "CURRENCY_AMBIGUOUS"
    UNIT_AMBIGUOUS = "UNIT_AMBIGUOUS"
    PERIOD_AMBIGUOUS = "PERIOD_AMBIGUOUS"


class ResponseStatus(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    UNSUPPORTED = "UNSUPPORTED"
    UNVERIFIED = "UNVERIFIED"
    # General AI Mode answer: conversational, based on general knowledge and
    # never presented as a PostgreSQL-verified fact (no data/evidence).
    GENERAL = "GENERAL"
    # The general provider or its output validation failed.  This is explicitly
    # not the same as "no data in the registry" and must never carry the
    # UNVERIFIED data message.
    GENERAL_UNAVAILABLE = "GENERAL_UNAVAILABLE"


class MatchKind(StrEnum):
    PARTICIPANT = "participant"
    BROKER = "broker"
    PRODUCT = "product"
    REGION = "region"


class EntityRole(StrEnum):
    SELLER = "seller"
    BUYER = "buyer"
    BROKER_SELLER = "broker_seller"
    BROKER_BUYER = "broker_buyer"
    ANY = "any"


class RegionMeaning(StrEnum):
    WAREHOUSE = "warehouse"
    COMPANY = "company"


# Exact source values. They are stored as text; these constants are used only
# for validation and controlled vocabularies, never for inventing data.
STATUSES: tuple[str, ...] = (
    "Новый",
    "Не подписан",
    "Заключен",
    "Ждёт оплату",
    "Оплачен",
    "Исполнен",
    "Исполнен(Частично)",
    "Аннулирован(Не оплачен)",
)
STATUSES_EXECUTED = ("Исполнен",)
STATUSES_EXECUTED_OR_PARTIAL = ("Исполнен", "Исполнен(Частично)")
TRADE_TYPES: tuple[str, ...] = (
    "Английский аукцион",
    "Обратный аукцион",
    "Двусторонний аукцион",
)
CONTRACT_TYPES: tuple[str, ...] = ("Внутренний", "Экспортный")
CONTRACT_FORMS: tuple[str, ...] = ("Спот", "Форвард", "Фьючерс")
CURRENCIES: tuple[str, ...] = ("Узбекский сум", "Доллар США")
CURRENCY_UZS = "Узбекский сум"
CURRENCY_USD = "Доллар США"

INTENT_SUBJECT_METRICS: dict[Intent, dict[Subject, frozenset[Metric]]] = {
    Intent.DEAL_LOOKUP: {Subject.DEALS: frozenset({Metric.RECORD_LOOKUP})},
    Intent.CONTRACT_LOOKUP: {Subject.CONTRACTS: frozenset({Metric.RECORD_LOOKUP})},
    Intent.PARTICIPANT_LOOKUP: {Subject.PARTICIPANTS: frozenset({Metric.RECORD_LOOKUP})},
    Intent.BROKER_LOOKUP: {Subject.BROKERS: frozenset({Metric.RECORD_LOOKUP})},
    Intent.COUNT: {Subject.DEALS: frozenset({Metric.DEAL_COUNT})},
    Intent.SUM_AMOUNT: {
        Subject.DEALS: frozenset(
            {
                Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
                Metric.AMOUNT_SUM_UZS_CONVERTED,
            }
        )
    },
    Intent.SUM_VOLUME: {
        Subject.DEALS: frozenset({Metric.VOLUME_SUM_BY_UNIT, Metric.DELIVERED_VOLUME_BY_UNIT})
    },
    Intent.AVERAGE: {
        Subject.DEALS: frozenset(
            {
                Metric.AMOUNT_AVERAGE_UZS_CONVERTED,
                Metric.UNIT_PRICE_SIMPLE_AVERAGE,
                Metric.UNIT_PRICE_WEIGHTED_AVERAGE,
            }
        )
    },
    Intent.MIN_MAX: {
        Subject.DEALS: frozenset({Metric.AMOUNT_MIN, Metric.AMOUNT_MAX})
    },
    Intent.TOP_N: {
        Subject.DEALS: frozenset(
            {
                Metric.DEAL_COUNT,
                Metric.AMOUNT_SUM_UZS_CONVERTED,
                Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
                Metric.VOLUME_SUM_BY_UNIT,
                Metric.DELIVERED_LOTS_SUM,
            }
        ),
        Subject.PARTICIPANTS: frozenset(
            {Metric.DEAL_COUNT, Metric.AMOUNT_SUM_UZS_CONVERTED}
        ),
        Subject.BROKERS: frozenset({Metric.DEAL_COUNT, Metric.AMOUNT_SUM_UZS_CONVERTED}),
    },
    Intent.COMPARE: {
        Subject.DEALS: frozenset(
            {
                Metric.DEAL_COUNT,
                Metric.AMOUNT_SUM_UZS_CONVERTED,
                Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
                Metric.VOLUME_SUM_BY_UNIT,
            }
        )
    },
    Intent.TREND: {
        Subject.DEALS: frozenset(
            {
                Metric.DEAL_COUNT,
                Metric.AMOUNT_SUM_UZS_CONVERTED,
                Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
                Metric.VOLUME_SUM_BY_UNIT,
            }
        )
    },
    Intent.FILTER_LIST: {Subject.DEALS: frozenset({Metric.DEAL_COUNT, Metric.RECORD_LOOKUP})},
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

# Fixed default source tables per intent, used in evidence.
INTENT_SOURCE_TABLES: dict[Intent, tuple[str, ...]] = {
    Intent.DEAL_LOOKUP: ("deals", "contracts", "participants", "brokers", "regions", "usd_rates"),
    Intent.CONTRACT_LOOKUP: ("contracts", "participants", "regions"),
    Intent.PARTICIPANT_LOOKUP: ("participants", "deals"),
    Intent.BROKER_LOOKUP: ("brokers", "deals"),
    Intent.COUNT: ("deals", "contracts", "participants", "brokers", "regions", "usd_rates"),
    Intent.SUM_AMOUNT: ("deals", "contracts", "usd_rates"),
    Intent.SUM_VOLUME: ("deals", "contracts"),
    Intent.AVERAGE: ("deals", "contracts", "usd_rates"),
    Intent.MIN_MAX: ("deals", "contracts", "usd_rates"),
    Intent.TOP_N: ("deals", "contracts", "participants", "brokers", "usd_rates"),
    Intent.COMPARE: ("deals", "contracts", "usd_rates"),
    Intent.TREND: ("deals", "contracts", "usd_rates"),
    Intent.FILTER_LIST: ("deals", "contracts", "participants", "brokers", "regions", "usd_rates"),
}

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.agent.query_plan import FilterCondition, QueryPlan
from app.domain.enums import (
    CURRENCY_USD,
    CURRENCY_UZS,
    EntityRole,
    FilterField,
    GroupField,
    Language,
    Metric,
    Operator,
    Script,
    Subject,
)
from app.handlers.base import ResultRow

_GROUP_SEPARATOR = "\u00a0"
_DECIMAL_SEPARATOR = ","

METRIC_KIND: dict[Metric, str] = {
    Metric.RECORD_LOOKUP: "record",
    Metric.DEAL_COUNT: "count",
    Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY: "money",
    Metric.AMOUNT_SUM_UZS_CONVERTED: "money",
    Metric.AMOUNT_AVERAGE_UZS_CONVERTED: "money",
    Metric.AMOUNT_MIN: "money",
    Metric.AMOUNT_MAX: "money",
    Metric.VOLUME_SUM_BY_UNIT: "volume",
    Metric.DELIVERED_VOLUME_BY_UNIT: "volume",
    Metric.DELIVERED_LOTS_SUM: "volume",
    Metric.UNIT_PRICE_SIMPLE_AVERAGE: "price",
    Metric.UNIT_PRICE_WEIGHTED_AVERAGE: "price",
}

METRIC_LABEL_KEY: dict[Metric, str] = {
    Metric.DEAL_COUNT: "metric_count",
    Metric.AMOUNT_SUM_UZS_CONVERTED: "metric_amount_converted",
    Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY: "metric_amount_native",
    Metric.AMOUNT_AVERAGE_UZS_CONVERTED: "metric_average_amount",
    Metric.AMOUNT_MIN: "metric_amount_min",
    Metric.AMOUNT_MAX: "metric_amount_max",
    Metric.VOLUME_SUM_BY_UNIT: "metric_volume",
    Metric.DELIVERED_VOLUME_BY_UNIT: "metric_delivered_volume",
    Metric.DELIVERED_LOTS_SUM: "metric_delivered_lots",
    Metric.UNIT_PRICE_SIMPLE_AVERAGE: "metric_unit_price_simple",
    Metric.UNIT_PRICE_WEIGHTED_AVERAGE: "metric_unit_price_weighted",
    Metric.RECORD_LOOKUP: "metric_record_lookup",
}


def locale_for(language: Language, script: Script) -> str:
    if language is Language.RU:
        return "ru"
    if script is Script.CYRILLIC:
        return "uz_cyrl"
    return "uz_latn"


def format_integer(value: int) -> str:
    return f"{value:,}".replace(",", _GROUP_SEPARATOR)


def format_decimal(
    value: Decimal,
    *,
    places: int = 2,
    strip_trailing_zeros: bool = False,
) -> str:
    """Return a display string while leaving the exact Decimal untouched.

    The SQL value is authoritative and is returned separately in ``data``.
    Rounding here is presentation only.
    """
    quantum = Decimal(1).scaleb(-places)
    rounded = value.quantize(quantum, rounding=ROUND_HALF_UP)
    text = f"{rounded:,.{places}f}"
    text = text.replace(",", _GROUP_SEPARATOR)
    text = text.replace(".", _DECIMAL_SEPARATOR)
    if strip_trailing_zeros and _DECIMAL_SEPARATOR in text:
        text = text.rstrip("0").rstrip(_DECIMAL_SEPARATOR)
    return text


def format_value(value: Decimal | int | None, kind: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        raise ValueError("boolean is not a valid metric value")
    if isinstance(value, int):
        return format_integer(value)
    if isinstance(value, Decimal):
        if kind == "count":
            return format_integer(int(value))
        if kind == "volume":
            return format_decimal(value, places=6, strip_trailing_zeros=True)
        if kind == "price":
            return format_decimal(value, places=4, strip_trailing_zeros=True)
        return format_decimal(value, places=2)
    raise ValueError(f"unsupported metric value type {type(value).__name__}")


def format_date(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10]).strftime("%d.%m.%Y")
        except ValueError:
            return text
    return text


def format_period(
    filters: list[FilterCondition], templates: dict[str, str]
) -> str | None:
    for condition in filters:
        if condition.field is not FilterField.DEAL_DATE:
            continue
        value = condition.value
        if condition.operator is Operator.BETWEEN and isinstance(value, list):
            if len(value) != 2:
                return None
            start_text, end_text = str(value[0]), str(value[1])
            try:
                start = date.fromisoformat(start_text)
                end = date.fromisoformat(end_text)
            except ValueError:
                return None
            if (
                start.month == 1
                and start.day == 1
                and end.month == 12
                and end.day == 31
                and start.year == end.year
            ):
                return templates["period_year"].format(year=start.year)
            return templates["period_from_to"].format(
                start=format_date(start), end=format_date(end)
            )
        if condition.operator is Operator.EQ:
            return templates["period_date"].format(date=format_date(value))
        if condition.operator in (Operator.GTE, Operator.LTE):
            return templates["period_date"].format(date=format_date(value))
    return None


def currency_key(currency: str | None) -> str | None:
    if currency in (CURRENCY_UZS, "UZS"):
        return "currency_UZS"
    if currency in (CURRENCY_USD, "USD"):
        return "currency_USD"
    return None


def display_currency(currency: str | None, labels: dict[str, Any]) -> str:
    if not currency:
        return ""
    key = currency_key(currency)
    if key is not None:
        return str(labels.get(key, currency))
    return currency


def display_role(role: EntityRole | None, labels: dict[str, Any]) -> str:
    key = {
        EntityRole.SELLER: "role_seller",
        EntityRole.BUYER: "role_buyer",
        EntityRole.BROKER_SELLER: "role_broker_seller",
        EntityRole.BROKER_BUYER: "role_broker_buyer",
        EntityRole.ANY: "role_any",
    }.get(role)
    if key is None:
        return str(labels.get("missing", ""))
    return str(labels.get(key, key))


def display_metric(metric: Metric, labels: dict[str, Any]) -> str:
    key = METRIC_LABEL_KEY.get(metric)
    if key is not None and key in labels:
        return str(labels[key])
    return metric.value


def display_subject(plan: QueryPlan, labels: dict[str, Any]) -> str:
    groups = set(plan.group_by)
    if plan.subject is Subject.PARTICIPANTS:
        if GroupField.SELLER_INN in groups:
            return str(labels["subject_sellers"])
        if GroupField.BUYER_INN in groups:
            return str(labels["subject_buyers"])
    if plan.subject is Subject.BROKERS:
        if GroupField.SELLER_BROKER_CODE in groups:
            return str(labels["subject_seller_brokers"])
        if GroupField.BUYER_BROKER_CODE in groups:
            return str(labels["subject_buyer_brokers"])
    if GroupField.CONTRACT_NO in groups:
        return str(labels["subject_contracts"])
    if GroupField.PRODUCT_NAME in groups:
        return str(labels["subject_products"])
    if GroupField.WAREHOUSE_REGION in groups:
        return str(labels["subject_warehouse_regions"])
    if GroupField.CURRENCY in groups:
        return str(labels["subject_currencies"])
    return str(labels["subject_groups"])


def row_label(row: ResultRow) -> str:
    for key in (
        "year",
        "month",
        "period",
        "seller_inn",
        "buyer_inn",
        "seller_broker_code",
        "buyer_broker_code",
        "warehouse_region",
        "product_name",
        "product_category",
        "product_group",
        "industry",
        "hs_code",
        "contract_no",
        "status",
        "trade_type",
        "contract_type",
        "contract_form",
        "unit_name",
        "currency",
    ):
        if key in row.labels and row.labels[key]:
            return str(row.labels[key])
        if key in row.group and row.group[key] is not None:
            return str(row.group[key])
    if row.group:
        first = next(iter(row.group.values()))
        return str(first) if first is not None else ""
    return ""


def suffix_for_row(
    metric: Metric, row: ResultRow, labels: dict[str, Any]
) -> str:
    kind = METRIC_KIND.get(metric, "record")
    if kind == "count":
        return f" {labels['unit_deals']}"
    if kind == "money":
        return f" {display_currency(row.currency, labels)}"
    if kind == "volume":
        return f" {row.unit}" if row.unit else ""
    if kind == "price":
        if row.currency and row.unit:
            return f" {display_currency(row.currency, labels)} / {row.unit}"
        if row.currency:
            return f" {display_currency(row.currency, labels)}"
        return f" / {row.unit}" if row.unit else ""
    return ""


def primary_value(rows: list[ResultRow]) -> Decimal | int | None:
    """Return a scalar only when the answer is genuinely a single value."""
    if len(rows) != 1:
        return None
    return rows[0].value


def primary_unit(rows: list[ResultRow], metric: Metric, labels: dict[str, Any]) -> str | None:
    kind = METRIC_KIND.get(metric)
    if kind == "count":
        return str(labels["unit_deals"])
    if len(rows) != 1:
        return None
    return rows[0].unit


def primary_currency(rows: list[ResultRow], metric: Metric) -> str | None:
    if len(rows) != 1:
        return None
    return rows[0].currency


def metric_kind(metric: Metric) -> str:
    return METRIC_KIND.get(metric, "record")


def first_row_label(row: ResultRow, key: str) -> str:
    value = row.labels.get(key)
    return str(value) if value else ""


def safe_text(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    return str(value) if value is not None else ""


def format_month(value: object) -> str:
    """Format a ``YYYY-MM`` group key without inventing a month name."""
    text = str(value or "").strip()
    if len(text) == 7 and text[4] == "-" and text[:4].isdigit() and text[5:].isdigit():
        return f"{text[5:7]}.{text[:4]}"
    return text


def region_display(extra: dict[str, Any], locale: str, prefix: str) -> str:
    """Pick the stored canonical region name for the answer language."""
    preferred = ("_uz", "_ru") if locale.startswith("uz") else ("_ru", "_uz")
    for suffix in preferred:
        value = extra.get(f"{prefix}{suffix}")
        if value:
            return str(value)
    raw = extra.get(f"{prefix}_raw")
    return str(raw) if raw else ""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

from app.agent.query_plan import ExecutionPlan
from app.domain.enums import ClarificationCode, Intent, Metric
from app.domain.errors import EntityNotFound
from app.handlers.base import HandlerResult, ResultRow
from app.responses.formatting import (
    display_currency,
    display_metric,
    display_subject,
    format_date,
    format_decimal,
    format_integer,
    format_month,
    format_period,
    format_value,
    metric_kind,
    region_display,
    row_label,
    suffix_for_row,
)
from app.responses.models import AnswerData, DataRow
from app.responses.templates import LocaleContext, TemplateError

CLARIFY_MESSAGE_KEY: dict[ClarificationCode, str] = {
    ClarificationCode.BROKER_SIDE_AMBIGUOUS: "clarify_broker_side",
    ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS: "clarify_participant_role",
    ClarificationCode.REGION_MEANING_AMBIGUOUS: "clarify_region_meaning",
    ClarificationCode.REGION_AMBIGUOUS: "clarify_region_meaning",
    ClarificationCode.PRODUCT_AMBIGUOUS: "clarify_product",
    ClarificationCode.PRODUCT_TOO_GENERIC: "clarify_product",
    ClarificationCode.PARTICIPANT_AMBIGUOUS: "clarify_entity",
    ClarificationCode.BROKER_AMBIGUOUS: "clarify_entity",
    ClarificationCode.CURRENCY_AMBIGUOUS: "clarify_currency",
    ClarificationCode.UNIT_AMBIGUOUS: "clarify_unit",
    ClarificationCode.PERIOD_AMBIGUOUS: "clarify_period",
}


@dataclass(frozen=True)
class FormattedAnswer:
    answer: str
    data: AnswerData


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, Decimal)) or value is None:
        return value
    if isinstance(value, bool):
        return value
    return str(value)


def _exact(value: Decimal | int | None) -> Decimal | int | None:
    return value


def _fmt(value: Any, kind: str, ctx: LocaleContext) -> str:
    if value is None:
        return ctx.label("missing")
    if isinstance(value, bool):
        raise TemplateError("boolean is not a valid display value")
    if isinstance(value, (datetime, date)):
        return format_date(value)
    if isinstance(value, (Decimal, int)):
        return format_value(value, kind)
    return str(value)


def _rows_data(rows: Iterable[ResultRow]) -> list[DataRow]:
    return [
        DataRow(
            label=row_label(row) or None,
            value=_exact(row.value),
            unit=row.unit,
            currency=row.currency,
            group={key: _json_safe(item) for key, item in row.group.items()},
            fields={key: _json_safe(item) for key, item in row.extra.items()},
        )
        for row in rows
    ]


def _group_value(key: str, value: Any) -> str:
    if value is None:
        return ""
    if key in {"year"}:
        return str(value)
    if key == "month":
        return format_month(value)
    if isinstance(value, (datetime, date)):
        return format_date(value)
    return str(value)


def _group_display(row: ResultRow) -> str:
    keys = list(row.group)
    if len(keys) == 1:
        return row_label(row) or _group_value(keys[0], row.group[keys[0]])
    parts: list[str] = []
    for key in keys:
        label = row.labels.get(key)
        if label:
            parts.append(str(label))
        else:
            parts.append(_group_value(key, row.group[key]))
    return " / ".join(part for part in parts if part)


def _grouped_text(
    result: HandlerResult,
    ctx: LocaleContext,
    *,
    period: str | None = None,
) -> str:
    metric = result.metric
    lines = [ctx.answer("grouped_header", metric=display_metric(metric, ctx.labels))]
    for row in result.rows:
        label = _group_display(row) or ctx.label("subject_groups")
        lines.append(
            ctx.answer(
                "grouped_line",
                label=label,
                value=_fmt(row.value, metric_kind(metric), ctx),
                suffix=suffix_for_row(metric, row, ctx.labels),
            )
        )
    if period:
        lines.append(period)
    return "\n".join(lines)


def _notes_for(
    metric: Metric,
    result: HandlerResult,
    ctx: LocaleContext,
    extra: list[str] | None = None,
) -> list[str]:
    notes: list[str] = []
    kind = metric_kind(metric)
    if kind in {"money", "price", "volume"}:
        notes.append(ctx.message("rounded_note"))
    if metric in (Metric.DELIVERED_VOLUME_BY_UNIT, Metric.DELIVERED_LOTS_SUM):
        notes.append(ctx.message("delivered_note"))
    if metric is Metric.AMOUNT_SUM_UZS_CONVERTED:
        notes.append(ctx.message("conversion_note"))
    if extra:
        notes.extend(extra)
    # Keep first occurrence order, never duplicate a locale sentence.
    unique: list[str] = []
    for note in notes:
        if note not in unique:
            unique.append(note)
    return unique


def _answer_data(
    result: HandlerResult,
    ctx: LocaleContext,
    *,
    period: str | None,
    value: Decimal | int | None = None,
    unit: str | None = None,
    currency: str | None = None,
    notes: list[str] | None = None,
) -> AnswerData:
    return AnswerData(
        metric=result.metric.value,
        value=value,
        unit=unit,
        currency=currency,
        method=result.currency_method,
        period=period,
        rows=_rows_data(result.rows),
        notes=notes or [],
    )


# ---------------------------------------------------------------------------
# per-intent formatters
# ---------------------------------------------------------------------------


def _format_count(
    execution: ExecutionPlan, result: HandlerResult, ctx: LocaleContext
) -> FormattedAnswer:
    period = format_period(execution.filters, ctx.answers)
    metric = result.metric
    if len(result.rows) == 1:
        row = result.rows[0]
        key = "count_with_period" if period else "count"
        text = ctx.answer(
            key,
            value=_fmt(row.value, "count", ctx),
            period=period or "",
        )
        data = _answer_data(
            result,
            ctx,
            period=period,
            value=_exact(row.value),
            unit=ctx.label("unit_deals"),
            notes=_notes_for(metric, result, ctx),
        )
        return FormattedAnswer(answer=text, data=data)
    text = _grouped_text(result, ctx, period=period)
    data = _answer_data(result, ctx, period=period, notes=_notes_for(metric, result, ctx))
    return FormattedAnswer(answer=text, data=data)


def _format_sum_amount(
    execution: ExecutionPlan, result: HandlerResult, ctx: LocaleContext
) -> FormattedAnswer:
    period = format_period(execution.filters, ctx.answers)
    metric = result.metric
    grouping = set(result.grouping)

    if metric is Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY:
        if len(result.rows) == 1:
            row = result.rows[0]
            key = "sum_amount_with_period" if period else "sum_amount"
            text = ctx.answer(
                key,
                value=_fmt(row.value, "money", ctx),
                currency=display_currency(row.currency, ctx.labels),
                period=period or "",
            )
            data = _answer_data(
                result,
                ctx,
                period=period,
                value=_exact(row.value),
                currency=row.currency,
                notes=_notes_for(metric, result, ctx),
            )
            return FormattedAnswer(answer=text, data=data)

        if grouping <= {"currency"}:
            lines = [ctx.answer("multi_currency_header")]
            for row in result.rows:
                lines.append(
                    ctx.answer(
                        "multi_currency_line",
                        currency=display_currency(row.currency, ctx.labels),
                        value=_fmt(row.value, "money", ctx),
                    )
                )
            if period:
                lines.append(period)
            text = "\n".join(lines)
            notes = _notes_for(
                metric, result, ctx, [ctx.message("multi_currency_note")]
            )
            data = _answer_data(result, ctx, period=period, notes=notes)
            return FormattedAnswer(answer=text, data=data)

        text = _grouped_text(result, ctx, period=period)
        data = _answer_data(
            result, ctx, period=period, notes=_notes_for(metric, result, ctx)
        )
        return FormattedAnswer(answer=text, data=data)

    # UZS-converted amount
    if len(result.rows) == 1:
        row = result.rows[0]
        key = "sum_amount_converted_with_period" if period else "sum_amount_converted"
        text = ctx.answer(
            key,
            value=_fmt(row.value, "money", ctx),
            period=period or "",
        )
        data = _answer_data(
            result,
            ctx,
            period=period,
            value=_exact(row.value),
            currency=row.currency or "UZS",
            notes=_notes_for(metric, result, ctx),
        )
        return FormattedAnswer(answer=text, data=data)

    text = _grouped_text(result, ctx, period=period)
    data = _answer_data(
        result, ctx, period=period, notes=_notes_for(metric, result, ctx)
    )
    return FormattedAnswer(answer=text, data=data)


def _format_volume(
    execution: ExecutionPlan, result: HandlerResult, ctx: LocaleContext
) -> FormattedAnswer:
    period = format_period(execution.filters, ctx.answers)
    metric = result.metric
    grouping = set(result.grouping)
    label = display_metric(metric, ctx.labels)

    if len(result.rows) == 1:
        row = result.rows[0]
        key = "volume_with_period" if period else "volume"
        text = ctx.answer(
            key,
            label=label,
            value=_fmt(row.value, "volume", ctx),
            unit=row.unit or "",
            period=period or "",
        )
        data = _answer_data(
            result,
            ctx,
            period=period,
            value=_exact(row.value),
            unit=row.unit,
            notes=_notes_for(metric, result, ctx),
        )
        return FormattedAnswer(answer=text, data=data)

    if grouping <= {"unit_name"}:
        lines = [ctx.answer("multi_unit_header", label=label)]
        for row in result.rows:
            lines.append(
                ctx.answer(
                    "multi_unit_line",
                    unit=row.unit or "",
                    value=_fmt(row.value, "volume", ctx),
                )
            )
        if period:
            lines.append(period)
        text = "\n".join(lines)
        notes = _notes_for(metric, result, ctx, [ctx.message("multi_unit_note")])
        data = _answer_data(result, ctx, period=period, notes=notes)
        return FormattedAnswer(answer=text, data=data)

    text = _grouped_text(result, ctx, period=period)
    data = _answer_data(
        result, ctx, period=period, notes=_notes_for(metric, result, ctx)
    )
    return FormattedAnswer(answer=text, data=data)


def _format_average(
    execution: ExecutionPlan, result: HandlerResult, ctx: LocaleContext
) -> FormattedAnswer:
    period = format_period(execution.filters, ctx.answers)
    metric = result.metric
    grouping = set(result.grouping)

    if metric is Metric.AMOUNT_AVERAGE_UZS_CONVERTED:
        if len(result.rows) == 1:
            row = result.rows[0]
            key = "average_amount_with_period" if period else "average_amount"
            text = ctx.answer(
                key,
                value=_fmt(row.value, "money", ctx),
                period=period or "",
            )
            data = _answer_data(
                result,
                ctx,
                period=period,
                value=_exact(row.value),
                currency=row.currency or "UZS",
                notes=_notes_for(metric, result, ctx),
            )
            return FormattedAnswer(answer=text, data=data)
        text = _grouped_text(result, ctx, period=period)
        data = _answer_data(
            result, ctx, period=period, notes=_notes_for(metric, result, ctx)
        )
        return FormattedAnswer(answer=text, data=data)

    method = (
        ctx.label("method_weighted")
        if metric is Metric.UNIT_PRICE_WEIGHTED_AVERAGE
        else ctx.label("method_simple")
    )
    if len(result.rows) == 1:
        row = result.rows[0]
        text = ctx.answer(
            "unit_price",
            method=method,
            value=_fmt(row.value, "price", ctx),
            currency=display_currency(row.currency, ctx.labels),
            unit=row.unit or "",
        )
        data = _answer_data(
            result,
            ctx,
            period=period,
            value=_exact(row.value),
            unit=row.unit,
            currency=row.currency,
            notes=_notes_for(metric, result, ctx),
        )
        return FormattedAnswer(answer=text, data=data)

    if grouping <= {"currency", "unit_name"}:
        lines = [ctx.answer("unit_price_header", method=method)]
        for row in result.rows:
            lines.append(
                ctx.answer(
                    "unit_price_line",
                    currency=display_currency(row.currency, ctx.labels),
                    unit=row.unit or "",
                    value=_fmt(row.value, "price", ctx),
                )
            )
        if period:
            lines.append(period)
        text = "\n".join(lines)
        data = _answer_data(
            result, ctx, period=period, notes=_notes_for(metric, result, ctx)
        )
        return FormattedAnswer(answer=text, data=data)

    text = _grouped_text(result, ctx, period=period)
    data = _answer_data(
        result, ctx, period=period, notes=_notes_for(metric, result, ctx)
    )
    return FormattedAnswer(answer=text, data=data)


def _format_min_max(
    execution: ExecutionPlan, result: HandlerResult, ctx: LocaleContext
) -> FormattedAnswer:
    period = format_period(execution.filters, ctx.answers)
    metric = result.metric
    is_min = metric is Metric.AMOUNT_MIN
    header_key = "min_header" if is_min else "max_header"
    line_key = "min_line" if is_min else "max_line"
    lines = [ctx.answer(header_key)]
    for row in result.rows:
        deal_no = row.extra.get("deal_no")
        deal_date = row.extra.get("deal_date")
        lines.append(
            ctx.answer(
                line_key,
                currency=display_currency(row.currency, ctx.labels),
                value=_fmt(row.value, "money", ctx),
                deal_no=_fmt(deal_no, "record", ctx),
                date=format_date(deal_date) if deal_date else ctx.label("missing"),
            )
        )
    if period:
        lines.append(period)
    text = "\n".join(lines)
    notes = _notes_for(
        metric, result, ctx, [ctx.message("amount_per_currency_note")]
    )
    data = _answer_data(result, ctx, period=period, notes=notes)
    return FormattedAnswer(answer=text, data=data)


def _format_top_n(
    execution: ExecutionPlan, result: HandlerResult, ctx: LocaleContext
) -> FormattedAnswer:
    period = format_period(execution.filters, ctx.answers)
    metric = result.metric
    plan = execution.plan
    limit = plan.limit or 10
    lines = [
        ctx.answer(
            "top_header",
            limit=limit,
            subject=display_subject(plan, ctx.labels),
            metric=display_metric(metric, ctx.labels),
        )
    ]
    for rank, row in enumerate(result.rows, start=1):
        lines.append(
            ctx.answer(
                "top_line",
                rank=rank,
                label=row_label(row) or ctx.label("missing"),
                value=_fmt(row.value, metric_kind(metric), ctx),
                suffix=suffix_for_row(metric, row, ctx.labels),
            )
        )
    if period:
        lines.append(period)
    text = "\n".join(lines)
    notes = _notes_for(metric, result, ctx)
    if metric is Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY:
        notes.append(ctx.message("multi_currency_note"))
    data = _answer_data(result, ctx, period=period, notes=notes)
    return FormattedAnswer(answer=text, data=data)


def _format_compare(
    execution: ExecutionPlan, result: HandlerResult, ctx: LocaleContext
) -> FormattedAnswer:
    metric = result.metric
    plan = execution.plan
    order: dict[str, int] = {}
    if plan.comparison is not None:
        order[plan.comparison.left.label] = 0
        order[plan.comparison.right.label] = 1
    rows = sorted(
        result.rows,
        key=lambda row: (
            order.get(str(row.group.get("period", "")), 99),
            str(row.group.get("currency", "")),
            str(row.group.get("unit_name", "")),
        ),
    )
    lines = [ctx.answer("compare_header", metric=display_metric(metric, ctx.labels))]
    for row in rows:
        label = str(row.group.get("period", "")) or ctx.label("missing")
        absent = row.value is None and row.extra.get("matched_rows") == 0
        if absent:
            lines.append(ctx.answer("compare_absent", period=label))
            continue
        lines.append(
            ctx.answer(
                "compare_line",
                period=label,
                value=_fmt(row.value, metric_kind(metric), ctx),
                suffix=suffix_for_row(metric, row, ctx.labels),
            )
        )
    text = "\n".join(lines)
    notes = _notes_for(metric, result, ctx, [ctx.message("compare_note")])
    data = AnswerData(
        metric=metric.value,
        method=result.currency_method,
        rows=_rows_data(rows),
        notes=notes,
    )
    return FormattedAnswer(answer=text, data=data)


def _period_text(row: ResultRow, ctx: LocaleContext) -> str:
    if "year" in row.group and row.group["year"] is not None:
        return ctx.answer("period_year", year=row.group["year"])
    if "month" in row.group and row.group["month"] is not None:
        return format_month(row.group["month"])
    if "period" in row.group and row.group["period"] is not None:
        return str(row.group["period"])
    return row_label(row) or ctx.label("missing")


def _format_trend(
    execution: ExecutionPlan, result: HandlerResult, ctx: LocaleContext
) -> FormattedAnswer:
    period = format_period(execution.filters, ctx.answers)
    metric = result.metric
    lines = [ctx.answer("trend_header", metric=display_metric(metric, ctx.labels))]
    for row in result.rows:
        lines.append(
            ctx.answer(
                "trend_line",
                period=_period_text(row, ctx),
                value=_fmt(row.value, metric_kind(metric), ctx),
                suffix=suffix_for_row(metric, row, ctx.labels),
            )
        )
    if period:
        lines.append(period)
    text = "\n".join(lines)
    notes = _notes_for(metric, result, ctx, [ctx.message("trend_note")])
    data = _answer_data(result, ctx, period=period, notes=notes)
    return FormattedAnswer(answer=text, data=data)


def _product_name(extra: dict[str, Any], ctx: LocaleContext) -> str:
    for key in ("deal_product_name", "product_name"):
        value = extra.get(key)
        if value:
            return str(value)
    return ctx.label("missing")


def _deal_line(extra: dict[str, Any], ctx: LocaleContext) -> str:
    delivery_date = extra.get("delivery_date")
    return ctx.answer(
        "deal_line",
        deal_no=_fmt(extra.get("deal_no"), "record", ctx),
        date=format_date(extra.get("deal_date"))
        if extra.get("deal_date")
        else ctx.label("missing"),
        product=_product_name(extra, ctx),
        quantity=_fmt(extra.get("quantity_lots"), "volume", ctx),
        delivered=_fmt(extra.get("delivered_lots"), "volume", ctx),
        delivery_date=format_date(delivery_date)
        if delivery_date
        else ctx.label("missing"),
        unit=extra.get("unit_name") or "",
        amount=_fmt(extra.get("deal_amount"), "money", ctx),
        currency=display_currency(extra.get("currency"), ctx.labels),
        status=extra.get("status") or ctx.label("missing"),
    )


def _format_filter_list(
    execution: ExecutionPlan, result: HandlerResult, ctx: LocaleContext
) -> FormattedAnswer:
    shown = len(result.rows)
    lines = [
        ctx.answer(
            "filter_list_header",
            total=format_integer(result.matched_rows),
            shown=format_integer(shown),
        )
    ]
    for row in result.rows:
        lines.append(_deal_line(row.extra, ctx))
    text = "\n".join(lines)
    notes: list[str] = []
    if shown < result.matched_rows:
        notes.append(
            ctx.answer(
                "filter_list_more",
                shown=format_integer(shown),
                total=format_integer(result.matched_rows),
            )
        )
    data = _answer_data(result, ctx, period=None, notes=notes)
    return FormattedAnswer(answer=text, data=data)


def _lookup_text_and_rows(
    header_key: str,
    line_builder: Any,
) -> Any:
    def build(
        execution: ExecutionPlan, result: HandlerResult, ctx: LocaleContext
    ) -> FormattedAnswer:
        lines = [ctx.answer(header_key)]
        for row in result.rows:
            lines.append(line_builder(row.extra, ctx))
        text = "\n".join(lines)
        data = _answer_data(result, ctx, period=None)
        return FormattedAnswer(answer=text, data=data)

    return build


def _contract_line(extra: dict[str, Any], ctx: LocaleContext) -> str:
    return ctx.answer(
        "contract_line",
        contract_no=_fmt(extra.get("contract_no"), "record", ctx),
        product=extra.get("product_name") or ctx.label("missing"),
        seller=extra.get("seller_name") or ctx.label("missing"),
        currency=display_currency(extra.get("currency"), ctx.labels),
        price=_fmt(extra.get("base_price"), "money", ctx),
    )


def _participant_line(extra: dict[str, Any], ctx: LocaleContext) -> str:
    return ctx.answer(
        "participant_line",
        name=extra.get("name") or ctx.label("missing"),
        inn=_fmt(extra.get("inn"), "record", ctx),
        region=region_display(extra, ctx.locale, "company_region")
        or ctx.label("missing"),
        role=extra.get("role") or ctx.label("missing"),
    )


def _broker_line(extra: dict[str, Any], ctx: LocaleContext) -> str:
    return ctx.answer(
        "broker_line",
        name=extra.get("name") or ctx.label("missing"),
        code=_fmt(extra.get("broker_code"), "record", ctx),
        inn=_fmt(extra.get("inn"), "record", ctx),
        region=region_display(extra, ctx.locale, "region") or ctx.label("missing"),
    )


_FORMATTERS = {
    Intent.COUNT: _format_count,
    Intent.SUM_AMOUNT: _format_sum_amount,
    Intent.SUM_VOLUME: _format_volume,
    Intent.AVERAGE: _format_average,
    Intent.MIN_MAX: _format_min_max,
    Intent.TOP_N: _format_top_n,
    Intent.COMPARE: _format_compare,
    Intent.TREND: _format_trend,
    Intent.FILTER_LIST: _format_filter_list,
    Intent.DEAL_LOOKUP: _lookup_text_and_rows("lookup_header_deal", _deal_line),
    Intent.CONTRACT_LOOKUP: _lookup_text_and_rows(
        "lookup_header_contract", _contract_line
    ),
    Intent.PARTICIPANT_LOOKUP: _lookup_text_and_rows(
        "lookup_header_participant", _participant_line
    ),
    Intent.BROKER_LOOKUP: _lookup_text_and_rows("lookup_header_broker", _broker_line),
}


def format_found(
    execution: ExecutionPlan,
    result: HandlerResult,
    ctx: LocaleContext,
) -> FormattedAnswer:
    formatter = _FORMATTERS.get(execution.plan.intent)
    if formatter is None:
        raise TemplateError(
            f"no deterministic formatter for intent {execution.plan.intent.value}"
        )
    return formatter(execution, result, ctx)


# ---------------------------------------------------------------------------
# non-FOUND messages
# ---------------------------------------------------------------------------


def format_not_found(
    ctx: LocaleContext,
    *,
    entity: EntityNotFound | None = None,
    no_value: bool = False,
) -> str:
    if entity is not None:
        kind = ctx.labels.get(f"entity_{entity.kind}", entity.kind)
        return ctx.message(
            "not_found_entity", kind=str(kind), mention=entity.mention
        )
    if no_value:
        return ctx.message("no_value")
    return ctx.message("not_found")


def format_clarification(
    ctx: LocaleContext,
    code: ClarificationCode,
    options: list[str] | None = None,
) -> str:
    key = CLARIFY_MESSAGE_KEY.get(code, "clarify_entity")
    text = ctx.message(key)
    if options:
        rendered = "; ".join(str(option) for option in options[:10])
        text = f"{text} {ctx.message('options_prefix')} {rendered}"
    return text


def format_unsupported(ctx: LocaleContext) -> str:
    return ctx.message("unsupported")


def format_unverified(ctx: LocaleContext) -> str:
    return ctx.message("unverified")


def format_general_unavailable(ctx: LocaleContext) -> str:
    """A technical general-AI failure, never a registry "no data" outcome."""

    return ctx.message("general_unavailable")

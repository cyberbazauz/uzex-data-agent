from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.config import settings
from app.database.connection import get_active_dataset, readonly_connection
from app.language import SUPPORTED_LOCALES
from app.responses.models import (
    ExampleQuestion,
    ExamplesResponse,
    MetaResponse,
)

router = APIRouter(tags=["metadata"])

_EXAMPLES = (
    ExampleQuestion(
        question="Сколько сделок было в 2024 году?",
        language="ru",
        topic="count",
    ),
    ExampleQuestion(
        question="Какая сумма сделок в долларах?",
        language="ru",
        topic="sum_amount",
    ),
    ExampleQuestion(
        question="Сколько сделок по товару Карбамид?",
        language="ru",
        topic="count_filtered",
    ),
    ExampleQuestion(
        question="Покажи сделку 100001",
        language="ru",
        topic="deal_lookup",
    ),
    ExampleQuestion(
        question="Динамика количества сделок по годам",
        language="ru",
        topic="trend",
    ),
    ExampleQuestion(
        question="Топ-5 продавцов по сумме сделок",
        language="ru",
        topic="top_n",
    ),
    ExampleQuestion(
        question="2024-yilda nechta bitim bo'ldi?",
        language="uz",
        topic="count",
    ),
    ExampleQuestion(
        question="2024-йилда нечта битим бўлди?",
        language="uz",
        topic="count",
    ),
)


def _row_count(counts: dict[str, Any], name: str) -> int | None:
    value = counts.get(f"{name}.csv")
    return int(value) if isinstance(value, int) else None


@router.get("/meta", response_model=MetaResponse)
def meta() -> MetaResponse:
    dataset: dict[str, Any] | None = None
    try:
        with readonly_connection() as conn:
            dataset = get_active_dataset(conn)
    except Exception:  # noqa: BLE001 - public metadata must stay safe
        dataset = None

    if not dataset:
        return MetaResponse(
            status="unavailable",
            supported_languages=["ru", "uz"],
            supported_locales=list(SUPPORTED_LOCALES),
            planner_mode=settings.planner_mode,
        )

    counts = dataset.get("row_counts") or {}
    if not isinstance(counts, dict):
        counts = {}
    deal_files = [
        value for key, value in counts.items() if str(key).startswith("deals")
    ]
    total_deals = (
        int(sum(deal_files))
        if deal_files and all(isinstance(value, int) for value in deal_files)
        else None
    )

    return MetaResponse(
        status="ready",
        dataset_version=str(dataset["dataset_version"]),
        dataset_cutoff=str(dataset["max_deal_date"]),
        dataset_loaded_at=str(dataset.get("imported_at")) if dataset.get("imported_at") else None,
        total_deals=total_deals,
        contracts=_row_count(counts, "contracts"),
        participants=_row_count(counts, "participants"),
        brokers=_row_count(counts, "brokers"),
        usd_rates=_row_count(counts, "usd_rates"),
        supported_languages=["ru", "uz"],
        supported_locales=list(SUPPORTED_LOCALES),
        planner_mode=settings.planner_mode,
    )


@router.get("/examples", response_model=ExamplesResponse)
def examples() -> ExamplesResponse:
    items = list(_EXAMPLES)
    if settings.planner_mode == "llm" and settings.general_mode_enabled:
        # General AI Mode is only reachable through an LLM planner; the local
        # deterministic planner never emits GENERAL.
        items.append(
            ExampleQuestion(
                question="Что такое экспорт?",
                language="ru",
                topic="general",
            )
        )
    return ExamplesResponse(
        examples=items,
        note=(
            "Это только примеры формулировок, а не список разрешённых вопросов. "
            "Факты о сделках рассчитываются по активному набору данных PostgreSQL; "
            "общие вопросы получают ответ из общих знаний и не проверяются по реестру."
        ),
    )

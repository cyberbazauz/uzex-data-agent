from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.agent.orchestrator import Orchestrator  # noqa: E402

QUESTIONS = [
    "Сколько сделок было в 2024 году?",
    "Какая сумма сделок в долларах?",
    "Покажи сделку 100001",
    "Покажи контракт 100001",
    "Топ-5 продавцов по сумме сделок",
    "Динамика количества сделок по годам",
    "Сколько сделок было в 2027 году?",
    "Сколько сделок по товару Карбамид?",
    "Спрогнозируй количество сделок в 2027 году",
    "Какая прибыль от сделок?",
    "Сколько сделок было в Ташкенте?",
    "2024-yilda nechta bitim bo'ldi?",
    "2024-йилда нечта битим бўлди?",
    "Покажи брокера BK002",
    "Покажи участника \"Diyi Un\" XK",
    "Средняя цена карбамида",
]

orchestrator = Orchestrator()
for question in QUESTIONS:
    response = orchestrator.chat(question)
    print("=" * 70)
    print(question)
    print(response.status.value, response.locale)
    print(response.answer)
    if response.data is not None:
        print("DATA:", response.data.model_dump_json())
    if response.clarification is not None:
        print("CLARIFY:", response.clarification.model_dump_json())
    if response.evidence is not None:
        print(
            "EVIDENCE:",
            json.dumps(
                {
                    "tables": response.evidence.source_tables,
                    "metric": response.evidence.metric,
                    "rows": response.evidence.matched_rows,
                    "grouping": response.evidence.grouping,
                },
                ensure_ascii=False,
            ),
        )

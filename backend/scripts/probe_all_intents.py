from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.agent.orchestrator import Orchestrator  # noqa: E402

QUESTIONS = [
    "Сколько всего сделок?",
    "Сумма сделок в 2024 году",
    "Сумма сделок в сумах",
    "Сумма сделок в валюте контракта",
    "Сумма сделок по годам",
    "Объём сделок по товару Карбамид",
    "Объём по единицам измерения",
    "Средняя сумма сделки",
    "Средняя цена карбамида",
    "Минимальная сумма сделки",
    "Максимальная сумма сделки в 2024",
    "Топ-5 продавцов по количеству сделок",
    "Топ-5 покупателей по сумме сделок",
    "Топ-5 брокеров продавца по количеству сделок",
    "Сравни 2023 и 2024 годы по количеству сделок",
    "Сравни сумму сделок за 2023 и 2024",
    "Сравни объём поставок за 2023 и 2024",
    "Динамика сделок по годам",
    "Покажи последние 10 сделок",
    "Сколько сделок по статусу Исполнен",
    "Сколько экспортных сделок",
    "Сколько внутренних сделок в 2024",
    "Сколько сделок у продавца с ИНН 937772675",
    "Сколько сделок по региону Ташкентская область",
    "Топ-5 продавцов из Ташкентской области по количеству сделок",
]

orchestrator = Orchestrator()
failures = 0
for question in QUESTIONS:
    response = orchestrator.chat(question)
    print("=" * 70)
    print(question)
    print(response.status.value)
    print(response.answer)
    if response.status.value == "UNVERIFIED":
        failures += 1
print("UNVERIFIED count:", failures)

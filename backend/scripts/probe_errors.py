from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.agent.orchestrator import Orchestrator  # noqa: E402

orchestrator = Orchestrator()
for question in [
    "Покажи сделку 999999",
    "Сколько сделок у компании sujo plus mchj",
    "Сколько сделок у продавца с ИНН 000000000",
    "Покажи участника Несуществующая Компания",
    "Покажи контракт 999999",
    "Покажи брокера BK999",
    "Сколько сделок было в 2027 году?",
    "Сколько сделок по товару НесуществующийТовар",
]:
    response = orchestrator.chat(question)
    print("=" * 60)
    print(question)
    print(response.status.value)
    print(response.answer)
    print("evidence rows:", response.evidence.matched_rows if response.evidence else None)

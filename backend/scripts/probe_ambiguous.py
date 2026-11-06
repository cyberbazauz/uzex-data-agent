from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.agent.orchestrator import Orchestrator  # noqa: E402

orchestrator = Orchestrator()
for question in [
    "Покажи участника sujo plus mchj",
    "Сколько сделок у продавца sujo plus mchj",
    "Сколько сделок у покупателя sujo plus mchj",
    "Покажи брокера KAHIR",
]:
    response = orchestrator.chat(question)
    print("=" * 60)
    print(question)
    print(response.status.value)
    print(response.answer)
    print(response.clarification.model_dump_json() if response.clarification else "")

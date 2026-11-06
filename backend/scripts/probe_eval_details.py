from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.agent.planner import LocalPlannerAdapter  # noqa: E402
from app.database.connection import readonly_connection  # noqa: E402
from app.entities.resolver import EntityResolver  # noqa: E402
from app.handlers.dispatch import execute  # noqa: E402
from app.language import detect_language  # noqa: E402

QUESTIONS = [
    "Сумма экспортных сделок",
    "Сумма внутренних сделок в 2024",
    "Объём экспортных сделок",
    "Объём внутренних сделок в 2024",
    "Shartnoma raqami 100001",
    "Сколько сделок было 01.01.2024",
    "Сколько сделок по статусу Не подписан в 2024",
]

planner = LocalPlannerAdapter()
for question in QUESTIONS:
    detection = detect_language(question)
    plan = planner.plan(question, detection)
    print("=" * 80)
    print(question)
    print("DECISION", plan.decision.value, "INTENT", plan.intent.value if plan.intent else None)
    print("FILTERS", [item.model_dump() for item in plan.filters])
    print("ENTITIES", [item.model_dump() for item in plan.entity_candidates])
    if plan.decision.value != "EXECUTE":
        continue
    with readonly_connection() as conn:
        execution = EntityResolver(conn).resolve(plan)
        print("EXEC FILTERS", [item.model_dump() for item in execution.filters])
        result = execute(conn, execution, "probe")
        print("ROW COUNT", len(result.rows), "MATCHED", result.matched_rows)
        print("ROWS", [row.model_dump() for row in result.rows][:5])

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.agent.orchestrator import Orchestrator  # noqa: E402
from app.agent.planner import LocalPlannerAdapter  # noqa: E402
from app.agent.query_plan import ExecutionPlan  # noqa: E402
from app.database.connection import get_active_dataset, readonly_connection  # noqa: E402
from app.entities.resolver import EntityResolver  # noqa: E402
from app.handlers.dispatch import execute  # noqa: E402
from app.language import detect_language  # noqa: E402
from app.responses.formatter import format_found  # noqa: E402
from app.responses.templates import context_for  # noqa: E402

question = "Динамика количества сделок по годам"
detection = detect_language(question)
plan = LocalPlannerAdapter().plan(question, detection)
print(plan.model_dump_json(indent=2))
with readonly_connection() as conn:
    execution = EntityResolver(conn).resolve(plan)
    print("EXECUTION FILTERS:", [f.model_dump() for f in execution.filters])
    result = execute(conn, execution, "probe")
    print(result.model_dump_json(indent=2)[:4000])
    ctx = context_for(detection.language, detection.script)
    try:
        formatted = format_found(execution, result, ctx)
        print(formatted.answer)
    except Exception:
        traceback.print_exc()

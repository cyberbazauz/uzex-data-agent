from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.agent.planner import LocalPlannerAdapter  # noqa: E402
from app.language import detect_language  # noqa: E402

planner = LocalPlannerAdapter()
for question in [
    "Shartnoma raqami 100001",
    "Shartnoma 100001",
    "Контракт 100001",
    "Битим рақами 100001",
    "Сделка 100001",
]:
    detection = detect_language(question)
    plan = planner.plan(question, detection)
    print("=" * 70)
    print(question, detection.locale)
    print(plan.model_dump_json(indent=2))

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_QUALIFICATION_PATH = Path(__file__).resolve().parent / "qualification.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"providers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"providers": {}}
    if not isinstance(data, dict):
        return {"providers": {}}
    return data


def qualification_path() -> Path:
    override = os.getenv("LLM_QUALIFICATION_PATH", "").strip()
    return Path(override) if override else DEFAULT_QUALIFICATION_PATH


def provider_status(provider: str, model: str) -> dict[str, Any] | None:
    data = _load(qualification_path())
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return None
    key = f"{provider.strip().lower()}:{model.strip()}"
    entry = providers.get(key)
    return entry if isinstance(entry, dict) else None


def is_qualified(provider: str, model: str) -> bool:
    """Return True only for a provider/model with a recorded PASS report.

    An entry must explicitly say ``status: qualified``.  A missing report, an
    expired report or a different model is treated as unqualified.
    """

    entry = provider_status(provider, model)
    if not entry:
        return False
    if str(entry.get("status", "")).strip().lower() != "qualified":
        return False
    # A report is bound to an evaluation dataset version; an operator must
    # regenerate it after a benchmark change instead of reusing a stale PASS.
    if not entry.get("report") or not entry.get("benchmark_version"):
        return False
    return True

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from app.database.connection import check_database_ready, readonly_connection
from app.responses.models import HealthResponse, ReadyResponse, ReadinessChecks

router = APIRouter(tags=["health"])

VERSION = "1.0.0"

_REQUIRED_TABLES = (
    "public.dataset_meta",
    "public.deals",
    "public.contracts",
    "public.participants",
    "public.brokers",
    "public.regions",
    "public.usd_rates",
)


def _schema_ready() -> bool:
    try:
        with readonly_connection() as conn:
            checks = " AND ".join(
                f"to_regclass('{table}') IS NOT NULL" for table in _REQUIRED_TABLES
            )
            return bool(conn.execute(text(f"SELECT {checks}")).scalar_one())
    except Exception:  # noqa: BLE001 - readiness must never leak internals
        return False


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        version=VERSION,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/ready", response_model=ReadyResponse)
def ready() -> ReadyResponse:
    info = check_database_ready()
    database_ok = bool(info.get("ready"))
    dataset_ok = database_ok and bool(info.get("dataset_version"))
    schema_ok = database_ok and _schema_ready()
    is_ready = database_ok and dataset_ok and schema_ok
    return ReadyResponse(
        status="ready" if is_ready else "not_ready",
        checks=ReadinessChecks(
            database=database_ok,
            dataset=dataset_ok,
            schema_ready=schema_ok,
        ),
        dataset_version=str(info["dataset_version"]) if info.get("dataset_version") else None,
        dataset_cutoff=str(info["dataset_cutoff"]) if info.get("dataset_cutoff") else None,
    )

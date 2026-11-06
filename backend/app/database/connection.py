from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection

from app.config import settings

_engines: dict[str, Engine] = {}


def get_engine(readonly: bool = True) -> Engine:
    key = "reader" if readonly else "owner"
    if key not in _engines:
        url = settings.database_readonly_url if readonly else settings.database_url
        # statement_timeout is a server-side guard; it does not rely on client
        # cooperation after the query has been sent.
        connect_args: dict[str, Any] = {
            "connect_timeout": 5,
            "application_name": f"uzex-data-agent-{key}",
            "options": (
                f"-c statement_timeout={settings.sql_timeout_ms}"
                if readonly
                else "-c statement_timeout=0"
            ),
        }
        if readonly:
            connect_args["options"] += " -c default_transaction_read_only=on"
        _engines[key] = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_recycle=1800,
            connect_args=connect_args,
        )
    return _engines[key]


@contextmanager
def readonly_connection() -> Iterator[Connection]:
    engine = get_engine(readonly=True)
    with engine.connect() as conn:
        yield conn


@contextmanager
def owner_connection() -> Iterator[Connection]:
    engine = get_engine(readonly=False)
    with engine.connect() as conn:
        yield conn


def get_active_dataset(conn: Connection) -> dict[str, Any] | None:
    row = conn.execute(
        text(
            """
            SELECT dataset_version, imported_at, row_counts, min_deal_date,
                   max_deal_date, validation_status, schema_version,
                   dictionary_version, file_checksums
            FROM dataset_meta
            WHERE is_active = true
            LIMIT 1
            """
        )
    ).mappings().first()
    return dict(row) if row else None


def check_database_ready() -> dict[str, Any]:
    """Return readiness information without exposing connection details."""
    try:
        with readonly_connection() as conn:
            active = get_active_dataset(conn)
            if not active:
                return {"ready": False, "reason": "no_active_dataset"}
            if active["validation_status"] != "PASSED":
                return {"ready": False, "reason": "dataset_not_validated"}
            return {
                "ready": True,
                "dataset_version": active["dataset_version"],
                "dataset_cutoff": str(active["max_deal_date"]),
            }
    except Exception as exc:  # noqa: BLE001 - readiness must never leak details
        return {"ready": False, "reason": "database_unavailable", "error_type": type(exc).__name__}

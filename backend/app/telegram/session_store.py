from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    chat_id       INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    last_question TEXT    NOT NULL,
    last_status   TEXT    NOT NULL,
    last_metric   TEXT,
    last_period   TEXT,
    updated_at    TEXT    NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SessionStore:
    """Durable, per-user conversation state.

    State is keyed by ``(chat_id, user_id)`` so a group chat can never leak one
    participant's context into another participant's answer.  SQLite keeps the
    bot independent from the read-only PostgreSQL role that serves facts.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._connection = sqlite3.connect(self._path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.executescript(_SCHEMA)
            self._connection.commit()

    def get_context(self, chat_id: int, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT last_question, last_status, last_metric, last_period,
                       updated_at
                FROM sessions
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def save_context(
        self,
        chat_id: int,
        user_id: int,
        *,
        question: str,
        status: str,
        metric: str | None = None,
        period: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO sessions (
                    chat_id, user_id, last_question, last_status, last_metric,
                    last_period, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    last_question = excluded.last_question,
                    last_status   = excluded.last_status,
                    last_metric   = excluded.last_metric,
                    last_period   = excluded.last_period,
                    updated_at    = excluded.updated_at
                """,
                (chat_id, user_id, question, status, metric, period, now),
            )
            self._connection.commit()

    def reset(self, chat_id: int, user_id: int) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM sessions WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            self._connection.commit()

    def get_offset(self) -> int | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM bot_state WHERE key = 'update_offset'"
            ).fetchone()
        if row is None:
            return None
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return None

    def set_offset(self, offset: int) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO bot_state (key, value) VALUES ('update_offset', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(offset),),
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

"""Telegram long-polling adapter.

The adapter is deliberately thin: every question goes through the same
``Orchestrator`` used by the HTTP API.  No business rule, SQL handler or
formatting guarantee is re-implemented here.
"""

from app.telegram.session_store import SessionStore

__all__ = ["SessionStore"]

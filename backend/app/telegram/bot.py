from __future__ import annotations

import logging
import os
import random
import signal
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, text

from app.agent.orchestrator import Orchestrator
from app.config import Settings
from app.telegram.handlers import NON_TEXT_TEXT, TelegramHandlers
from app.telegram.session_store import SessionStore

logger = logging.getLogger("uzex.telegram")

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
LOCK_KEY = 921_754_321
HEARTBEAT_ENV = "TELEGRAM_HEARTBEAT_PATH"
STATE_DB_ENV = "TELEGRAM_STATE_DB"


class TelegramAPIError(RuntimeError):
    def __init__(self, code: int | None, method: str) -> None:
        # Only the numeric code and the method are retained.  The provider
        # payload is never logged, so a token or user text cannot leak.
        super().__init__(f"telegram_api_error code={code} method={method}")
        self.code = code
        self.method = method


class TelegramClient:
    def __init__(self, token: str, base_url: str = "https://api.telegram.org") -> None:
        self._client = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/bot{token}",
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
        )

    def call(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        read_timeout: float | None = None,
    ) -> Any:
        timeout = None
        if read_timeout is not None:
            timeout = httpx.Timeout(
                connect=10.0, read=read_timeout, write=10.0, pool=10.0
            )
        response = self._client.post(method, json=payload or {}, timeout=timeout)
        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramAPIError(response.status_code, method) from exc
        if not data.get("ok"):
            raise TelegramAPIError(data.get("error_code"), method)
        return data.get("result")

    def get_me(self) -> dict[str, Any]:
        return self.call("getMe")

    def get_webhook_info(self) -> dict[str, Any]:
        return self.call("getWebhookInfo")

    def delete_webhook(self) -> Any:
        return self.call("deleteWebhook", {"drop_pending_updates": False})

    def get_updates(self, offset: int | None, poll_timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": poll_timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self.call("getUpdates", payload, read_timeout=poll_timeout + 15)
        return list(result or [])

    def send_message(self, chat_id: int, text: str) -> Any:
        return self.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )

    def close(self) -> None:
        self._client.close()


class PollingLock:
    """Durable single-instance lock shared by every bot replica."""

    def __init__(self, database_url: str, key: int = LOCK_KEY) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._key = key
        self._connection = None

    def acquire(self) -> bool:
        self._connection = self._engine.connect()
        granted = self._connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": self._key}
        ).scalar()
        return bool(granted)

    def release(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": self._key}
            )
        except Exception:  # noqa: BLE001 - shutdown must not raise
            logger.warning("telegram_lock_release_failed")
        finally:
            self._connection.close()
            self._connection = None


def _touch_heartbeat(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        logger.warning("telegram_heartbeat_write_failed")


def _process_update(
    update: dict[str, Any],
    *,
    client: TelegramClient,
    handlers: TelegramHandlers,
) -> None:
    message = update.get("message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = sender.get("id")
    if not isinstance(chat_id, int) or not isinstance(user_id, int):
        return

    text = message.get("text")
    if not isinstance(text, str):
        client.send_message(chat_id, NON_TEXT_TEXT)
        return

    replies = handlers.handle(chat_id, user_id, text)
    for index, reply in enumerate(replies):
        result = client.send_message(chat_id, reply)
        message_id = result.get("message_id") if isinstance(result, dict) else None
        logger.info(
            "telegram_reply_sent part=%s of=%s message_id=%s",
            index + 1,
            len(replies),
            message_id,
        )
        if index + 1 < len(replies):
            time.sleep(0.05)


def run(stop: threading.Event) -> int:
    token = os.getenv(TOKEN_ENV, "").strip()
    if not token:
        logger.error("telegram_token_missing")
        return 2

    settings = Settings.from_env()
    state_db = os.getenv(STATE_DB_ENV, "/app/runtime/telegram_state.sqlite3")
    heartbeat = Path(
        os.getenv(HEARTBEAT_ENV, "/app/runtime/telegram.heartbeat")
    )
    poll_timeout = int(os.getenv("TELEGRAM_POLL_TIMEOUT", "30"))
    max_input = int(os.getenv("TELEGRAM_MAX_INPUT_CHARS", "1000"))
    rate_limit = int(os.getenv("TELEGRAM_RATE_LIMIT_PER_MINUTE", "20"))

    store = SessionStore(state_db)
    lock = PollingLock(settings.database_readonly_url)
    if not lock.acquire():
        logger.error("telegram_polling_lock_held")
        store.close()
        return 0

    client = TelegramClient(token)
    try:
        identity = client.get_me()
        username = identity.get("username")
        logger.info("telegram_bot_identity username=%s", username)

        webhook = client.get_webhook_info()
        if webhook.get("url"):
            client.delete_webhook()
            logger.info("telegram_webhook_removed")

        handlers = TelegramHandlers(
            orchestrator=Orchestrator(settings=settings),
            store=store,
            max_input_chars=max_input,
            rate_limit_per_minute=rate_limit,
        )

        offset = store.get_offset()
        attempt = 0
        _touch_heartbeat(heartbeat)
        logger.info("telegram_polling_started")

        while not stop.is_set():
            try:
                updates = client.get_updates(offset, poll_timeout)
                for update in updates:
                    update_id = update.get("update_id")
                    try:
                        _process_update(
                            update, client=client, handlers=handlers
                        )
                    except TelegramAPIError as exc:
                        # A blocked/removed chat must not trap the offset and
                        # create an infinite retry loop.
                        logger.warning(
                            "telegram_update_failed code=%s method=%s",
                            exc.code,
                            exc.method,
                        )
                    if isinstance(update_id, int):
                        offset = update_id + 1
                        store.set_offset(offset)
                        logger.info(
                            "telegram_update_processed update_id=%s", update_id
                        )
                attempt = 0
                _touch_heartbeat(heartbeat)
            except TelegramAPIError as exc:
                if exc.code == 401:
                    logger.error("telegram_unauthorized")
                    return 2
                if exc.code == 409:
                    logger.error("telegram_conflict_other_poller")
                    return 3
                attempt += 1
                delay = min(30.0, 2.0**min(attempt, 5)) + random.uniform(0, 0.5)
                logger.warning(
                    "telegram_api_retry code=%s method=%s delay=%.1f",
                    exc.code,
                    exc.method,
                    delay,
                )
                stop.wait(delay)
            except (httpx.HTTPError, OSError) as exc:
                attempt += 1
                delay = min(30.0, 2.0**min(attempt, 5)) + random.uniform(0, 0.5)
                logger.warning(
                    "telegram_network_retry error_type=%s delay=%.1f",
                    type(exc).__name__,
                    delay,
                )
                stop.wait(delay)
        logger.info("telegram_polling_stopped")
        return 0
    finally:
        client.close()
        lock.release()
        store.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx logs the full request URL at INFO level.  For the Telegram API
    # that URL embeds the bot token, so it must never reach the logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    stop = threading.Event()

    def _stop(signum: int, _frame: object) -> None:
        logger.info("telegram_signal_received signal=%s", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return run(stop)


if __name__ == "__main__":
    raise SystemExit(main())

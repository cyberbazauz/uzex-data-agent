from __future__ import annotations

import time
from collections import defaultdict, deque
from uuid import uuid4

from app.agent.orchestrator import Orchestrator
from app.responses.models import ChatResponse
from app.telegram.message_formatter import format_response, split_text
from app.telegram.session_store import SessionStore

START_TEXT = (
    "Здравствуйте! Я — UZEX Data Agent.\n\n"
    "Задайте вопрос обычным текстом: статистика сделок, суммы, объёмы, "
    "рейтинги, динамика, поиск по номеру договора или ИНН.\n\n"
    "Языки: русский, узбекский (латиница и кириллица).\n"
    "Все факты берутся только из PostgreSQL и сопровождаются evidence.\n\n"
    "Salom! Savolingizni oddiy matn bilan yozing. "
    "Faktlar faqat PostgreSQL bazasidan olinadi."
)

HELP_TEXT = (
    "Примеры вопросов:\n"
    "• Сколько сделок было в 2024 году?\n"
    "• Покажи динамику сделок по годам\n"
    "• 2024-yilda nechta bitim bo'ldi?\n"
    "• 2024-йилда нечта битим бўлди?\n"
    "• Топ-10 участников по сумме сделок\n\n"
    "Команды:\n"
    "/start — приветствие\n"
    "/help — эта справка\n"
    "/reset — очистить только ваш контекст\n"
    "/language — как определяется язык"
)

LANGUAGE_TEXT = (
    "Язык ответа определяется автоматически по последнему вопросу: "
    "русский, узбекская латиница или узбекская кириллица. "
    "Названия компаний, ИНН и номера договоров не переводятся."
)

RESET_TEXT = "Ваш контекст очищен. / Kontekst tozalandi."
RATE_LIMIT_TEXT = (
    "Слишком много запросов. Подождите минуту и повторите. "
    "/ Juda ko'p so'rov. Bir daqiqadan so'ng qayta urinib ko'ring."
)
TOO_LONG_TEXT = (
    "Вопрос слишком длинный. Сократите его, пожалуйста. "
    "/ Savol juda uzun."
)
NON_TEXT_TEXT = (
    "Поддерживаются только текстовые вопросы. "
    "/ Faqat matnli savollar qabul qilinadi."
)
UNKNOWN_COMMAND_TEXT = (
    "Неизвестная команда. Используйте /help. / Noma'lum buyruq."
)

class TelegramHandlers:
    """Route Telegram messages into the shared orchestrator.

    No SQL, no planner call and no formatting logic lives here.  The adapter
    only manages commands, limits and per-user context.
    """

    def __init__(
        self,
        *,
        orchestrator: Orchestrator,
        store: SessionStore,
        max_input_chars: int = 1000,
        rate_limit_per_minute: int = 20,
    ) -> None:
        self._orchestrator = orchestrator
        self._store = store
        self._max_input_chars = max_input_chars
        self._rate_limit = rate_limit_per_minute
        self._requests: dict[int, deque[float]] = defaultdict(deque)

    def handle(self, chat_id: int, user_id: int, text: str) -> list[str]:
        text = (text or "").strip()
        if not text:
            return [NON_TEXT_TEXT]

        if text.startswith("/"):
            return [self._command(chat_id, user_id, text)]

        if not self._allow(user_id):
            return [RATE_LIMIT_TEXT]
        if len(text) > self._max_input_chars:
            return [TOO_LONG_TEXT]

        session_id = f"tg:{chat_id}:{user_id}"
        response = self._orchestrator.chat(
            text,
            request_id=f"tg-{chat_id}-{user_id}-{uuid4().hex[:12]}",
            session_id=session_id,
        )
        self._store.save_context(
            chat_id,
            user_id,
            question=text,
            status=response.status.value,
            metric=response.data.metric if response.data is not None else None,
            period=response.data.period if response.data is not None else None,
        )
        return split_text(format_response(response))

    def _command(self, chat_id: int, user_id: int, text: str) -> str:
        command = text.split(maxsplit=1)[0].lower().split("@", 1)[0]
        if command == "/start":
            return START_TEXT
        if command == "/help":
            return HELP_TEXT
        if command == "/reset":
            self._store.reset(chat_id, user_id)
            self._orchestrator.reset_session(f"tg:{chat_id}:{user_id}")
            return RESET_TEXT
        if command == "/language":
            return LANGUAGE_TEXT
        return UNKNOWN_COMMAND_TEXT

    def _allow(self, user_id: int) -> bool:
        now = time.monotonic()
        window = self._requests[user_id]
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= self._rate_limit:
            return False
        window.append(now)
        return True

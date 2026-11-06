from __future__ import annotations

from app.domain.enums import ResponseStatus
from app.handlers.base import Evidence
from app.responses.models import AnswerData, ChatResponse
from app.telegram.context import rewrite_follow_up
from app.telegram.handlers import (
    RATE_LIMIT_TEXT,
    RESET_TEXT,
    TelegramHandlers,
    UNKNOWN_COMMAND_TEXT,
)
from app.telegram.message_formatter import MAX_CHUNK_CHARS, format_response, split_text
from app.telegram.session_store import SessionStore


def _found(metric: str = "deal_count", value: int = 106647) -> ChatResponse:
    return ChatResponse(
        status=ResponseStatus.FOUND,
        language="ru",
        script="cyrillic",
        locale="ru-RU",
        answer=f"За период 2024 год найдено сделок: {value}.",
        data=AnswerData(metric=metric, value=value, period="2024 год"),
        evidence=Evidence(
            source_tables=["deals"],
            dataset_version="sha256:test",
            dataset_cutoff="2026-10-30",
            filters=[],
            metric="deal_count",
            matched_rows=value,
            grouping=[],
            currency_method=None,
            unit=None,
            execution_id="exec-1",
        ),
        duration_ms=1,
        request_id="req-1",
    )


class FakeOrchestrator:
    def __init__(self) -> None:
        self.questions: list[str] = []
        self.session_ids: list[str | None] = []
        self.reset_sessions: list[str] = []

    def chat(
        self,
        question: str,
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> ChatResponse:
        self.questions.append(question)
        self.session_ids.append(session_id)
        return _found()

    def reset_session(self, session_id: str) -> None:
        self.reset_sessions.append(session_id)


def test_session_context_is_isolated_by_chat_and_user(tmp_path):
    store = SessionStore(tmp_path / "state.sqlite3")
    try:
        store.save_context(
            1, 10, question="q-a", status="FOUND", metric="deal_count"
        )
        store.save_context(
            1, 11, question="q-b", status="FOUND", metric="amount"
        )
        store.save_context(
            2, 10, question="q-c", status="NOT_FOUND", metric=None
        )

        assert store.get_context(1, 10)["last_question"] == "q-a"
        assert store.get_context(1, 11)["last_question"] == "q-b"
        assert store.get_context(2, 10)["last_question"] == "q-c"
        assert store.get_context(1, 10)["last_metric"] == "deal_count"
        assert store.get_context(1, 11)["last_metric"] == "amount"

        store.reset(1, 10)
        assert store.get_context(1, 10) is None
        assert store.get_context(1, 11)["last_question"] == "q-b"
        assert store.get_context(2, 10)["last_question"] == "q-c"
    finally:
        store.close()


def test_offset_is_persisted(tmp_path):
    store = SessionStore(tmp_path / "state.sqlite3")
    try:
        assert store.get_offset() is None
        store.set_offset(1001)
        assert store.get_offset() == 1001
        store.set_offset(1002)
        assert store.get_offset() == 1002
    finally:
        store.close()


def test_year_follow_up_preserves_deal_count_metric():
    context = {"last_status": "FOUND", "last_metric": "deal_count"}
    assert rewrite_follow_up("А за 2023?", context) == (
        "Сколько сделок было в 2023 году?",
        True,
    )
    assert rewrite_follow_up("2023", context) == (
        "Сколько сделок было в 2023 году?",
        True,
    )


def test_year_follow_up_is_not_applied_to_other_metrics_or_states():
    question = "А за 2023?"
    assert rewrite_follow_up(
        question, {"last_status": "FOUND", "last_metric": "amount"}
    ) == (question, False)
    assert rewrite_follow_up(
        question, {"last_status": "NOT_FOUND", "last_metric": "deal_count"}
    ) == (question, False)
    assert rewrite_follow_up(question, None) == (question, False)
    assert rewrite_follow_up(
        "Покажи динамику сделок по годам",
        {"last_status": "FOUND", "last_metric": "deal_count"},
    ) == ("Покажи динамику сделок по годам", False)


def test_split_text_respects_limit_and_keeps_content():
    short = "короткий ответ"
    assert split_text(short) == [short]

    long_text = "\n".join(f"строка {index} " + "x" * 80 for index in range(120))
    chunks = split_text(long_text, limit=500)
    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert "".join(chunk.replace("\n", "") for chunk in chunks) == long_text.replace(
        "\n", ""
    )


def test_format_includes_evidence_footer():
    text = format_response(_found())
    assert "106647" in text
    assert "Источник: PostgreSQL" in text
    assert "cutoff 2026-10-30" in text
    assert "<" not in text and "*" not in text


def test_handlers_use_shared_orchestrator_and_isolate_context(tmp_path):
    store = SessionStore(tmp_path / "state.sqlite3")
    orchestrator = FakeOrchestrator()
    handlers = TelegramHandlers(orchestrator=orchestrator, store=store)
    try:
        replies = handlers.handle(5, 100, "Сколько сделок было в 2024 году?")
        assert orchestrator.questions == ["Сколько сделок было в 2024 году?"]
        assert orchestrator.session_ids == ["tg:5:100"]
        assert any("106647" in reply for reply in replies)
        assert any("Источник: PostgreSQL" in reply for reply in replies)
        assert store.get_context(5, 100)["last_status"] == "FOUND"

        assert handlers.handle(5, 100, "/reset") == [RESET_TEXT]
        assert store.get_context(5, 100) is None

        assert handlers.handle(5, 200, "/reset") == [RESET_TEXT]
        assert handlers.handle(5, 200, "/unknown") == [UNKNOWN_COMMAND_TEXT]
        assert len(orchestrator.questions) == 1
    finally:
        store.close()


def test_handler_forwards_follow_up_and_session_id_to_orchestrator(tmp_path):
    """The adapter no longer rewrites text; the orchestrator owns context.

    The raw elliptical follow-up must reach the orchestrator together with a
    stable per-(chat, user) session id, so plan history stays isolated.
    """

    store = SessionStore(tmp_path / "state.sqlite3")
    orchestrator = FakeOrchestrator()
    handlers = TelegramHandlers(orchestrator=orchestrator, store=store)
    try:
        handlers.handle(7, 700, "Сколько сделок было в 2024 году?")
        handlers.handle(7, 700, "А за 2023?")
        assert orchestrator.questions[-1] == "А за 2023?"
        assert orchestrator.session_ids == ["tg:7:700", "tg:7:700"]

        handlers.handle(7, 701, "А за 2023?")
        assert orchestrator.session_ids[-1] == "tg:7:701"

        handlers.handle(7, 700, "/reset")
        assert orchestrator.reset_sessions == ["tg:7:700"]
    finally:
        store.close()


def test_rate_limit_blocks_without_calling_orchestrator(tmp_path):
    store = SessionStore(tmp_path / "state.sqlite3")
    orchestrator = FakeOrchestrator()
    handlers = TelegramHandlers(
        orchestrator=orchestrator, store=store, rate_limit_per_minute=1
    )
    try:
        handlers.handle(8, 800, "Сколько сделок было в 2024 году?")
        replies = handlers.handle(8, 800, "Сколько сделок было в 2023 году?")
        assert replies == [RATE_LIMIT_TEXT]
        assert orchestrator.questions == ["Сколько сделок было в 2024 году?"]
    finally:
        store.close()


def test_long_input_is_rejected_before_orchestrator(tmp_path):
    store = SessionStore(tmp_path / "state.sqlite3")
    orchestrator = FakeOrchestrator()
    handlers = TelegramHandlers(
        orchestrator=orchestrator, store=store, max_input_chars=20
    )
    try:
        replies = handlers.handle(9, 900, "x" * 21)
        assert len(replies) == 1
        assert "слишком длинный" in replies[0].lower()
        assert orchestrator.questions == []
    finally:
        store.close()


def test_chunk_size_is_below_telegram_limit():
    assert MAX_CHUNK_CHARS < 4096

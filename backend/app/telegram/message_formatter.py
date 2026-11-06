from __future__ import annotations

from app.responses.models import ChatResponse

# Telegram rejects messages longer than 4096 characters.  Keep a margin for
# safety and split on line boundaries where possible.
MAX_CHUNK_CHARS = 3800


def split_text(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current.strip():
                chunks.append(current.rstrip())
                current = ""
            chunks.append(line[:limit].rstrip())
            line = line[limit:]
        if len(current) + len(line) > limit:
            if current.strip():
                chunks.append(current.rstrip())
            current = line
        else:
            current += line
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


def format_response(response: ChatResponse) -> str:
    """Plain text answer plus the PostgreSQL evidence footer.

    Telegram is intentionally rendered without Markdown/HTML: user text is
    never parsed, so it cannot inject formatting or links.
    """

    parts = [response.answer.strip()]
    evidence = response.evidence
    if response.status.value == "FOUND" and evidence is not None:
        parts.append(
            "Источник: PostgreSQL"
            f" · dataset {evidence.dataset_version}"
            f" · cutoff {evidence.dataset_cutoff}"
            f" · строк: {evidence.matched_rows}"
        )
    return "\n\n".join(part for part in parts if part)

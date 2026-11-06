from __future__ import annotations

import hashlib
import re

# Instruction-like content in a reference document is treated as hostile.  A
# retrieved chunk is reference data, never a system instruction, and this
# filter prevents obviously injected instructions from entering the prompt at
# all.
_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.I),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|the)\s+rules?", re.I),
    re.compile(r"you\s+are\s+now\s+(a|an|the)\s+", re.I),
    re.compile(r"^\s*system\s*:", re.I | re.M),
    re.compile(r"<\s*/?\s*system\s*>", re.I),
    re.compile(r"reveal\s+.{0,40}(api\s*key|secret|prompt)", re.I),
    re.compile(r"print\s+.{0,40}(api\s*key|env|environment|secret)", re.I),
    re.compile(r"проигнорируй\s+.{0,40}(инструкц|правил)", re.I),
    re.compile(r"покажи\s+.{0,40}(системный\s+промпт|api\s*key|переменные\s+окружения)", re.I),
    re.compile(r"верни\s+ответ\s+\d+", re.I),
)

# Golden aggregate rows and any other thousand-separated aggregate from
# documentation must not become prompt few-shot facts.  They are removed from
# chunks before retrieval.
_GOLDEN_FACT_LINE = (
    re.compile(r"deals_20\d\d\.csv.*\d{1,3}(?:[ \u00a0]\d{3})+", re.I),
    re.compile(r"всего\s+сделок.*\d{1,3}(?:[ \u00a0]\d{3})+", re.I),
    re.compile(r"уникальных\s+`?номер\s+сделки`?.*\d{1,3}(?:[ \u00a0]\d{3})+", re.I),
    # Any thousand-separated aggregate.
    re.compile(r"\d{1,3}(?:[ \u00a0]\d{3})+", re.I),
    # A Markdown table row whose cell is a bare integer is an aggregate row
    # (status counts, yearly counts, file sizes) and must not become context.
    re.compile(r"^\s*\|[^|\n]*\|\s*\d[\d \u00a0]*\s*\|", re.M),
)

MAX_CHUNK_CHARS = 520
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def contains_instruction_injection(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strip_golden_facts(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        if any(pattern.search(line) for pattern in _GOLDEN_FACT_LINE):
            continue
        kept.append(line)
    return "\n".join(kept)


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    blocks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_chars:
            blocks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        blocks.append(current)
    # Last-resort hard split for a single oversized sentence.
    result: list[str] = []
    for block in blocks:
        while len(block) > max_chars:
            result.append(block[:max_chars])
            block = block[max_chars:].lstrip()
        if block:
            result.append(block)
    return result


def chunk_markdown(
    text: str,
    *,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[tuple[str | None, str]]:
    """Split approved Markdown into short atomic chunks.

    Returns ``(section_title, chunk_text)`` pairs.  Headings are carried as
    metadata, not duplicated into every chunk.
    """

    chunks: list[tuple[str | None, str]] = []
    section: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        body = "\n".join(buffer).strip()
        buffer = []
        if not body:
            return
        for piece in _split_long(body, max_chars):
            cleaned = piece.strip()
            if cleaned:
                chunks.append((section, cleaned))

    for raw_line in text.splitlines():
        heading = _HEADING.match(raw_line.strip())
        if heading:
            flush()
            section = heading.group(2).strip()
            continue
        if not raw_line.strip():
            flush()
            continue
        buffer.append(raw_line.rstrip())
    flush()
    return chunks

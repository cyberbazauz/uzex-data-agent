from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.rag.chunker import (
    chunk_markdown,
    contains_instruction_injection,
    content_hash,
    strip_golden_facts,
)
from app.rag.models import (
    ApprovalStatus,
    KnowledgeChunk,
    RejectedSource,
    SourceType,
    TrustLevel,
)

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"
DEFAULT_MANIFEST = KNOWLEDGE_DIR / "manifest.json"
ALLOWED_FORMATS = {"markdown", "synonyms", "examples"}
SOURCE_TYPE_ALLOWLIST = {item.value for item in SourceType}


@dataclass
class KnowledgeLoadResult:
    chunks: list[KnowledgeChunk] = field(default_factory=list)
    rejected: list[RejectedSource] = field(default_factory=list)
    manifest_version: str | None = None


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _date(value: object) -> date | None:
    if value is None or value == "":
        return None
    return date.fromisoformat(str(value))


def _chunk(
    *,
    source: dict[str, Any],
    index: int,
    section: str | None,
    text: str,
    language: str | None = None,
    script: str | None = None,
) -> KnowledgeChunk:
    digest = content_hash(text)
    return KnowledgeChunk(
        id=f"{source['source_id']}:{index}:{digest[:12]}",
        source_id=str(source["source_id"]),
        source_type=SourceType(str(source["source_type"])),
        source_name=str(source["source_name"]),
        section_title=section,
        language=str(language or source.get("language", "*")),
        script=str(script or source.get("script", "*")),
        chunk_text=text,
        source_version=str(source.get("source_version", "1")),
        content_hash=digest,
        approval_status=ApprovalStatus(str(source.get("approval_status", "DRAFT"))),
        trust_level=TrustLevel(str(source.get("trust_level", "CURATED"))),
        effective_from=_date(source.get("effective_from")),
        effective_to=_date(source.get("effective_to")),
    )


def _is_meaningful_markdown(body: str) -> bool:
    """Reject a chunk that is only a table header/separator shell.

    Aggregate table rows are redacted before chunking.  What remains can be a
    header such as ``| Значение | Сделок |`` with no semantic content; it must
    not occupy a retrieval slot.
    """

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if len(lines) > 3:
        return True
    if all(line.startswith("|") for line in lines):
        return sum(1 for char in body if char.isalpha()) >= 40
    return True


def _load_markdown(
    source: dict[str, Any],
    text: str,
    *,
    start_index: int,
) -> tuple[list[KnowledgeChunk], list[RejectedSource]]:
    chunks: list[KnowledgeChunk] = []
    rejected: list[RejectedSource] = []
    if source.get("redact_golden_facts", True):
        text = strip_golden_facts(text)
    for offset, (section, body) in enumerate(chunk_markdown(text)):
        if len(body) < 12:
            continue
        if not _is_meaningful_markdown(body):
            continue
        if contains_instruction_injection(body):
            rejected.append(
                RejectedSource(
                    source_id=str(source["source_id"]),
                    reason="instruction_injection",
                    detail=f"section={section or '-'} offset={offset}",
                )
            )
            continue
        chunks.append(
            _chunk(
                source=source,
                index=start_index + len(chunks),
                section=section,
                text=body,
            )
        )
    return chunks, rejected


def _load_synonyms(
    source: dict[str, Any],
    text: str,
    *,
    start_index: int,
) -> tuple[list[KnowledgeChunk], list[RejectedSource]]:
    data = json.loads(text)
    terms = data.get("terms") if isinstance(data, dict) else None
    if not isinstance(terms, list):
        raise ValueError("synonym source must contain a terms array")
    chunks: list[KnowledgeChunk] = []
    rejected: list[RejectedSource] = []
    for term in terms:
        if not isinstance(term, dict):
            continue
        raw_term = str(term.get("term", "")).strip()
        canonical = str(term.get("canonical", "")).strip()
        if not raw_term or not canonical:
            continue
        description = str(term.get("description", "")).strip()
        body = f"{raw_term} => {canonical}"
        if description:
            body = f"{body}. {description}"
        if contains_instruction_injection(body):
            rejected.append(
                RejectedSource(
                    source_id=str(source["source_id"]),
                    reason="instruction_injection",
                    detail=raw_term[:40],
                )
            )
            continue
        chunks.append(
            _chunk(
                source=source,
                index=start_index + len(chunks),
                section=raw_term,
                text=body,
                language=str(term.get("language") or source.get("language", "*")),
                script=str(term.get("script") or source.get("script", "*")),
            )
        )
    return chunks, rejected


def _plan_summary(plan: dict[str, Any]) -> str:
    # Deliberately excludes dates, limits and every numeric fact.  A pattern
    # teaches the question -> plan mapping; it is not a stored answer.
    parts = [
        f"decision={plan.get('decision')}",
        f"intent={plan.get('intent')}",
        f"subject={plan.get('subject')}",
        f"metric={plan.get('metric')}",
        f"currency_mode={plan.get('currency_mode')}",
    ]
    if plan.get("clarification"):
        code = plan["clarification"].get("code")
        parts.append(f"clarification={code}")
    return ", ".join(parts)


def _load_examples(
    source: dict[str, Any],
    text: str,
    *,
    start_index: int,
) -> tuple[list[KnowledgeChunk], list[RejectedSource]]:
    data = json.loads(text)
    examples = data.get("examples") if isinstance(data, dict) else None
    if not isinstance(examples, list):
        raise ValueError("example source must contain an examples array")
    chunks: list[KnowledgeChunk] = []
    rejected: list[RejectedSource] = []
    for example in examples:
        if not isinstance(example, dict):
            continue
        if str(example.get("approval_status", "DRAFT")) != "APPROVED":
            continue
        question = str(example.get("question", "")).strip()
        plan = example.get("plan")
        if not question or not isinstance(plan, dict):
            continue
        body = f"Вопрос: {question}\nУтверждённый план: {_plan_summary(plan)}"
        if contains_instruction_injection(body):
            rejected.append(
                RejectedSource(
                    source_id=str(source["source_id"]),
                    reason="instruction_injection",
                    detail=str(example.get("example_id", ""))[:40],
                )
            )
            continue
        chunks.append(
            _chunk(
                source=source,
                index=start_index + len(chunks),
                section=str(example.get("example_id", "")),
                text=body,
                language=str(example.get("language") or source.get("language", "*")),
                script=str(example.get("script") or source.get("script", "*")),
            )
        )
    return chunks, rejected


def load_knowledge(manifest_path: Path | None = None) -> KnowledgeLoadResult:
    """Load only explicitly APPROVED reference sources.

    Fail-closed rules:
    * a missing/invalid manifest yields no knowledge;
    * a source with a different SHA-256 than the approved manifest entry is
      rejected (the approval no longer applies);
    * DRAFT/RETIRED sources are rejected;
    * instruction-like content inside a chunk is rejected.
    """

    path = manifest_path or DEFAULT_MANIFEST
    result = KnowledgeLoadResult()
    if not path.exists():
        result.rejected.append(
            RejectedSource(source_id="manifest", reason="manifest_missing")
        )
        return result
    try:
        manifest = json.loads(_read_text(path))
    except (OSError, ValueError) as exc:
        result.rejected.append(
            RejectedSource(
                source_id="manifest", reason="manifest_invalid", detail=type(exc).__name__
            )
        )
        return result
    if not isinstance(manifest, dict):
        result.rejected.append(
            RejectedSource(source_id="manifest", reason="manifest_invalid")
        )
        return result
    result.manifest_version = str(manifest.get("version", "")) or None

    seen: set[tuple[str, str]] = set()
    for source in manifest.get("sources", []):
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id", ""))
        try:
            if source.get("approval_status") != "APPROVED":
                result.rejected.append(
                    RejectedSource(source_id=source_id, reason="not_approved")
                )
                continue
            source_type = str(source.get("source_type", ""))
            if source_type not in SOURCE_TYPE_ALLOWLIST:
                result.rejected.append(
                    RejectedSource(
                        source_id=source_id, reason="source_type_not_allowed"
                    )
                )
                continue
            source_format = str(source.get("format", "markdown"))
            if source_format not in ALLOWED_FORMATS:
                result.rejected.append(
                    RejectedSource(source_id=source_id, reason="format_not_allowed")
                )
                continue
            source_path = (path.parent / str(source.get("path", ""))).resolve()
            if not source_path.exists():
                result.rejected.append(
                    RejectedSource(source_id=source_id, reason="source_missing")
                )
                continue
            text = _read_text(source_path)
            declared = str(source.get("declared_sha256", "")).strip().lower()
            if declared and declared != _sha256_file(source_path):
                result.rejected.append(
                    RejectedSource(
                        source_id=source_id,
                        reason="sha256_mismatch",
                        detail="approved content changed; re-approval required",
                    )
                )
                continue

            if source_format == "markdown":
                chunks, rejected = _load_markdown(
                    source, text, start_index=0
                )
            elif source_format == "synonyms":
                chunks, rejected = _load_synonyms(
                    source, text, start_index=0
                )
            else:
                chunks, rejected = _load_examples(
                    source, text, start_index=0
                )
            result.rejected.extend(rejected)
            for chunk in chunks:
                key = (chunk.source_id, chunk.content_hash)
                if key in seen:
                    continue
                seen.add(key)
                result.chunks.append(chunk)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.rejected.append(
                RejectedSource(
                    source_id=source_id,
                    reason="source_invalid",
                    detail=type(exc).__name__,
                )
            )
    return result

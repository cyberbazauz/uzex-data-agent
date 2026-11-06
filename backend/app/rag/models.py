from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceType(StrEnum):
    """Approved reference-material categories.

    There is deliberately no ``DEAL_ROW`` member: transaction rows are facts
    resolved by PostgreSQL handlers and must never become RAG knowledge.
    """

    FIELD_DEFINITION = "FIELD_DEFINITION"
    BUSINESS_RULE = "BUSINESS_RULE"
    LANGUAGE_SYNONYM = "LANGUAGE_SYNONYM"
    ENTITY_ALIAS = "ENTITY_ALIAS"
    QUERY_PATTERN = "QUERY_PATTERN"
    DATA_LIMITATION = "DATA_LIMITATION"


class ApprovalStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    RETIRED = "RETIRED"


class TrustLevel(StrEnum):
    AUTHORITATIVE = "AUTHORITATIVE"
    CURATED = "CURATED"
    EXAMPLE = "EXAMPLE"


TRUST_WEIGHT: dict[TrustLevel, float] = {
    TrustLevel.AUTHORITATIVE: 1.0,
    TrustLevel.CURATED: 0.8,
    TrustLevel.EXAMPLE: 0.5,
}


class KnowledgeChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_id: str
    source_type: SourceType
    source_name: str
    section_title: str | None = None
    language: str = "*"  # ru | uz | *
    script: str = "*"  # cyrillic | latin | *
    chunk_text: str
    source_version: str
    content_hash: str
    approval_status: ApprovalStatus
    trust_level: TrustLevel
    effective_from: date | None = None
    effective_to: date | None = None

    def is_active(self, day: date) -> bool:
        if self.approval_status is not ApprovalStatus.APPROVED:
            return False
        if self.effective_from is not None and day < self.effective_from:
            return False
        if self.effective_to is not None and day > self.effective_to:
            return False
        return True

    def matches_language(self, language: str, script: str) -> bool:
        if self.language not in {"*", language}:
            return False
        if self.script not in {"*", script}:
            return False
        return True

    def public_source(self) -> dict[str, Any]:
        """Safe metadata only: never the raw chunk text in a public API."""

        return {
            "source_id": self.source_id,
            "source_type": self.source_type.value,
            "source_name": self.source_name,
            "section_title": self.section_title,
            "language": self.language,
            "script": self.script,
            "source_version": self.source_version,
            "content_hash": self.content_hash,
            "trust_level": self.trust_level.value,
            "approval_status": self.approval_status.value,
        }


class RejectedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    reason: str
    detail: str | None = None


class RetrievalHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk: KnowledgeChunk
    rank: int
    relevance: float = 0.0
    lexical_score: float = 0.0
    trigram_score: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)

    def trace_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.chunk.source_id,
            "source_type": self.chunk.source_type.value,
            "source_name": self.chunk.source_name,
            "source_version": self.chunk.source_version,
            "content_hash": self.chunk.content_hash,
            "rank": self.rank,
            "relevance": round(self.relevance, 6),
            "lexical_score": round(self.lexical_score, 6),
            "trigram_score": round(self.trigram_score, 6),
            "matched_terms": self.matched_terms,
        }


class RetrievalTrace(BaseModel):
    """Internal retrieval evidence.

    Contains no database facts, no prompt text and no secrets.  It is logged
    internally and never returned by the public API.
    """

    model_config = ConfigDict(extra="forbid")

    query: str
    language: str
    script: str
    backend: str = "in_memory_lexical_trigram_rrf"
    top_k: int
    candidates_scanned: int = 0
    hits: list[RetrievalHit] = Field(default_factory=list)
    rejected: list[RejectedSource] = Field(default_factory=list)

    def trace_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "language": self.language,
            "script": self.script,
            "top_k": self.top_k,
            "candidates_scanned": self.candidates_scanned,
            "hits": [hit.trace_dict() for hit in self.hits],
            "rejected": [item.model_dump() for item in self.rejected],
        }


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_context: str
    hits: list[RetrievalHit] = Field(default_factory=list)
    trace: RetrievalTrace

    @property
    def is_empty(self) -> bool:
        return not self.hits

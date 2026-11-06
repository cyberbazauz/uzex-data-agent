from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.rag.models import (
    ApprovalStatus,
    KnowledgeChunk,
    RejectedSource,
    RetrievalHit,
    SourceType,
    TRUST_WEIGHT,
)

_WORD = re.compile(r"[0-9a-zа-яёўқғҳ\u0400-\u04ff']+", re.I)
_APOSTROPHES = str.maketrans({"'": "", "’": "", "ʻ": "", "ʼ": ""})

_STOPWORDS = frozenset(
    {
        # Russian
        "и", "в", "во", "на", "по", "за", "с", "со", "у", "к", "ко", "о", "об",
        "от", "до", "из", "для", "не", "ни", "что", "как", "какой", "какая",
        "какие", "сколько", "где", "когда", "ли", "же", "бы", "это", "все",
        "весь", "было", "были", "есть", "или", "а", "но", "the", "of", "to",
        "in", "is", "are", "was", "were", "what", "how", "many", "much",
        # Uzbek Latin / Cyrillic
        "va", "da", "dagi", "uchun", "bilan", "nechta", "qancha", "qanday",
        "bu", "shu", "bor", "bo", "lgan", "bo'lgan", "bolgan", "jami",
        "ва", "да", "учун", "билан", "нечта", "қанча", "қандай", "бу", "шу",
        "бор", "бўлган", "жами",
    }
)


def _normalize(text: str) -> str:
    return text.casefold().translate(_APOSTROPHES)


def tokenize(text: str) -> list[str]:
    tokens = _WORD.findall(_normalize(text))
    return [token for token in tokens if len(token) >= 2 and token not in _STOPWORDS]


def _trigrams(text: str) -> set[str]:
    compact = re.sub(r"\s+", " ", _normalize(text))
    if len(compact) < 3:
        return {compact} if compact else set()
    return {compact[index : index + 3] for index in range(len(compact) - 2)}


def _token_match(query_token: str, chunk_token: str) -> bool:
    if query_token == chunk_token:
        return True
    short, long = sorted((query_token, chunk_token), key=len)
    return len(short) >= 4 and long.startswith(short)


def _lexical_score(
    query_tokens: list[str], chunk_tokens: list[str]
) -> tuple[float, list[str]]:
    if not query_tokens:
        return 0.0, []
    matched = [
        token
        for token in query_tokens
        if any(_token_match(token, candidate) for candidate in chunk_tokens)
    ]
    return len(matched) / len(query_tokens), list(dict.fromkeys(matched))


def _trigram_score(query_text: str, chunk_text: str) -> float:
    left = _trigrams(query_text)
    right = _trigrams(chunk_text)
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    return intersection / union if union else 0.0


@dataclass
class ScoredChunk:
    chunk: KnowledgeChunk
    score: float
    matched_terms: list[str] = field(default_factory=list)


@dataclass
class SearchOutcome:
    lexical: list[ScoredChunk] = field(default_factory=list)
    fuzzy: list[ScoredChunk] = field(default_factory=list)
    dictionary: list[ScoredChunk] = field(default_factory=list)
    scanned: int = 0
    rejected: list[RejectedSource] = field(default_factory=list)


class InMemoryKnowledgeRepository:
    """Deterministic lexical + trigram retrieval over approved chunks.

    PostgreSQL remains the production store; this repository implements the
    same ranking contract without any embedding dependency, so retrieval still
    works if pgvector is unavailable.  Scores are relevance signals only and
    are never used as proof of an entity or a fact.
    """

    backend = "in_memory_lexical_trigram_rrf"

    def __init__(
        self,
        chunks: list[KnowledgeChunk],
        *,
        today: date | None = None,
        rejected: list[RejectedSource] | None = None,
    ) -> None:
        self._today = today or date.today()
        self._rejected = list(rejected or [])
        self._chunks = [
            chunk
            for chunk in chunks
            if chunk.approval_status is ApprovalStatus.APPROVED
            and chunk.is_active(self._today)
        ]

    def __len__(self) -> int:
        return len(self._chunks)

    def all_chunks(self) -> list[KnowledgeChunk]:
        return list(self._chunks)

    def search(
        self,
        query: str,
        *,
        language: str,
        script: str,
        top_k: int = 5,
        min_relevance: float = 0.12,
    ) -> SearchOutcome:
        query_tokens = tokenize(query)
        normalized_query = _normalize(query)
        outcome = SearchOutcome(rejected=list(self._rejected))
        lexical: list[ScoredChunk] = []
        fuzzy: list[ScoredChunk] = []

        for chunk in self._chunks:
            if not chunk.matches_language(language, script):
                continue
            outcome.scanned += 1
            chunk_tokens = tokenize(chunk.chunk_text)
            lexical_score, matched = _lexical_score(query_tokens, chunk_tokens)
            trigram = _trigram_score(query, chunk.chunk_text)
            weight = TRUST_WEIGHT[chunk.trust_level]
            combined = (0.72 * lexical_score + 0.28 * trigram) * weight

            if lexical_score > 0.0 and combined >= min_relevance:
                lexical.append(
                    ScoredChunk(
                        chunk=chunk,
                        score=min(1.0, combined),
                        matched_terms=matched,
                    )
                )
            if trigram >= 0.28 and combined >= min_relevance:
                fuzzy.append(
                    ScoredChunk(
                        chunk=chunk,
                        score=min(1.0, 0.6 * trigram + 0.4 * lexical_score),
                        matched_terms=matched,
                    )
                )

            if chunk.source_type in {
                SourceType.LANGUAGE_SYNONYM,
                SourceType.ENTITY_ALIAS,
            }:
                term = chunk.chunk_text.split("=>", 1)[0].strip()
                if term and _normalize(term) in normalized_query:
                    outcome.dictionary.append(
                        ScoredChunk(
                            chunk=chunk,
                            score=1.0,
                            matched_terms=[term],
                        )
                    )

        def ordered(items: list[ScoredChunk]) -> list[ScoredChunk]:
            return sorted(
                items,
                key=lambda item: (
                    -item.score,
                    -TRUST_WEIGHT[item.chunk.trust_level],
                    item.chunk.id,
                ),
            )[: max(top_k * 3, 10)]

        outcome.lexical = ordered(lexical)
        outcome.fuzzy = ordered(fuzzy)
        outcome.dictionary = ordered(outcome.dictionary)
        return outcome


def build_hits(
    fused: list[tuple[KnowledgeChunk, float, ScoredChunk | None]],
    query: str,
    *,
    top_k: int,
) -> list[RetrievalHit]:
    hits: list[RetrievalHit] = []
    for rank, (chunk, score, source) in enumerate(fused[:top_k], start=1):
        lexical_score = 0.0
        trigram_score = 0.0
        matched: list[str] = []
        if source is not None:
            matched = source.matched_terms
            lexical_score = source.score
        trigram_score = _trigram_score(query, chunk.chunk_text)
        hits.append(
            RetrievalHit(
                chunk=chunk,
                rank=rank,
                relevance=max(0.0, min(1.0, score)),
                lexical_score=lexical_score,
                trigram_score=trigram_score,
                matched_terms=matched,
            )
        )
    return hits

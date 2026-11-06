from __future__ import annotations

from app.rag.knowledge_repository import ScoredChunk
from app.rag.models import KnowledgeChunk


def reciprocal_rank_fusion(
    rankings: list[list[ScoredChunk]],
    *,
    weights: list[float] | None = None,
    k: int = 60,
) -> list[tuple[KnowledgeChunk, float, ScoredChunk | None]]:
    """Fuse ranked lists with weighted reciprocal rank fusion.

    The function is deliberately tiny and deterministic.  It combines ranked
    candidates; it never promotes a chunk that was not retrieved by at least
    one approved lexical/trigram/dictionary signal.
    """

    if weights is None:
        weights = [1.0] * len(rankings)
    scores: dict[str, float] = {}
    representatives: dict[str, ScoredChunk] = {}
    for ranking, weight in zip(rankings, weights):
        for rank, item in enumerate(ranking, start=1):
            key = item.chunk.id
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
            current = representatives.get(key)
            if current is None or item.score > current.score:
                representatives[key] = item

    ordered = sorted(
        representatives.values(),
        key=lambda item: (-scores[item.chunk.id], item.chunk.id),
    )
    return [(item.chunk, scores[item.chunk.id], item) for item in ordered]

"""Hybrid Knowledge RAG.

The package indexes only approved reference material: documentation, field
definitions, business rules, language synonyms, entity aliases and approved
question -> QueryPlan examples.  It deliberately has no access to transaction
tables and no ability to prove a fact.  PostgreSQL remains the only source of
deal values, entities, sums and codes.
"""

from app.rag.document_loader import (
    KnowledgeLoadResult,
    load_knowledge,
)
from app.rag.hybrid_retriever import (
    HybridRetriever,
    build_retriever,
    render_reference_context,
)
from app.rag.models import (
    ApprovalStatus,
    KnowledgeChunk,
    RetrievalHit,
    RetrievalResult,
    RetrievalTrace,
    SourceType,
    TrustLevel,
)
from app.rag.query_examples import (
    ApprovedExample,
    load_approved_examples,
    select_examples,
)

__all__ = [
    "ApprovalStatus",
    "ApprovedExample",
    "HybridRetriever",
    "KnowledgeChunk",
    "KnowledgeLoadResult",
    "RetrievalHit",
    "RetrievalResult",
    "RetrievalTrace",
    "SourceType",
    "TrustLevel",
    "build_retriever",
    "load_approved_examples",
    "load_knowledge",
    "render_reference_context",
    "select_examples",
]

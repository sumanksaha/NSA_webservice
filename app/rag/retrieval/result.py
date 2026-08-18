"""Unified result types for the RAG retrieval pipeline.

Defines the ``RetrievedChunk``, ``SearchResult``, ``RAGResponse``, and
``Citation`` dataclasses that flow through every retrieval layer component.
These are plain dataclasses (not ORM models) so they are cheap to construct,
serialize, and test — following the ``SaveResult`` pattern from
``app/services/document_lifecycle.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RetrievedChunk:
    """A single chunk retrieved from the vector / sparse index.

    Fields mirror the Qdrant payload schema defined in
    ``RAG_AGENT_A_SCOPE.md`` §5.1 so Agent B can consume Agent A's index
    without transformation.
    """

    chunk_id: str
    score: float
    text: str
    section_number: str | None = None
    clause_number: str | None = None
    document_title: str = ""
    act_name: str = ""
    document_type: str = ""
    authority: str = ""
    chunk_index: int = 0
    hierarchy_level: int = 0
    parent_chunk_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "score": round(self.score, 6),
            "text": self.text,
            "section_number": self.section_number,
            "clause_number": self.clause_number,
            "document_title": self.document_title,
            "act_name": self.act_name,
            "document_type": self.document_type,
            "authority": self.authority,
            "chunk_index": self.chunk_index,
            "hierarchy_level": self.hierarchy_level,
            "parent_chunk_id": self.parent_chunk_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetrievedChunk:
        return cls(
            chunk_id=data["chunk_id"],
            score=data["score"],
            text=data["text"],
            section_number=data.get("section_number"),
            clause_number=data.get("clause_number"),
            document_title=data.get("document_title", ""),
            act_name=data.get("act_name", ""),
            document_type=data.get("document_type", ""),
            authority=data.get("authority", ""),
            chunk_index=data.get("chunk_index", 0),
            hierarchy_level=data.get("hierarchy_level", 0),
            parent_chunk_id=data.get("parent_chunk_id"),
        )


@dataclass
class SearchResult:
    """Result of a retrieval call for a single query.

    ``source`` indicates which retriever produced the result:
    ``"dense"``, ``"sparse"``, ``"hybrid"``, or ``"reranked"``.
    """

    query: str
    query_type: str
    chunks: list[RetrievedChunk] = field(default_factory=list)
    total: int = 0
    latency_ms: int = 0
    source: str = "hybrid"
    error: str | None = None


@dataclass
class Citation:
    """A citation extracted from or supporting an LLM response.

    ``confidence`` (0.0–1.0) reflects how strongly the cited chunk supports
    the answer — computed by the citation validator / hallucination detector
    (Phase 3).  For Phase 1 it defaults to the chunk's retrieval score.
    """

    chunk_id: str
    section_number: str | None
    document_title: str
    document_type: str
    authority: str
    url: str | None
    snippet: str
    confidence: float = 0.0


@dataclass
class RAGResponse:
    """Full RAG response schema (Phase 5 integration).

    Phase 1 populates the retrieval fields (``retrieved_chunks``,
    ``retrieval_latency_ms``) and leaves generation fields as defaults.
    Phases 2–3 fill in ``answer``, ``citations``, ``groundedness_score``,
    etc.
    """

    query: str = ""
    query_type: str = ""
    answer: str = ""
    citations: list[Citation] = field(default_factory=list)
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)
    groundedness_score: float = 0.0
    hallucination_detected: bool = False
    hallucinated_claims: list[str] = field(default_factory=list)
    confidence: float = 0.0
    retrieval_latency_ms: int = 0
    generation_latency_ms: int = 0
    total_latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_model: str = ""
    token_usage: dict[str, Any] = field(default_factory=lambda: {"prompt": 0, "completion": 0, "total": 0})
    debug: dict[str, Any] = field(default_factory=dict)

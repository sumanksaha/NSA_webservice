"""Autonomous research sub-package for the RAG agent.

Holds components that close the research loop: discovering gaps in the legal
corpus, planning ingestion, and orchestrating autonomous research over time.

Modules:
    corpus_discovery — scans the benchmark gold registry to find provisions
        registered as required evidence but absent (or body-missing) in the
        Qdrant corpus; maps gaps to source documents and emits ingestion
        requests.  Extends the Step 0 pre-annotation (question-centric,
        human-gated) into an autonomous, provision-centric discovery.
    failure_queue — the autoresearch input list: every badly-answered
        question (low human rating today, detector + eval failures later) is
        deduplicated here as a research target for the next session.
"""

from __future__ import annotations

from app.rag.research.corpus_discovery import (
    DiscoveryReport,
    GapAnalyzer,
    IngestionRequest,
    ProvisionGap,
    SourceDocumentGap,
    discover_corpus_gaps,
)
from app.rag.research.failure_queue import (
    enqueue_from_feedback,
    enqueue_research_target,
    open_targets,
    query_hash,
)

__all__ = [
    "DiscoveryReport",
    "GapAnalyzer",
    "IngestionRequest",
    "ProvisionGap",
    "SourceDocumentGap",
    "discover_corpus_gaps",
    "enqueue_from_feedback",
    "enqueue_research_target",
    "open_targets",
    "query_hash",
]

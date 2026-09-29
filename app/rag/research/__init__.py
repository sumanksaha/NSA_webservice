"""Autonomous research sub-package for the RAG agent.

Holds components that close the research loop: discovering gaps in the legal
corpus, planning ingestion, and orchestrating autonomous research over time.

Modules:
    corpus_discovery — scans the benchmark gold registry to find provisions
        registered as required evidence but absent (or body-missing) in the
        Qdrant corpus; maps gaps to source documents and emits ingestion
        requests.  Extends the Step 0 pre-annotation (question-centric,
        human-gated) into an autonomous, provision-centric discovery.
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

__all__ = [
    "DiscoveryReport",
    "GapAnalyzer",
    "IngestionRequest",
    "ProvisionGap",
    "SourceDocumentGap",
    "discover_corpus_gaps",
]

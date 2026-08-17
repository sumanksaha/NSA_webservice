"""RAG evaluation sub-package — Phase 4 deliverable (Evaluation Framework).

Computes RAGAS-style metrics (faithfulness, answer relevance, context
precision/recall, citation recall, groundedness) and provides a batch
evaluation runner + result storage backed by the ``rag_eval_result`` and
``rag_eval_dataset`` models.

Reuses:
- ``rapidfuzz`` text-similarity (pattern from ``app/rag/retrieval/sparse_retriever.py``)
- ``EvidenceVerifier`` (Phase 3) for faithfulness scoring
- ``ScoreField``-style method-based scoring from ``app/metadata_extractor/confidence.py``
- ``RAGEvalResult`` / ``RAGEvalDataset`` models (``app/models/rag.py``)
- ``log_audit`` hash-chained audit (R0)
"""

from app.rag.evaluation.metrics import (
    AnswerRelevanceMetric,
    CitationRecallMetric,
    ContextPrecisionMetric,
    ContextRecallMetric,
    EvalScore,
    FaithfulnessMetric,
    GroundednessMetric,
)
from app.rag.evaluation.report import EvalReport, EvalSummary
from app.rag.evaluation.runner import EvalRunner
from app.rag.evaluation.storage import EvalStorage

__all__ = [
    "AnswerRelevanceMetric",
    "CitationRecallMetric",
    "ContextPrecisionMetric",
    "ContextRecallMetric",
    "EvalReport",
    "EvalRunner",
    "EvalScore",
    "EvalStorage",
    "EvalSummary",
    "FaithfulnessMetric",
    "GroundednessMetric",
]

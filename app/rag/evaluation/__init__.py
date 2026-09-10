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

Phase 4 additionally provides the decomposition benchmark scored against the
gold dataset (``gold_dataset`` + ``benchmark``) and shared deterministic
text-matching helpers (``textmatch``).
"""

from app.rag.evaluation.benchmark import DecompositionBenchmark, QueryBenchmark
from app.rag.evaluation.gold_dataset import GOLD_DECOMPOSITION
from app.rag.evaluation.metrics import (
    CoverageMetrics,
    EvalScore,
    SeparateConfidenceMetrics,
)
from app.rag.evaluation.ragas_metrics import (
    AnswerRelevanceMetric,
    CitationRecallMetric,
    ContextPrecisionMetric,
    ContextRecallMetric,
    FaithfulnessMetric,
    GroundednessMetric,
)
from app.rag.evaluation.report import EvalReport, EvalSummary
from app.rag.evaluation.runner import EvalRunner
from app.rag.evaluation.storage import EvalStorage

__all__ = [
    "GOLD_DECOMPOSITION",
    "AnswerRelevanceMetric",
    "CitationRecallMetric",
    "ContextPrecisionMetric",
    "ContextRecallMetric",
    "CoverageMetrics",
    "DecompositionBenchmark",
    "EvalReport",
    "EvalRunner",
    "EvalScore",
    "EvalStorage",
    "EvalSummary",
    "FaithfulnessMetric",
    "GroundednessMetric",
    "QueryBenchmark",
    "SeparateConfidenceMetrics",
]

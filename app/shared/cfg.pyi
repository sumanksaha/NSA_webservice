"""Auto-generated type stub for cfg (see config_lint.py)."""
from __future__ import annotations

class Config:
    """RAG pipeline configuration (validated at import time)."""
    retrieval_cache: bool
    kg_fusion: bool
    kg_expansion: bool
    ensemble_ce_head: int
    retrieval_cache_ttl_seconds: int
    kg_max_provisions: int
    hallucination_detector: bool
    rag_log_retention_days: int

cfg: Config
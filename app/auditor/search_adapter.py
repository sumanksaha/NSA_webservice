"""RAG bridge for the auditor agent (best-effort regulation grounding).

Reuses the production retrieval entry point
(:func:`run_retrieval_pipeline`) so auditor evidence and the legal-RAG
answers share exactly the same hybrid retrieval + reranking. Degrades to
``[]`` when RAG is unavailable — the agent still produces a plan, just
without grounded citations.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def search_regulations(query: str, top_k: int = 3) -> list[dict[str, str]]:
    """Retrieve grounded regulatory evidence for *query*.

    Returns ``[{"citation": str, "text": str}]`` (citation prefers
    ``"<title> §<section>"``). Never raises: RAG failures yield ``[]``.
    """
    try:
        from app.rag.tasks import run_retrieval_pipeline

        result = run_retrieval_pipeline(query=query, top_k=top_k, pipeline="auditor")
    except Exception as exc:
        logger.warning("search_regulations: retrieval failed (%s)", exc)
        return []
    evidence: list[dict[str, str]] = []
    for chunk in (result or {}).get("chunks") or []:
        if not isinstance(chunk, dict):
            continue
        title = str(chunk.get("document_title") or chunk.get("act_name") or "Regulation").strip()
        section = str(chunk.get("section_number") or "").strip()
        citation = f"{title} §{section}" if section else title
        evidence.append({"citation": citation, "text": str(chunk.get("text") or "")})
    return evidence

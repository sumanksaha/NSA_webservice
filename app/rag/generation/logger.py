"""Generation observability — persist LLM generation metrics.

``GenerationLogger`` updates an existing :class:`RAGQueryLog` row (created
by :class:`app.rag.retrieval.logger.RetrievalLogger`) with
generation-phase metrics: response text, cited chunk IDs, groundedness
score, token usage, and latency.

Reuses:
- ``_content_hash`` pattern from ``app/rag/retrieval/logger.py``
- ``log_audit`` from ``app/services/audit.py`` for hash-chained audit
  entries (R0).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.extensions import db
from app.models.rag import RAGQueryLog
from app.services.audit import log_audit

logger = logging.getLogger(__name__)


def _content_hash(query: str, response_text: str | None) -> str:
    """SHA-256 hex digest of query + response (fingerprint)."""
    payload = query + ":" + (response_text or "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class GenerationLogger:
    """Log generation-phase metrics to the ``rag_query_log`` table.

    Call :meth:`log_generation` after an LLM generation completes.
    Updates the row identified by ``query_log_id`` with response text,
    token usage, citations, groundedness, and error info.

    All DB writes are best-effort — failures are logged and swallowed
    so a logging failure never breaks the generation pipeline.
    """

    def __init__(self, actor: str = "rag_system") -> None:
        self.actor = actor

    def log_generation(
        self,
        query_log_id: str | None,
        *,
        query: str | None = None,
        response_text: str | None = None,
        cited_chunk_ids: list[str] | None = None,
        groundedness_score: float | None = None,
        hallucination_detected: bool = False,
        hallucinated_claims: list[str] | None = None,
        total_latency_ms: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        llm_model: str | None = None,
        error: str | None = None,
        context_length: int | None = None,
        token_counter_result: dict | None = None,
    ) -> RAGQueryLog | None:
        """Update a ``RAGQueryLog`` row with generation-phase metrics.

        Args:
            query_log_id: The UUID of the ``RAGQueryLog`` row to update.
            query: The original query (for audit logging).
            response_text: The LLM's generated response text.
            cited_chunk_ids: Chunk IDs cited in the response.
            groundedness_score: 0.0-1.0 groundedness metric.
            hallucination_detected: Whether hallucination was flagged.
            hallucinated_claims: List of unverifiable claims.
            total_latency_ms: End-to-end latency in milliseconds.
            prompt_tokens: Tokens used in the prompt.
            completion_tokens: Tokens generated.
            llm_model: Model name used for the generation.
            error: Error message if generation failed.

        Returns:
            The updated ``RAGQueryLog`` row, or ``None`` on failure.
        """
        try:
            log_entry = db.session.get(RAGQueryLog, query_log_id)
            if log_entry is None:
                logger.warning("RAGQueryLog not found: %s", query_log_id)
                return None

            if response_text is not None:
                log_entry.response_text = response_text
            if llm_model is not None:
                log_entry.llm_model = llm_model
            if prompt_tokens is not None:
                log_entry.prompt_tokens = prompt_tokens
            if completion_tokens is not None:
                log_entry.completion_tokens = completion_tokens
            if cited_chunk_ids is not None:
                log_entry.cited_chunk_ids = cited_chunk_ids
            if groundedness_score is not None:
                log_entry.groundedness_score = groundedness_score
            log_entry.hallucination_detected = hallucination_detected
            if hallucinated_claims is not None:
                log_entry.hallucinated_claims = hallucinated_claims
            if total_latency_ms is not None:
                log_entry.total_latency_ms = total_latency_ms
            if error is not None:
                log_entry.error = error
            if context_length is not None:
                log_entry.context_length = context_length
            if token_counter_result is not None:
                # Best-effort: fill in any missing token fields from the
                # TokenCounter estimates (useful when the LLM client was
                # in stub mode and returned placeholder values).
                if not log_entry.prompt_tokens and token_counter_result.get("prompt_tokens"):
                    log_entry.prompt_tokens = token_counter_result["prompt_tokens"]
                if not log_entry.completion_tokens and token_counter_result.get("completion_tokens"):
                    log_entry.completion_tokens = token_counter_result["completion_tokens"]

            db.session.commit()

            # Hash-chained audit entry for tamper evidence (R0).
            try:
                entity_id = query_log_id or "rag_unknown"
                details: dict[str, Any] = {
                    "pipeline": "rag_generation",
                    "query": (query or "")[:500],
                    "model": llm_model or "stub",
                    "groundedness": groundedness_score,
                    "hallucination_detected": hallucination_detected,
                    "cited_chunk_count": len(cited_chunk_ids) if cited_chunk_ids else 0,
                    "total_latency_ms": total_latency_ms,
                    "context_length": context_length,
                }
                if error:
                    details["error"] = error
                log_audit(
                    entity_type="rag_query",
                    entity_id=entity_id,
                    action="GENERATION",
                    actor=self.actor,
                    details=details,
                )
            except Exception as audit_exc:
                logger.warning("Audit log for generation failed: %s", audit_exc)

            return log_entry

        except Exception as exc:
            logger.warning("GenerationLogger.log_generation failed (best-effort): %s", exc)
            db.session.rollback()
            return None

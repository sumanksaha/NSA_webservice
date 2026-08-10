"""Retrieval observability — query logging + hash-chained audit trail.

``RetrievalLogger`` persists per-query retrieval metadata to the
``rag_query_log`` table (SQLAlchemy model, see ``app/models/rag.py``), mirroring
the ``RAGQueryLog`` schema from ``RAG_AGENT_B_SCOPE.md`` §5.1.

``RetrievalAuditLog`` wraps the hash-chained audit pattern from
``app/services/audit.py`` — each retrieval event is appended to the existing
``AuditLog`` table with a SHA-256 chain, providing tamper-evident logging of
the full retrieval pipeline (query → retrieved → generated → verified).

The content-hash uses the SHA-256 pattern from
``app/services/version_control.py`` (``_calculate_content_hash``).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from app.extensions import db
from app.models.rag import RAGQueryLog
from app.services.audit import log_audit
from app.rag.retrieval.result import SearchResult

logger = logging.getLogger(__name__)


def _content_hash(query: str, response_text: str | None) -> str:
    """SHA-256 hex digest of query + response (fingerprint).

    Reuses the hashing approach from ``app/services/version_control.py``
    ``VersionService._calculate_content_hash``.
    """
    payload = query + ":" + (response_text or "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RetrievalLogger:
    """Log retrieval results to the ``rag_query_log`` table.

    Call :meth:`log` after every retrieval call.  The stored ``content_hash``
    enables dedup/trend analysis and can be used as the ``eval_run_id``
    for batched evaluation (Phase 4).
    """

    def log(
        self,
        query: str,
        query_type: str,
        result: SearchResult,
        *,
        llm_model: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        response_text: str | None = None,
        cited_chunk_ids: list[str] | None = None,
        groundedness_score: float | None = None,
        hallucination_detected: bool = False,
        hallucinated_claims: list[str] | None = None,
        total_latency_ms: int | None = None,
        error: str | None = None,
    ) -> RAGQueryLog | None:
        """Persist a retrieval-event record.

        Args:
            query: The user query.
            query_type: Classified :class:`QueryType` value.
            result: The :class:`SearchResult` from the retriever.
            response_text: The LLM response (Phase 2 — for content hashing).
            error: Error message if the pipeline failed.
            **kwargs: Generation-phase metrics (populated in Phase 2+).

        Returns:
            The created :class:`RAGQueryLog` row, or ``None`` if DB write
            failed (best-effort — never raises).
        """
        try:
            retrieved_ids = [c.chunk_id for c in result.chunks]
            retrieval_scores = [round(c.score, 6) for c in result.chunks]

            log_entry = RAGQueryLog(
                query=query,
                query_type=query_type,
                retrieved_chunk_ids=retrieved_ids,
                retrieval_scores=retrieval_scores,
                retrieval_latency_ms=result.latency_ms,
                context_length=len(" ".join(c.text for c in result.chunks)),
                llm_model=llm_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                response_text=response_text,
                cited_chunk_ids=cited_chunk_ids or [],
                groundedness_score=groundedness_score,
                hallucination_detected=hallucination_detected,
                hallucinated_claims=hallucinated_claims or [],
                total_latency_ms=total_latency_ms,
                error=error,
                content_hash=_content_hash(query, response_text),
                created_at=datetime.now(UTC),
            )
            db.session.add(log_entry)
            db.session.commit()
            return log_entry
        except Exception as exc:  # noqa: BLE001
            logger.warning("RetrievalLogger.log failed (best-effort): %s", exc)
            db.session.rollback()
            return None


class RetrievalAuditLog:
    """Hash-chained, tamper-evident audit trail for RAG retrieval operations.

    Wraps the :func:`app.services.audit.log_audit` function, which chains each
    audit entry's SHA-256 hash to the previous entry for the same entity
    (``query → retrieved → generated → verified``).  This provides the same
    tamper-evidence as the inspection and document-editor audit chains.

    The ``entity_id`` is the ``RAGQueryLog.id`` UUID, so the full lifecycle
    of a single query can be verified end-to-end via
    :func:`app.services.audit.verify_audit_chain`.
    """

    def __init__(self, actor: str = "rag_system") -> None:
        self.actor = actor

    def log_retrieval(
        self,
        query_log_id: str | None,
        query: str,
        query_type: str,
        chunk_ids: list[str],
        latency_ms: int,
        error: str | None = None,
    ) -> bool:
        """Log a retrieval event to the ``AuditLog`` table.

        Args:
            query_log_id: The ``RAGQueryLog.id`` UUID, or ``"new"`` if no
                log row was created.
            query: The user query (stored in details).
            query_type: Classified query type.
            chunk_ids: List of retrieved chunk IDs.
            latency_ms: Total retrieval latency.
            error: Optional error message.

        Returns:
            ``True`` on success, ``False`` on failure (best-effort).
        """
        details: dict[str, Any] = {
            "pipeline": "rag_retrieval",
            "query": query[:500],  # truncate for storage
            "query_type": query_type,
            "retrieved_chunk_ids": chunk_ids,
            "chunk_count": len(chunk_ids),
            "latency_ms": latency_ms,
        }
        if error:
            details["error"] = error

        try:
            entity_id = query_log_id or "rag_unknown"
            log_audit(
                entity_type="rag_query",
                entity_id=entity_id,
                action="RETRIEVAL",
                actor=self.actor,
                details=details,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("RetrievalAuditLog.log_retrieval failed: %s", exc)
            return False
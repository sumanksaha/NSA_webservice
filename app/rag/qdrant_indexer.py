"""Qdrant indexer + SQLAlchemy after_flush hook (Agent A, Phase 1 — Day 3).

The :class:`QdrantIndexer` is the ingestion-facing facade over the Phase 1
Day 1–2 components: it chunks legal text (:class:`~app.rag.chunker.Chunker`),
embeds the chunks (:class:`~app.rag.embedding_service.EmbeddingService`), and
upserts the resulting points (:class:`~app.rag.qdrant_client.QdrantStore`).

The after_flush hook mirrors ``app/search/indexer.py``'s FTS5 auto-index
pattern (``_on_after_flush`` + idempotent ``register_search_hooks()``): a
``Session`` ``after_flush`` listener keeps the Qdrant index in sync with any
ORM model registered via :func:`register_chunk_model` (per-chunk rows, e.g.
the planned ``LegalChunk``) or :func:`register_document_model` (document rows
whose deletion removes all of its chunks).  No models are registered by
default, so the hook is completely inert until the ``LegalChunk`` /
``LegalDocument`` models land (Phase 3, Day 12) — the hook then starts
syncing with zero further wiring.

Unlike FTS5 (same database, same transaction), Qdrant is an external store:
all hook work happens inside ``after_flush`` but is wrapped so a Qdrant or
embedding failure never breaks the caller's transaction — the FTS5
error-swallowing contract.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.rag.chunker import Chunk, Chunker
from app.rag.embedding_service import EmbeddingService
from app.rag.qdrant_client import Point, QdrantStore
from app.rag.sparse_embedding import SparseEmbeddingService

logger = logging.getLogger(__name__)

#: §5.1 payload fields that map onto ``Chunk`` optional (defaulted) fields.
#: Used when rebuilding a :class:`Chunk` from a raw payload dict.
_CHUNK_OPTIONAL_FIELDS = frozenset(Chunk.__dataclass_fields__) - {
    "chunk_id",
    "document_id",
    "chunk_index",
    "chunk_text",
}

# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #


@dataclass
class ChunkIngestionResult:
    """Outcome of an ingestion / sync operation.

    Mirrors the ``SaveResult`` pattern from
    ``app/services/document_lifecycle.py`` (§2.2) — ``ok`` is True only when
    every chunk was upserted and no errors were recorded.
    """

    document_id: str = ""
    document_type: str = ""
    chunk_count: int = 0
    points_upserted: int = 0
    vector_size: int = 0
    embedding_model: str = ""
    latency_ms: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the operation succeeded end-to-end."""
        return not self.errors and self.points_upserted == self.chunk_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "document_type": self.document_type,
            "chunk_count": self.chunk_count,
            "points_upserted": self.points_upserted,
            "vector_size": self.vector_size,
            "embedding_model": self.embedding_model,
            "latency_ms": self.latency_ms,
            "errors": list(self.errors),
            "ok": self.ok,
        }


# --------------------------------------------------------------------------- #
# QdrantIndexer
# --------------------------------------------------------------------------- #


class QdrantIndexer:
    """Chunk -> embed -> upsert facade over the Day 1–2 components.

    Args:
        store: Optional pre-built :class:`QdrantStore` (injected for tests).
        embedder: Optional pre-built :class:`EmbeddingService` (injected).
        chunker: Optional pre-built :class:`Chunker` (injected).
        sparse_embedder: Optional pre-built :class:`SparseEmbeddingService`
            (injected for tests; built lazily in production).
        collection_name: Target Qdrant collection (Phase 1 — multi-domain).
            When no ``store`` is injected, the default store is built against
            this collection instead of ``RAG_QDRANT_COLLECTION``.
    """

    def __init__(
        self,
        store: QdrantStore | None = None,
        embedder: EmbeddingService | None = None,
        chunker: Chunker | None = None,
        sparse_embedder: SparseEmbeddingService | None = None,
        collection_name: str | None = None,
    ) -> None:
        self._store = store or QdrantStore(collection_name=collection_name)
        self._embedder = embedder or EmbeddingService()
        self._chunker = chunker or Chunker()
        self._sparse_embedder = sparse_embedder

    @property
    def chunker(self) -> Chunker:
        """The chunker used by this indexer (exposed for pipeline reuse)."""
        return self._chunker

    # ------------------------------------------------------------------ #
    # Collection management
    # ------------------------------------------------------------------ #

    def ping(self) -> bool:
        """Health probe through the underlying store."""
        return self._store.ping()

    @property
    def sparse_enabled(self) -> bool:
        """Whether BM25 sparse vectors are enabled for ingestion.

        Reads ``RAG_ENABLE_SPARSE`` (default true).  Sparse vectors are only
        upserted when the collection actually declares them (see
        :meth:`_embed_sparse`) — so existing dense-only collections keep
        working unchanged until they are recreated.
        """
        try:
            from flask import current_app

            return bool(current_app.config.get("RAG_ENABLE_SPARSE", True))
        except Exception:
            return True

    def ensure_collection(self, create_payload_indexes: bool = True) -> bool:
        """Create the configured collection (+ §5.1 payload indexes) if missing.

        New collections are created with the BM25 sparse vector when
        ``RAG_ENABLE_SPARSE`` is on; existing collections are untouched.
        """
        if isinstance(self._store, QdrantStore):
            return self._store.ensure_collection(
                create_payload_indexes=create_payload_indexes,
                sparse_enabled=self.sparse_enabled,
            )
        return self._store.ensure_collection(create_payload_indexes=create_payload_indexes)

    # ------------------------------------------------------------------ #
    # Ingestion
    # ------------------------------------------------------------------ #

    def index_document(
        self,
        text: str,
        document: dict[str, Any] | None = None,
    ) -> ChunkIngestionResult:
        """Chunk, embed, and upsert a full legal document.

        Args:
            text: Clean legal document text.
            document: Optional document-level metadata passed to the chunker
                (§5.1 fields: ``document_id``, ``title``/``document_title``,
                ``type``, ``authority``, ...).

        Returns:
            :class:`ChunkIngestionResult` — ``ok`` False (with ``errors``)
            when chunking, embedding, or the Qdrant upsert fails.
        """
        chunks = self._chunker.chunk_text(text, document)
        result = self.sync_chunks(chunks)
        if document:
            result.document_id = str(document.get("document_id") or result.document_id)
            result.document_type = str(document.get("type") or result.document_type)
        return result

    def sync_chunks(self, chunks: list[Chunk]) -> ChunkIngestionResult:
        """Embed and upsert pre-built :class:`Chunk` objects."""
        return self.sync_payloads([c.to_payload() for c in chunks])

    def sync_payloads(self, payloads: list[dict[str, Any]]) -> ChunkIngestionResult:
        """Embed and upsert raw §5.1 payload dicts (used by the after_flush hook).

        Chunk payloads are rebuilt into :class:`Chunk` objects so the same
        embed + upsert path serves corpus ingestion and ORM sync.
        """
        start = time.monotonic()
        first = payloads[0] if payloads else {}
        result = ChunkIngestionResult(
            document_id=str(first.get("document_id") or ""),
            document_type=str(first.get("document_type") or ""),
            chunk_count=len(payloads),
            vector_size=self._embedder.vector_size,
            embedding_model=self._chunker.embedding_model,
        )
        if not payloads:
            result.latency_ms = int((time.monotonic() - start) * 1000)
            return result

        if not self._embedder.validate_vector_size():
            result.errors.append(
                "embedding model vector size does not match RAG_VECTOR_SIZE "
                "(the Qdrant collection dimension) — aborting sync"
            )
            result.latency_ms = int((time.monotonic() - start) * 1000)
            return result

        chunks = [self._chunk_from_payload(p) for p in payloads]
        try:
            vectors = self._embedder.embed_chunks(chunks)
            sparse_vectors = self._embed_sparse(chunks)
            if sparse_vectors is not None:
                points = [
                    Point(id=c.chunk_id, vector=v, sparse_vector=sv, payload=c.to_payload())
                    for c, v, sv in zip(chunks, vectors, sparse_vectors, strict=True)
                ]
            else:
                points = [
                    Point(id=c.chunk_id, vector=v, payload=c.to_payload())
                    for c, v in zip(chunks, vectors, strict=True)
                ]
        except Exception as exc:
            # Covers embedding failures AND a mismatched vector/chunk count.
            result.errors.append(f"embedding failed: {exc}")
            result.latency_ms = int((time.monotonic() - start) * 1000)
            return result

        if self._upsert_with_retry(points, result):
            result.points_upserted = len(points)
        result.latency_ms = int((time.monotonic() - start) * 1000)
        return result

    # ------------------------------------------------------------------ #
    # Deletion
    # ------------------------------------------------------------------ #

    def remove_chunks(self, point_ids: list[str]) -> int:
        """Delete points by chunk/point id."""
        return self._store.delete_points(point_ids=list(point_ids))

    def remove_document(self, document_id: str) -> int:
        """Delete every point belonging to a document."""
        return self._store.delete_points(document_id=document_id)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _embed_sparse(self, chunks: list[Chunk]) -> list[dict[str, list]] | None:
        """Embed BM25 sparse vectors when enabled AND the collection supports them.

        Returns ``None`` (dense-only indexing) when sparse is disabled, the
        collection is dense-only, fastembed is unavailable, or sparse
        embedding fails — never fails the whole sync.
        """
        if not self.sparse_enabled:
            return None
        try:
            has_sparse = getattr(self._store, "has_sparse_vectors", None)
            if not callable(has_sparse) or not has_sparse():
                logger.info(
                    "QdrantIndexer: collection %r has no sparse vector — indexing dense-only",
                    getattr(self._store, "collection_name", "?"),
                )
                return None
        except Exception as exc:
            logger.warning("QdrantIndexer: sparse capability check failed (%s)", exc)
            return None
        embedder = self._sparse_embedder or SparseEmbeddingService()
        if not embedder.is_available():
            logger.warning(
                "QdrantIndexer: fastembed unavailable — indexing dense-only (hybrid disabled)"
            )
            return None
        try:
            return embedder.embed_chunks(chunks)
        except Exception as exc:
            logger.warning("QdrantIndexer: sparse embedding failed (%s) — dense-only", exc)
            return None

    def _upsert_with_retry(self, points: list[Point], result: ChunkIngestionResult) -> bool:
        """Upsert with a single retry for transient Qdrant failures."""
        try:
            self._store.upsert_points(points)
            return True
        except Exception:
            try:
                self._store.upsert_points(points)
                return True
            except Exception as exc2:
                result.errors.append(f"Qdrant upsert failed after retry: {exc2}")
                return False

    @staticmethod
    def _chunk_from_payload(payload: dict[str, Any]) -> Chunk:
        """Rebuild a :class:`Chunk` from a §5.1 payload dict."""
        kwargs = {k: v for k, v in payload.items() if k in _CHUNK_OPTIONAL_FIELDS}
        return Chunk(
            chunk_id=str(payload.get("chunk_id") or uuid.uuid4()),
            document_id=str(payload.get("document_id") or ""),
            chunk_index=int(payload.get("chunk_index") or 0),
            chunk_text=str(payload.get("chunk_text") or ""),
            **kwargs,
        )


# --------------------------------------------------------------------------- #
# after_flush hook — mirrors app/search/indexer.py (FTS5 auto-index pattern)
# --------------------------------------------------------------------------- #

#: Registered model classes -> kind ("chunk" | "document").
_REGISTERED_MODELS: dict[type, str] = {}
_registered_hooks = False
_default_indexer: QdrantIndexer | None = None

#: §5.1 payload attribute names read off chunk-model instances (duck-typed).
_CHUNK_PAYLOAD_ATTRS = (
    "chunk_id",
    "document_id",
    "document_uri",
    "document_title",
    "document_type",
    "authority",
    "jurisdiction",
    "state",
    "effective_date",
    "enactment_date",
    "amended_date",
    "is_current",
    "chunk_index",
    "chunk_text",
    "chunk_char_count",
    "word_count",
    "section_number",
    "section_title",
    "subsection",
    "clause_number",
    "hierarchy_level",
    "parent_chunk_id",
    "citations",
    "references",
    "confidence",
    "created_at",
    "embedding_model",
)


def register_chunk_model(model_cls: type) -> None:
    """Register an ORM model whose rows are §5.1 chunk payloads.

    New/updated rows are upserted into Qdrant on flush; deleted rows are
    removed by point id.  This is how the planned ``LegalChunk`` model
    (Phase 3, Day 12) plugs in with no further wiring.
    """
    _REGISTERED_MODELS[model_cls] = "chunk"


def register_document_model(model_cls: type) -> None:
    """Register an ORM document model; deleting a row removes all its chunks.

    Intended for the planned ``LegalDocument`` model.
    """
    _REGISTERED_MODELS[model_cls] = "document"


def unregister_model(model_cls: type) -> None:
    """Remove a model from the sync registry (test seam)."""
    _REGISTERED_MODELS.pop(model_cls, None)


def register_legal_chunk_hooks() -> None:
    """Arm the after_flush hook for the ``LegalChunk``/``LegalDocument`` models.

    Registers ``app.models.LegalChunk`` (per-chunk rows -> point upserts /
    deletes) and ``app.models.LegalDocument`` (document deletes -> remove all
    of its chunks) with the Qdrant sync hook.

    NOTE (2026-08-08): this is a *manual* opt-in — it is deliberately NOT
    wired into ``create_app()``.  The Day 4 ingestion pipeline writes chunks
    to Qdrant directly and never flushes ``LegalChunk`` rows, so with the
    hook unarmed there is no double-embedding; call this when operators want
    ORM-driven chunk changes (e.g. SQL inserts/updates of ``LegalChunk``
    rows) to sync to Qdrant automatically.  Use :func:`set_default_indexer`
    to redirect the hook to a test/fake indexer.
    """
    from app.models import LegalChunk, LegalDocument

    register_chunk_model(LegalChunk)
    register_document_model(LegalDocument)
    logger.info("Qdrant after_flush hook armed for LegalChunk + LegalDocument")


def set_default_indexer(indexer: QdrantIndexer | None) -> None:
    """Override the indexer used by the after_flush hook (test seam)."""
    global _default_indexer
    _default_indexer = indexer


def _get_indexer() -> QdrantIndexer:
    """Return the hook's indexer, lazily building the default one."""
    global _default_indexer
    if _default_indexer is None:
        _default_indexer = QdrantIndexer()
    return _default_indexer


def _chunk_payload(obj: Any) -> dict[str, Any] | None:
    """Extract a §5.1 payload from a chunk-model instance.

    Prefers an explicit ``to_payload()`` method; otherwise duck-types the
    known §5.1 attribute names.
    """
    to_payload = getattr(obj, "to_payload", None)
    if callable(to_payload):
        try:
            payload = dict(to_payload())
            if payload.get("chunk_text") is not None or payload.get("document_id"):
                return payload
        except Exception:
            pass

    payload = {
        attr: getattr(obj, attr)
        for attr in _CHUNK_PAYLOAD_ATTRS
        if hasattr(obj, attr) and getattr(obj, attr) is not None
    }
    if not payload.get("chunk_text") and not payload.get("document_id"):
        return None
    if "chunk_id" not in payload and hasattr(obj, "id"):
        payload["chunk_id"] = str(obj.id)
    return payload


def _on_after_flush(session, _flush_context):
    """Auto-sync changed registered rows to Qdrant after each flush.

    Follows ``app/search/indexer.py::_on_after_flush``: early-returns when
    nothing relevant changed, uses the registered-models registry, and
    swallows all errors so a Qdrant/embedding failure never breaks the
    caller's transaction.
    """
    if not _REGISTERED_MODELS or not (session.new or session.dirty or session.deleted):
        return

    chunk_models = tuple(m for m, kind in _REGISTERED_MODELS.items() if kind == "chunk")
    document_models = tuple(m for m, kind in _REGISTERED_MODELS.items() if kind == "document")

    payloads: list[dict[str, Any]] = []
    delete_ids: list[str] = []
    delete_docs: list[str] = []

    # Inserts + updates
    for target in session.new | session.dirty:
        if isinstance(target, chunk_models):
            payload = _chunk_payload(target)
            if payload:
                payloads.append(payload)

    # Deletes
    for target in session.deleted:
        if isinstance(target, chunk_models):
            chunk_id = str(getattr(target, "chunk_id", None) or getattr(target, "id", "") or "")
            if chunk_id:
                delete_ids.append(chunk_id)
        elif isinstance(target, document_models):
            doc_id = str(getattr(target, "id", "") or "")
            if doc_id:
                delete_docs.append(doc_id)

    if not (payloads or delete_ids or delete_docs):
        return

    try:
        indexer = _get_indexer()
        if payloads:
            indexer.sync_payloads(payloads)
        if delete_ids:
            indexer.remove_chunks(delete_ids)
        for doc_id in delete_docs:
            indexer.remove_document(doc_id)
    except Exception as exc:
        logger.warning("Qdrant index auto-update failed: %s", exc)


def register_qdrant_hooks(indexer: QdrantIndexer | None = None) -> None:
    """Wire the after_flush listener on ``db.session``.

    Mirrors ``register_search_hooks()``: idempotent, safe to call multiple
    times, must be called after ``db.init_app(app)``.  The hook is inert
    until a model is registered via :func:`register_chunk_model` /
    :func:`register_document_model`.

    Args:
        indexer: Optional indexer to use instead of the default.  Only honoured
            on the FIRST registration (the Session listener is attached once
            per process); later callers should use :func:`set_default_indexer`.
    """
    global _registered_hooks
    if _registered_hooks:
        return
    if indexer is not None:
        set_default_indexer(indexer)
    from sqlalchemy.event import listen
    from sqlalchemy.orm import Session as _SQLASession

    listen(_SQLASession, "after_flush", _on_after_flush)
    _registered_hooks = True


# End of qdrant_indexer.py

"""RAG pipeline models for the FSSAI Legal RAG system (Agent B + Agent A).

Agent B — retrieval, generation, evaluation, observability:
- ``RAGQueryLog``      per-query retrieval log (hash-keyed for dedup/trending)
- ``RAGEvalResult``    per-query evaluation metric scores
- ``RAGEvalDataset``   ground-truth queries for batch evaluation

Agent A — corpus/embedding pipeline (Phase 3 Day 12 scope, delivered 2026-08-08):
- ``LegalDocument``    corpus document registry (``file_hash`` unique => dedup)
- ``LegalChunk``       per-chunk metadata + content hash (``content_hash``)

All tables are created by the ``add_rag_tables`` / ``add_legal_document_tables``
Alembic migrations and also picked up by ``db.create_all()`` in the app
factory fallback.

Design notes:
- ``content_hash`` (SHA-256) enables cheap dedup and fingerprint-based
  lookups — reusing the SHA-256 pattern from ``app/services/version_control.py``.
- ``retrieval_scores`` / ``retrieved_chunk_ids`` are JSON lists so the same
  schema works on both SQLite and PostgreSQL without array types.
- Indexes mirror the migration exactly to avoid ``flask db migrate`` drift.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.extensions import db


class RAGQueryLog(db.Model):
    """Per-query retrieval log for the RAG pipeline.

    Logged by :class:`app.rag.retrieval.logger.RetrievalLogger` after every
    retrieval call so operators can audit what was asked, what was retrieved,
    and how fast it was.
    """

    __tablename__ = "rag_query_log"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    query = db.Column(db.Text, nullable=False)
    query_type = db.Column(db.String(32), nullable=False)  # section_lookup | case_law | provision_search | general_qa | amendment_query
    retrieved_chunk_ids = db.Column(db.JSON, default=list)  # list of Qdrant point IDs
    retrieval_scores = db.Column(db.JSON, default=list)  # per-chunk scores
    retrieval_latency_ms = db.Column(db.Integer, nullable=True)
    context_length = db.Column(db.Integer, nullable=True)  # token count of assembled context
    llm_model = db.Column(db.String(128), nullable=True)
    prompt_tokens = db.Column(db.Integer, nullable=True)
    completion_tokens = db.Column(db.Integer, nullable=True)
    response_text = db.Column(db.Text, nullable=True)
    cited_chunk_ids = db.Column(db.JSON, default=list)  # citations extracted from response
    groundedness_score = db.Column(db.Float, nullable=True)  # 0.0–1.0
    hallucination_detected = db.Column(db.Boolean, default=False)
    hallucinated_claims = db.Column(db.JSON, default=list)
    total_latency_ms = db.Column(db.Integer, nullable=True)
    error = db.Column(db.Text, nullable=True)
    content_hash = db.Column(db.String(64), nullable=False)  # SHA-256 of query + response
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("idx_rag_query_log_created", "created_at"),
        db.Index("idx_rag_query_log_type", "query_type"),
        db.Index("idx_rag_query_log_content_hash", "content_hash"),
    )


class RAGEvalResult(db.Model):
    """Stored evaluation results for a single query against ground truth."""

    __tablename__ = "rag_eval_result"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    eval_run_id = db.Column(db.String(36), nullable=False, index=True)
    query = db.Column(db.Text, nullable=False)
    expected_answer = db.Column(db.Text, nullable=True)
    expected_citations = db.Column(db.JSON, default=list)
    actual_answer = db.Column(db.Text, nullable=True)
    actual_citations = db.Column(db.JSON, default=list)
    faithfulness_score = db.Column(db.Float, nullable=True)
    answer_relevance_score = db.Column(db.Float, nullable=True)
    context_precision_score = db.Column(db.Float, nullable=True)
    context_recall_score = db.Column(db.Float, nullable=True)
    citation_recall_score = db.Column(db.Float, nullable=True)
    groundedness_score = db.Column(db.Float, nullable=True)
    avg_score = db.Column(db.Float, nullable=True)
    retrieval_mrr = db.Column(db.Float, nullable=True)  # Mean Reciprocal Rank
    latency_ms = db.Column(db.Integer, nullable=True)
    passed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("idx_rag_eval_run", "eval_run_id"),
        db.Index("idx_rag_eval_created", "created_at"),
    )


class RAGEvalDataset(db.Model):
    """Ground-truth dataset entries for batch RAG evaluation."""

    __tablename__ = "rag_eval_dataset"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(255), nullable=False)
    query = db.Column(db.Text, nullable=False)
    query_type = db.Column(db.String(32), nullable=False)
    expected_answer = db.Column(db.Text, nullable=False)
    expected_section = db.Column(db.String(32), nullable=True)
    expected_citations = db.Column(db.JSON, default=list)
    difficulty = db.Column(db.String(16), default="medium")  # easy | medium | hard
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("idx_eval_dataset_active", "is_active"),
        db.Index("idx_eval_dataset_type", "query_type"),
    )


class LegalDocument(db.Model):
    """Corpus document registry (Agent A — scope §5.3).

    ``file_hash`` (SHA-256 of the raw file / cleaned text) is UNIQUE so
    re-ingesting the same document is naturally deduplicated at the DB level
    — the persistent backing store for the Day 5 :class:`ChunkDeduper`.
    """

    __tablename__ = "legal_document"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_uri = db.Column(db.String(512), nullable=False, unique=True)  # file path or URL
    title = db.Column(db.String(512), nullable=True)
    document_type = db.Column(db.String(32), nullable=False)  # act/rule/regulation/notification/circular/case_law
    authority = db.Column(db.String(255), nullable=True)
    jurisdiction = db.Column(db.String(255), nullable=True)
    effective_date = db.Column(db.Date, nullable=True)
    enactment_date = db.Column(db.Date, nullable=True)
    amended_date = db.Column(db.Date, nullable=True)
    is_current = db.Column(db.Boolean, default=True)
    version = db.Column(db.String(32), nullable=True)
    file_hash = db.Column(db.String(64), nullable=False, unique=True)  # SHA-256 of raw file
    status = db.Column(db.String(32), default="pending")  # pending/processing/indexed/error
    qdrant_collection = db.Column(db.String(64), default="fssai_legal_768")
    chunk_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("idx_legal_document_status", "status"),
        db.Index("idx_legal_document_type", "document_type"),
    )


class LegalChunk(db.Model):
    """Per-chunk metadata + content hash (Agent A — scope §5.2).

    ``content_hash`` is the SHA-256 of the normalized chunk text (Day 5
    dedup); ``qdrant_point_id`` back-references the Qdrant point.  The
    after_flush hook in ``app/rag/qdrant_indexer.py`` can be armed for this
    model via :func:`register_legal_chunk_hooks`.
    """

    __tablename__ = "legal_chunk"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = db.Column(db.String(36), nullable=False, index=True)
    document_type = db.Column(db.String(32), nullable=False, index=True)
    section_number = db.Column(db.String(32), index=True)
    chunk_index = db.Column(db.Integer, nullable=False, index=True)
    text = db.Column(db.Text, nullable=False)
    char_count = db.Column(db.Integer, nullable=False)
    word_count = db.Column(db.Integer, nullable=False)
    hierarchy_level = db.Column(db.Integer, default=0)
    parent_id = db.Column(db.String(36), nullable=True, index=True)
    citations = db.Column(db.JSON, default=list)  # [{"section": "55", "type": "statutory"}]
    references = db.Column(db.JSON, default=list)  # [{"target": "Section 56", "kind": "paragraph"}]
    entities = db.Column(db.JSON, default=list)  # [{"name": ..., "type": "person|organization|case|statute", "confidence": 0.85}] (§3.4)
    metadata_json = db.Column(db.JSON)  # Full Qdrant payload (read-only cache)
    content_hash = db.Column(db.String(64), nullable=False)  # SHA-256 of chunk text
    qdrant_point_id = db.Column(db.String(64), nullable=True)  # Back-reference
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("idx_legal_chunk_doc_section", "document_id", "section_number"),
        db.Index("idx_legal_chunk_parent", "parent_id"),
        db.Index("idx_legal_chunk_content_hash", "content_hash"),
        db.UniqueConstraint("document_id", "chunk_index", name="uq_chunk_doc_index"),
    )
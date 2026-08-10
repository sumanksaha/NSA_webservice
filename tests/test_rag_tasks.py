"""Tests for the RAG Celery tasks (app/rag/tasks.py) — Agent A Phase 1 Day 3.

``embed_and_index_task`` / ``run_embed_and_index`` wire the chunk -> embed ->
Qdrant index pipeline (``QdrantIndexer``) behind a Celery boundary, following
the ``retrieve_task`` / ``run_retrieval_pipeline`` pattern.

The ``QdrantIndexer`` class is monkeypatched so no Qdrant server,
sentence-transformers, or model downloads are required; one test exercises the
real graceful-degradation path (no optional deps installed -> ``ok: False``
result dict instead of an exception).
"""

from __future__ import annotations

import pytest

from app.rag.qdrant_indexer import ChunkIngestionResult
from app.rag.tasks import embed_and_index_task, ingest_corpus_task, run_embed_and_index, run_ingest_corpus


class _FakeIndexer:
    """Records what the task delegates to QdrantIndexer."""

    def __init__(self, result=None):
        self.result = result or ChunkIngestionResult(
            document_id="doc-1",
            document_type="act",
            chunk_count=2,
            points_upserted=2,
            vector_size=768,
            embedding_model="test-model",
        )
        self.calls = []

    def index_document(self, text, document=None):
        self.calls.append((text, document))
        return self.result


def _patch_indexer(monkeypatch, fake):
    """Point the task's lazy ``QdrantIndexer`` lookup at a fake."""
    monkeypatch.setattr("app.rag.qdrant_indexer.QdrantIndexer", lambda: fake)


class TestRunEmbedAndIndex:
    def test_invokes_indexer_and_returns_result_dict(self, monkeypatch):
        fake = _FakeIndexer()
        _patch_indexer(monkeypatch, fake)
        result = run_embed_and_index("doc-1", "full act text", {"type": "act"})
        assert result["document_id"] == "doc-1"
        assert result["document_type"] == "act"
        assert result["chunk_count"] == 2
        assert result["points_upserted"] == 2
        assert result["ok"] is True
        assert result["embedding_model"] == "test-model"
        assert result["errors"] == []
        # The indexer received the text + merged document metadata.
        text, document = fake.calls[0]
        assert text == "full act text"
        assert document["document_id"] == "doc-1"
        assert document["type"] == "act"

    def test_document_id_injected_when_not_in_metadata(self, monkeypatch):
        fake = _FakeIndexer()
        _patch_indexer(monkeypatch, fake)
        run_embed_and_index("doc-2", "text")
        _text, document = fake.calls[0]
        # Payload document_id must always be stamped for Qdrant filter deletes.
        assert document["document_id"] == "doc-2"

    def test_empty_text_returns_ok_zero_chunk_result(self, monkeypatch):
        fake = _FakeIndexer(result=ChunkIngestionResult(chunk_count=0, points_upserted=0))
        _patch_indexer(monkeypatch, fake)
        result = run_embed_and_index("doc-1", "")
        assert result["ok"] is True
        assert result["chunk_count"] == 0
        assert result["points_upserted"] == 0

    def test_degrades_gracefully_without_optional_deps(self):
        """Real QdrantIndexer with no sentence-transformers/qdrant-client.

        Must return a result dict with errors (not raise), proving the task
        is safe to dispatch before the optional dependencies are installed.
        """
        result = run_embed_and_index(
            "doc-1",
            "Section 3(1)(a) The Food Authority shall ensure food safety.",
        )
        # The contract that matters: an errors dict instead of an exception.
        # (The specific error differs by environment — "embedding failed" when
        # sentence-transformers is missing, "Qdrant upsert failed" when it is
        # installed but Qdrant is unconfigured.)
        assert result["ok"] is False
        assert result["errors"], "expected descriptive errors"


class TestEmbedAndIndexTask:
    def test_task_delegates_to_pipeline_and_is_registered(self, monkeypatch):
        if not _celery_available():
            pytest.skip("Celery not installed — task remains a plain function")

        fake = _FakeIndexer()
        _patch_indexer(monkeypatch, fake)
        result = embed_and_index_task("doc-1", "full act text", {"type": "act"})
        assert result["document_id"] == "doc-1"
        assert result["ok"] is True
        assert len(fake.calls) == 1  # the task really ran the pipeline
        assert embed_and_index_task.name == "rag.embed_and_index_task"


class TestIngestCorpusTask:
    def test_run_ingest_corpus_delegates_and_returns_summary(self, monkeypatch, tmp_path):
        (tmp_path / "a.txt").write_text("doc a", encoding="utf-8")
        summary = {"total": 1, "indexed": 1, "duplicates": 0, "failed": 0, "results": []}
        monkeypatch.setattr(
            "app.rag.ingestion.ingest_corpus_dir", lambda corpus_dir, document: summary
        )
        result = run_ingest_corpus(str(tmp_path), {"type": "act"})
        assert result == summary

    def test_task_registered_and_dispatches(self, monkeypatch, tmp_path):
        if not _celery_available():
            pytest.skip("Celery not installed — task remains a plain function")

        calls = {}

        def fake_ingest(corpus_dir, document):
            calls["corpus_dir"] = corpus_dir
            calls["document"] = document
            return {"total": 1, "indexed": 1, "duplicates": 0, "failed": 0, "results": []}

        monkeypatch.setattr("app.rag.ingestion.ingest_corpus_dir", fake_ingest)
        result = ingest_corpus_task(str(tmp_path), {"type": "act"})
        assert calls["corpus_dir"] == str(tmp_path)
        assert calls["document"] == {"type": "act"}
        assert result["indexed"] == 1
        assert ingest_corpus_task.name == "rag.ingest_corpus_task"


def _celery_available() -> bool:
    from app.rag import tasks

    return tasks.celery is not None

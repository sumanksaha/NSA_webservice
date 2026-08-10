"""Agent A §6.2 integration tests — QStash-scheduled batch ingestion.

``test_batch_ingestion.py`` pins the Day 4/Day 11 batch-ingestion surface:

- the ``ingest_corpus`` QStash task is registered in ``TASK_REGISTRY`` and
  resolves to ``app.rag.tasks.ingest_corpus_task``;
- ``publish_recurring`` degrades to ``{"mode": "disabled"}`` without QStash
  credentials (a scheduled corpus ingestion is never silently lost);
- the Celery wrapper ``rag.ingest_corpus_task`` carries the registered name;
- ``run_ingest_corpus`` (the schedule's entry point) delegates to
  ``ingest_corpus_dir`` and returns the JSON-serializable summary;
- batch ingestion tracks per-file progress: deterministic result order,
  per-file ok/duplicate/failure flags, and fault isolation (one bad file
  never aborts the corpus).
"""

from __future__ import annotations

from pathlib import Path

from app.rag.chunker import Chunk
from app.rag.ingestion import IngestionPipeline, ingest_corpus_dir
from app.rag.qdrant_indexer import ChunkIngestionResult

_SAMPLE_TEXT = (
    "The Food Safety and Standards Act, 2006\n\n"
    "Section 3\n\n"
    "3(1)(a) The Food Authority shall ensure food safety.\n\n"
)


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class _FakeChunker:
    def chunk_text(self, text, document=None):
        return [Chunk(chunk_id=f"c{i}", document_id="doc-1", chunk_index=i, chunk_text=f"chunk {i}") for i in range(2)]


class _FakeIndexer:
    def __init__(self):
        self.sync_calls = []

    @property
    def chunker(self):
        return _FakeChunker()

    def sync_chunks(self, chunks):
        self.sync_calls.append(list(chunks))
        return ChunkIngestionResult(document_id="doc-1", chunk_count=len(chunks), points_upserted=len(chunks))


class _FakeDocResult:
    def __init__(self, text, document_id="doc-loaded", file_type="txt"):
        self.text = text
        self.document_id = document_id
        self.file_type = file_type


class _FakeLoaderRaisesOn:
    """Loader that reads the real file content (per-file text -> distinct
    fingerprints) and raises for files named ``bad*`` (fault-isolation test)."""

    def __init__(self, ok_doc=None):
        self._ok_doc = ok_doc

    def load(self, path):
        p = Path(path)
        if p.name.startswith("bad"):
            raise ValueError(f"cannot load {path}")
        if self._ok_doc is not None:
            return self._ok_doc
        return _FakeDocResult(text=p.read_text(encoding="utf-8"), document_id=p.stem)


# --------------------------------------------------------------------------- #
# QStash schedule wiring (Phase 3 Day 11)
# --------------------------------------------------------------------------- #


class TestQstashSchedule:
    def test_task_registry_has_ingest_corpus(self):
        from app.utils.qstash_client import TASK_REGISTRY

        assert "ingest_corpus" in TASK_REGISTRY
        assert TASK_REGISTRY["ingest_corpus"] == ("app.rag.tasks", "ingest_corpus_task")

    def test_publish_recurring_degrades_when_unconfigured(self, monkeypatch):
        """Without QStash credentials the schedule is 'disabled', not an error."""
        for var in ("QSTASH_TOKEN", "QSTASH_CURRENT_SIGNING_KEY", "QSTASH_NEXT_SIGNING_KEY", "PUBLIC_BASE_URL"):
            monkeypatch.delenv(var, raising=False)

        from app.utils.qstash_client import publish_recurring

        result = publish_recurring("ingest_corpus", schedule="0 3 * * *", payload={"corpus_dir": "/tmp/corpus"})
        assert result == {"mode": "disabled"}

    def test_resolve_task_returns_celery_task(self):
        from app.rag.tasks import ingest_corpus_task
        from app.utils.qstash_client import resolve_task

        callable_ = resolve_task("ingest_corpus")
        assert callable(callable_)
        # The registry points at the Celery-wrapped task with the canonical name.
        assert callable_ is ingest_corpus_task
        assert ingest_corpus_task.name == "rag.ingest_corpus_task"

    def test_run_ingest_corpus_delegates_and_returns_summary(self, monkeypatch, tmp_path):
        from app.rag.tasks import run_ingest_corpus

        summary = {"total": 1, "indexed": 1, "duplicates": 0, "failed": 0, "results": []}
        # run_ingest_corpus imports ingest_corpus_dir locally from app.rag.ingestion.
        monkeypatch.setattr(
            "app.rag.ingestion.ingest_corpus_dir",
            lambda corpus_dir, document: summary,
        )
        result = run_ingest_corpus(str(tmp_path), {"type": "act"})
        assert result == summary
        assert isinstance(result, dict)  # JSON-serializable QStash payload


# --------------------------------------------------------------------------- #
# Batch progress tracking (§6.2)
# --------------------------------------------------------------------------- #


class TestBatchProgress:
    def test_per_file_progress_with_fault_isolation(self, tmp_path):
        """Deterministic per-file results; one bad file never aborts the corpus."""
        (tmp_path / "a.txt").write_text("document one", encoding="utf-8")
        (tmp_path / "bad.txt").write_text("will fail", encoding="utf-8")
        (tmp_path / "b.txt").write_text("document two", encoding="utf-8")

        pipeline = IngestionPipeline(
            indexer=_FakeIndexer(),
            loader=_FakeLoaderRaisesOn(),  # reads real per-file text; bad* raises
        )
        summary = ingest_corpus_dir(str(tmp_path), pipeline=pipeline)

        assert summary["total"] == 3
        assert summary["indexed"] == 2
        assert summary["failed"] == 1
        assert summary["duplicates"] == 0

        # Deterministic (sorted-glob) result order + per-file progress flags.
        # (Lexicographic: "b.txt" sorts before "bad.txt" — '.' < 'a'.)
        names = [Path(r["source_uri"]).name for r in summary["results"]]
        assert names == ["a.txt", "b.txt", "bad.txt"]
        statuses = [r["ok"] for r in summary["results"]]
        assert statuses == [True, True, False]
        failed = next(r for r in summary["results"] if not r["ok"])
        assert any("cannot load" in e for e in failed["errors"])
        # Uniform result shape (incl. quality_summary key) for consumers.
        for res in summary["results"]:
            assert set(res) >= {"document_id", "source_uri", "chunk_count", "errors", "ok", "quality_summary"}

    def test_batch_duplicates_tracked_separately(self, tmp_path):
        """A repeated batch counts files as duplicates, not failures."""
        (tmp_path / "only.txt").write_text(_SAMPLE_TEXT, encoding="utf-8")
        pipeline = IngestionPipeline(indexer=_FakeIndexer())
        first = ingest_corpus_dir(str(tmp_path), pipeline=pipeline)
        second = ingest_corpus_dir(str(tmp_path), pipeline=pipeline)

        assert first["indexed"] == 1
        assert second["duplicates"] == 1
        assert second["indexed"] == 0
        assert second["failed"] == 0


# End of test_batch_ingestion.py

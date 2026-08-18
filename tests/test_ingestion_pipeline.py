"""Tests for the Agent A Phase 1 ingestion pipeline (app/rag/ingestion.py).

Two layers:

- **Pipeline** tests inject a fake indexer (with a fake chunker) and fake
  loader/cleaner to pin the load -> clean -> dedup -> chunk -> sync flow,
  duplicate handling, and result-dict contract.
- One **integration** test runs the real ``DocumentLoaderFactory`` (txt) +
  real ``DocumentCleaner`` + real legal paragraph engine through a
  ``QdrantIndexer`` whose store/embedder are fakes — the full Day 4 flow
  with no external services or optional dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from app.rag.chunker import Chunk
from app.rag.dedup import MemoryHashStore
from app.rag.ingestion import (
    IngestionPipeline,
    ingest_corpus_dir,
    make_ingestion_pipeline,
    run_ingest_document,
)
from app.rag.qdrant_indexer import ChunkIngestionResult

_SAMPLE_TEXT = (
    "The Food Safety and Standards Act, 2006\n\n"
    "Section 3(1)\n\n"
    "3(1)(a) The Food Authority shall ensure food safety.\n"
    "3(1)(b) The Food Authority shall coordinate with State authorities.\n\n"
    "Section 14 of the Act.\n"
)


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class _FakeChunker:
    def __init__(self, chunks=None):
        self._chunks = chunks or []

    def chunk_text(self, text, document=None):
        return self._chunks


class _FakeIndexer:
    """Indexer stand-in: chunks via a fake chunker, records sync calls."""

    def __init__(self, chunks=None, sync_result=None):
        self._chunks = chunks or []
        self._sync_result = sync_result
        self.sync_calls = []

    @property
    def chunker(self):
        return _FakeChunker(self._chunks)

    def sync_chunks(self, chunks):
        self.sync_calls.append(list(chunks))
        if self._sync_result is not None:
            return self._sync_result
        return ChunkIngestionResult(
            document_id="doc-1",
            chunk_count=len(chunks),
            points_upserted=len(chunks),
        )


class _FakeLoader:
    def __init__(self, doc_result):
        self._doc_result = doc_result
        self.loaded = []

    def load(self, path):
        self.loaded.append(str(path))
        return self._doc_result


class _FakeDocResult:
    def __init__(self, text, document_id="doc-loaded", file_type="txt"):
        self.text = text
        self.document_id = document_id
        self.file_type = file_type


class _FakeCleaner:
    """Cleaner returning a plain string (documented pipeline tolerance)."""

    def __init__(self, cleaned=None):
        self._cleaned = cleaned
        self.calls = 0

    def clean(self, text):
        self.calls += 1
        return self._cleaned if self._cleaned is not None else text


class _FakeStore:
    def __init__(self):
        self.points = []

    def upsert_points(self, points):
        self.points.extend(points)
        return len(points)


class _FakeEmbedder:
    def __init__(self, vector_size=768):
        self.vector_size = vector_size

    def embed_chunks(self, chunks):
        return [[0.1] * self.vector_size for _ in chunks]

    def validate_vector_size(self, expected=None):
        return True


def _make_chunk(index, text):
    return Chunk(chunk_id=f"c{index}", document_id="doc-1", chunk_index=index, chunk_text=text)


def _make_pipeline(indexer=None, loader=None, cleaner=None, deduper=None):
    return IngestionPipeline(
        indexer=indexer or _FakeIndexer(),
        loader=loader,
        cleaner=cleaner,
        deduper=deduper,
    )


class TestIngestText:
    def test_ingest_text_happy_path(self):
        chunks = [_make_chunk(0, "alpha"), _make_chunk(1, "beta")]
        indexer = _FakeIndexer(chunks=chunks)
        pipeline = _make_pipeline(indexer=indexer)
        result = pipeline.ingest_text(_SAMPLE_TEXT, {"document_id": "doc-1", "type": "act"})
        assert result.ok
        assert result.document_id == "doc-1"
        assert result.chunk_count == 2
        assert result.points_upserted == 2
        assert result.duplicate is False
        assert result.errors == []
        assert len(result.file_hash) == 64
        assert result.text_chars == len(_SAMPLE_TEXT)
        # The indexer received the deduped chunks (with content_hash stamped).
        synced = indexer.sync_calls[0]
        assert len(synced) == 2
        assert all(c.content_hash for c in synced)

    def test_duplicate_document_skips_indexing(self):
        chunks = [_make_chunk(0, "alpha")]
        indexer = _FakeIndexer(chunks=chunks)
        pipeline = _make_pipeline(indexer=indexer)
        first = pipeline.ingest_text(_SAMPLE_TEXT, {"document_id": "doc-1"})
        assert first.ok and not first.duplicate
        second = pipeline.ingest_text(_SAMPLE_TEXT, {"document_id": "doc-1"})
        assert second.duplicate is True
        assert second.ok  # nothing to do is not an error
        assert second.chunk_count == 0
        assert len(indexer.sync_calls) == 1  # second run never reached the indexer

    def test_empty_after_cleaning_is_error(self):
        pipeline = _make_pipeline(cleaner=_FakeCleaner(cleaned="   \n  "))
        result = pipeline.ingest_text("raw text", {"document_id": "doc-1"})
        assert not result.ok
        assert any("empty after cleaning" in e for e in result.errors)

    def test_cleaner_called_and_result_serializable(self):
        cleaner = _FakeCleaner(cleaned="cleaned text")
        pipeline = _make_pipeline(
            indexer=_FakeIndexer(chunks=[_make_chunk(0, "cleaned text")]),
            cleaner=cleaner,
        )
        result = pipeline.ingest_text("raw  text", {"document_id": "doc-1"})
        assert cleaner.calls == 1
        # to_dict must be JSON-safe (task payloads travel through QStash).
        json.loads(json.dumps(result.to_dict()))


class TestIngestLoadedAndFile:
    def test_ingest_loaded_sets_document_fields_and_uri(self):
        doc = _FakeDocResult(text=_SAMPLE_TEXT, document_id="doc-loaded", file_type="txt")
        indexer = _FakeIndexer(chunks=[_make_chunk(0, "alpha")])
        pipeline = _make_pipeline(indexer=indexer)
        result = pipeline.ingest_loaded(doc, source_uri="/corpus/fss.txt")
        assert result.document_id == "doc-loaded"
        assert result.file_type == "txt"
        assert result.source_uri == "/corpus/fss.txt"

    def test_ingest_file_delegates_to_loader(self):
        doc = _FakeDocResult(text="file text", document_id="doc-f", file_type="txt")
        loader = _FakeLoader(doc)
        pipeline = _make_pipeline(
            loader=loader,
            indexer=_FakeIndexer(chunks=[_make_chunk(0, "file text")]),
        )
        result = pipeline.ingest_file("/corpus/fss.txt")
        # The pipeline passes str(Path(...)) — normalize for the OS separator.
        assert loader.loaded == [str(Path("/corpus/fss.txt"))]
        assert result.document_id == "doc-f"

    def test_run_ingest_document_dispatches_text_vs_file(self, tmp_path):
        pipeline = _make_pipeline(indexer=_FakeIndexer(chunks=[_make_chunk(0, "t")]))
        # Raw text input.
        result = run_ingest_document("plain text input", pipeline=pipeline)
        assert result["ok"] is True
        # File input.
        f = tmp_path / "doc.txt"
        f.write_text("file text", encoding="utf-8")
        result = run_ingest_document(str(f), pipeline=pipeline)
        assert result["ok"] is True
        assert result["source_uri"] == str(f)

    def test_run_ingest_document_rejects_mistyped_path(self, tmp_path):
        pipeline = _make_pipeline(indexer=_FakeIndexer(chunks=[_make_chunk(0, "t")]))
        # A path-like string that does not exist must not be ingested as text.
        missing = str(tmp_path / "nope.pdf")
        try:
            run_ingest_document(missing, pipeline=pipeline)
            raise AssertionError("expected FileNotFoundError")
        except FileNotFoundError as exc:
            assert "nope.pdf" in str(exc)


class TestChunkLevelDedup:
    def test_duplicate_chunks_filtered_across_documents(self):
        from app.rag.dedup import ChunkDeduper

        deduper = ChunkDeduper(store=MemoryHashStore())
        indexer = _FakeIndexer(chunks=[_make_chunk(0, "shared section"), _make_chunk(1, "unique A")])
        pipeline = _make_pipeline(indexer=indexer, deduper=deduper)
        first = pipeline.ingest_text("document A", {"document_id": "doc-A"})
        assert first.ok
        assert first.duplicate_chunks == 0

        # Document B shares the first chunk but introduces one new chunk.
        indexer2 = _FakeIndexer(chunks=[_make_chunk(0, "shared section"), _make_chunk(1, "unique B")])
        pipeline2 = _make_pipeline(indexer=indexer2, deduper=deduper)
        second = pipeline2.ingest_text("document B", {"document_id": "doc-B"})
        assert second.ok
        assert second.duplicate_chunks == 1
        assert second.chunk_count == 1
        synced = indexer2.sync_calls[0]
        assert [c.chunk_text for c in synced] == ["unique B"]


class _FakeLoaderRaisesOn:
    """Loader that raises for paths containing ``bad``."""

    def __init__(self, ok_doc):
        self._ok_doc = ok_doc

    def load(self, path):
        if "bad" in str(path):
            raise ValueError(f"cannot load {path}")
        return self._ok_doc


class TestProductionDefaultPipeline:
    """Day 9: the production default wires the DocumentClassifier."""

    def test_make_ingestion_pipeline_wires_classifier(self):
        from app.rag.document_classifier import DocumentClassifier

        pipeline = make_ingestion_pipeline()
        assert isinstance(pipeline.classifier, DocumentClassifier)

    def test_default_pipeline_classifies_document_metadata(self):
        """Classification runs inside the default pipeline (real classifier)."""
        from app.rag.document_classifier import DocumentClassifier

        pipeline = make_ingestion_pipeline()
        assert isinstance(pipeline.classifier, DocumentClassifier)
        enriched = pipeline.classifier.enrich_document(
            {"document_id": "doc-1"},
            "The Food Safety and Standards Act, 2006",
        )
        assert enriched["document_type"] in {"act", "rule", "regulation", "notification", "circular", "case_law"}

    def test_default_pipeline_does_not_wire_heavy_adapters(self):
        """Without RAG_FULL_ENRICHMENT, only the classifier is wired (cheap default)."""
        pipeline = make_ingestion_pipeline()
        assert pipeline._metadata_adapter is None
        assert pipeline._citation_adapter is None
        assert pipeline._crossref_adapter is None
        assert pipeline._quality_validator is None


class TestFullEnrichmentFlag:
    """Day 9 follow-up: RAG_FULL_ENRICHMENT wires the full Phase 2 chain."""

    def test_explicit_full_enrichment_wires_all_adapters(self):
        pipeline = make_ingestion_pipeline(full_enrichment=True)
        from app.rag.chunk_quality import ChunkQualityValidator
        from app.rag.citation_adapter import CitationAdapter
        from app.rag.crossref_adapter import CrossRefAdapter
        from app.rag.document_classifier import DocumentClassifier
        from app.rag.metadata_adapter import MetadataAdapter

        assert isinstance(pipeline._metadata_adapter, MetadataAdapter)
        assert isinstance(pipeline._citation_adapter, CitationAdapter)
        assert isinstance(pipeline._crossref_adapter, CrossRefAdapter)
        assert isinstance(pipeline._quality_validator, ChunkQualityValidator)
        assert isinstance(pipeline._classifier, DocumentClassifier)  # always wired

    def test_explicit_false_disables_heavy_adapters(self):
        pipeline = make_ingestion_pipeline(full_enrichment=False)
        assert pipeline._metadata_adapter is None
        assert pipeline._citation_adapter is None
        assert pipeline._crossref_adapter is None
        assert pipeline._quality_validator is None
        assert pipeline._classifier is not None  # classifier never disabled

    def test_env_flag_respected_when_no_override(self, monkeypatch):
        monkeypatch.setenv("RAG_FULL_ENRICHMENT", "true")
        pipeline = make_ingestion_pipeline()  # no override -> env flag
        assert pipeline._metadata_adapter is not None
        assert pipeline._quality_validator is not None

        monkeypatch.setenv("RAG_FULL_ENRICHMENT", "false")
        pipeline = make_ingestion_pipeline()
        assert pipeline._metadata_adapter is None

    def test_flask_config_wins_over_env_inside_app_context(self, monkeypatch):
        """Inside an app context, current_app.config takes precedence."""
        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["RAG_FULL_ENRICHMENT"] = False
        monkeypatch.setenv("RAG_FULL_ENRICHMENT", "true")
        with app.app_context():
            pipeline = make_ingestion_pipeline()
        assert pipeline._metadata_adapter is None  # config False wins

    def test_string_config_value_is_parsed_like_env(self, monkeypatch):
        """A manually-set string config value is parsed, not truthy-coerced."""
        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["RAG_FULL_ENRICHMENT"] = "false"  # string override
        with app.app_context():
            pipeline = make_ingestion_pipeline()
        assert pipeline._metadata_adapter is None  # "false" -> False

        app.config["RAG_FULL_ENRICHMENT"] = "true"
        with app.app_context():
            pipeline = make_ingestion_pipeline()
        assert pipeline._metadata_adapter is not None  # "true" -> True

    def test_full_enrichment_pipeline_end_to_end(self):
        """The full chain runs through ingest_text with a fake indexer."""
        from app.rag.chunk_quality import ChunkQualityValidator
        from app.rag.citation_adapter import CitationAdapter
        from app.rag.crossref_adapter import CrossRefAdapter
        from app.rag.document_classifier import DocumentClassifier
        from app.rag.metadata_adapter import MetadataAdapter

        chunker = _RecordingChunker([("c0", "The Food Safety and Standards Act, 2006")])
        indexer = _RecordingIndexer(chunker)
        pipeline = IngestionPipeline(
            indexer=indexer,
            metadata_adapter=MetadataAdapter(),
            citation_adapter=CitationAdapter(),
            crossref_adapter=CrossRefAdapter(),
            classifier=DocumentClassifier(),
            quality_validator=ChunkQualityValidator(),
        )
        result = pipeline.ingest_text(_SAMPLE_TEXT, {"document_id": "doc-1"})
        assert result.ok
        assert result.quality_summary is not None
        assert chunker.last_document["document_type"] in {
            "act",
            "rule",
            "regulation",
            "notification",
            "circular",
            "case_law",
        }

    def test_run_ingest_document_uses_production_default(self, monkeypatch):
        """Without an explicit pipeline, the factory's (classifier-wired) default is used."""
        chunks = [_make_chunk(0, "alpha")]
        indexer = _FakeIndexer(chunks=chunks)
        default = _make_pipeline(indexer=indexer)
        monkeypatch.setattr("app.rag.ingestion.make_ingestion_pipeline", lambda: default)
        result = run_ingest_document("plain text input")
        assert result["ok"] is True
        assert len(indexer.sync_calls) == 1

    def test_ingest_corpus_dir_uses_production_default(self, monkeypatch, tmp_path):
        (tmp_path / "a.txt").write_text("document one content", encoding="utf-8")
        chunks = [_make_chunk(0, "alpha")]
        indexer = _FakeIndexer(chunks=chunks)
        default = _make_pipeline(indexer=indexer)
        monkeypatch.setattr("app.rag.ingestion.make_ingestion_pipeline", lambda: default)
        summary = ingest_corpus_dir(str(tmp_path))
        assert summary["total"] == 1
        assert summary["indexed"] == 1


class TestCorpusDir:
    def test_ingest_corpus_dir_summary(self, tmp_path):
        (tmp_path / "a.txt").write_text("document one content", encoding="utf-8")
        (tmp_path / "b.txt").write_text("document two content", encoding="utf-8")
        (tmp_path / "ignore.md").write_text("not a corpus file", encoding="utf-8")
        pipeline = _make_pipeline(indexer=_FakeIndexer(chunks=[_make_chunk(0, "alpha")]))
        summary = ingest_corpus_dir(str(tmp_path), pipeline=pipeline)
        assert summary["total"] == 2  # .md is not a supported corpus extension
        assert summary["indexed"] == 2
        assert summary["failed"] == 0
        assert len(summary["results"]) == 2

    def test_ingest_corpus_dir_counts_failures(self, tmp_path):
        (tmp_path / "good.txt").write_text("good content", encoding="utf-8")
        (tmp_path / "bad.txt").write_text("bad content", encoding="utf-8")
        pipeline = _make_pipeline(
            loader=_FakeLoaderRaisesOn(_FakeDocResult(text="good", document_id="d1")),
            indexer=_FakeIndexer(chunks=[_make_chunk(0, "alpha")]),
        )
        summary = ingest_corpus_dir(str(tmp_path), pipeline=pipeline)
        assert summary["total"] == 2
        assert summary["indexed"] == 1
        assert summary["failed"] == 1  # one bad file must not abort the corpus
        assert any(not r["ok"] for r in summary["results"])

    def test_ingest_corpus_dir_empty(self, tmp_path):
        summary = ingest_corpus_dir(str(tmp_path), pipeline=_make_pipeline())
        assert summary["total"] == 0
        assert summary["indexed"] == 0


class _RecordingChunker:
    """Chunker that records the document dict and builds Chunks from it.

    Mirrors the real ``Chunk.from_paragraph`` document-metadata read so the
    Day 6 metadata enrichment is observable on the produced chunks.
    """

    def __init__(self, specs):
        self._specs = specs  # [(chunk_id, text), ...]
        self.last_document = {}

    def chunk_text(self, text, document=None):
        doc = dict(document or {})
        self.last_document = doc
        chunks = []
        for i, (cid, ctext) in enumerate(self._specs):
            chunks.append(
                Chunk(
                    chunk_id=cid,
                    document_id=str(doc.get("document_id") or "doc-1"),
                    chunk_index=i,
                    chunk_text=ctext,
                    document_type=doc.get("document_type") or doc.get("type") or "",
                    authority=doc.get("authority", ""),
                    jurisdiction=doc.get("jurisdiction", ""),
                )
            )
        return chunks


class _RecordingIndexer:
    """Indexer stand-in that carries the recording chunker and captures syncs."""

    def __init__(self, chunker):
        self.chunker = chunker
        self.sync_calls = []

    def sync_chunks(self, chunks):
        self.sync_calls.append(list(chunks))
        return ChunkIngestionResult(
            document_id=str(chunks[0].document_id if chunks else "doc-1"),
            chunk_count=len(chunks),
            points_upserted=len(chunks),
        )


class TestAllAdaptersTogether:
    """All four Day 6–7 adapters wired into one pipeline (real engines).

    Uses the REAL ``MetadataAdapter`` / ``CitationAdapter`` / ``CrossRefAdapter``
    / ``ChunkQualityValidator`` (their engines are offline-capable) behind a
    recording fake chunker/indexer, proving the enrichment chain: metadata →
    document dict, citations + references → per-chunk payloads, quality →
    result summary.
    """

    _SPECS: ClassVar[list[tuple[str, str]]] = [
        ("c0", "The Food Safety and Standards Act, 2006"),
        ("c1", "3(1)(a) The Food Authority shall ensure food safety. Section 14 of the Act."),
    ]

    def _pipeline(self):
        from app.rag.chunk_quality import ChunkQualityValidator
        from app.rag.citation_adapter import CitationAdapter
        from app.rag.crossref_adapter import CrossRefAdapter
        from app.rag.metadata_adapter import MetadataAdapter

        chunker = _RecordingChunker(self._SPECS)
        indexer = _RecordingIndexer(chunker)
        pipeline = IngestionPipeline(
            indexer=indexer,
            metadata_adapter=MetadataAdapter(),
            citation_adapter=CitationAdapter(),
            crossref_adapter=CrossRefAdapter(),
            quality_validator=ChunkQualityValidator(),
        )
        return pipeline, chunker, indexer

    def test_all_four_adapters_enrich_pipeline_output(self):
        pipeline, chunker, indexer = self._pipeline()
        result = pipeline.ingest_text(_SAMPLE_TEXT, {"document_id": "doc-1"})
        assert result.ok
        assert result.chunk_count == 2
        assert result.points_upserted == 2

        # (1) MetadataAdapter enriched the document dict handed to the chunker.
        doc = chunker.last_document
        assert doc["document_id"] == "doc-1"
        assert "Food Safety and Standards Act" in doc.get("document_title", "")
        assert doc.get("document_type") in {"act", "rule", "regulation", "notification", "circular", "case_law", ""}

        synced = indexer.sync_calls[0]
        assert len(synced) == 2

        # (2) CitationAdapter enriched chunk 0 with the full statute name (§2.3)
        # — and chunk 1 has none (per-chunk separation).
        assert any("Food Safety and Standards Act" in c for c in synced[0].citations)
        assert not any("Food Safety and Standards Act" in c for c in synced[1].citations)
        # (3) CrossRefAdapter enriched chunk 1 with section references — and
        # chunk 0 (the Act header) has none.
        assert "Section 14" in synced[1].references
        assert synced[0].references == []

        # (4) ChunkQualityValidator produced the aggregate quality summary.
        q = result.quality_summary
        assert q is not None
        assert q["checked"] == 2
        assert q["ok"] == 2  # no errors, both chunks >= 0.5
        assert isinstance(q["issues"], list)
        # The full result (incl. quality_summary) stays JSON-serializable.
        json.loads(json.dumps(result.to_dict()))

    def test_adapters_never_clobber_caller_metadata(self):
        pipeline, chunker, _indexer = self._pipeline()
        result = pipeline.ingest_text(
            _SAMPLE_TEXT,
            {"document_id": "doc-1", "type": "case_law", "title": "Kept Title"},
        )
        assert result.ok
        doc = chunker.last_document
        # Caller-provided values win...
        assert doc["type"] == "case_law"
        assert doc["title"] == "Kept Title"
        # ...and enrichment still ran: enrich_document ALWAYS stamps the
        # metadata_extraction cache key (engine classification is not
        # guaranteed, so don't assert on document_type itself).
        assert "metadata_extraction" in doc

    def test_adapters_are_opt_in(self):
        """Without adapters, the result has no quality_summary and plain chunks."""
        chunker = _RecordingChunker(self._SPECS)
        indexer = _RecordingIndexer(chunker)
        pipeline = IngestionPipeline(indexer=indexer)  # no adapters
        result = pipeline.ingest_text(_SAMPLE_TEXT, {"document_id": "doc-1"})
        assert result.ok
        assert result.quality_summary is None  # no quality validator wired
        synced = indexer.sync_calls[0]
        assert all(c.citations == [] and c.references == [] for c in synced)


class TestRealPipelineIntegration:
    def test_end_to_end_txt_file(self, tmp_path):
        """Real loader + cleaner + engine through fake store/embedder."""
        from app.rag.qdrant_indexer import QdrantIndexer

        f = tmp_path / "fss_act.txt"
        f.write_text(_SAMPLE_TEXT, encoding="utf-8")
        store = _FakeStore()
        indexer = QdrantIndexer(store=store, embedder=_FakeEmbedder(), chunker=None)
        pipeline = IngestionPipeline(indexer=indexer)  # real loader/cleaner/deduper
        result = pipeline.ingest_file(str(f), {"type": "act"})
        assert result.ok
        assert result.chunk_count > 0
        assert result.points_upserted == result.chunk_count
        assert result.file_type == "txt"
        assert result.source_uri == str(f)
        # Points carry §5.1 payloads incl. the dedup content_hash + uri.
        assert len(store.points) == result.chunk_count
        payload = store.points[0].payload
        assert payload["document_id"]
        assert payload["document_uri"] == str(f)
        assert len(payload["content_hash"]) == 64
        # Re-ingesting the same file is a duplicate (doc-level dedup).
        second = pipeline.ingest_file(str(f), {"type": "act"})
        assert second.duplicate is True
        assert second.ok

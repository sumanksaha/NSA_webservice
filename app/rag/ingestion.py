"""End-to-end corpus ingestion pipeline (Agent A, Phase 1 — Day 4, §4).

Wires the R0/R1 loaders and the Phase 1 components into a single flow:

    source file/text
      -> DocumentLoaderFactory (R0, ``app/document_loader``)        [files]
      -> DocumentCleaner      (R0, ``app/document_cleaner``)
      -> ChunkDeduper         (Day 5, ``app/rag/dedup.py``)         [SHA-256]
      -> Chunker -> EmbeddingService -> QdrantStore                 (Day 1-2)
      -> QdrantIndexer        (Day 3, ``app/rag/qdrant_indexer.py``)

All components are injectable via the constructor (mock-injection pattern)
so the pipeline is fully testable without a Qdrant server or
sentence-transformers.  The module itself imports nothing heavy at import
time — optional components are resolved lazily.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Supported corpus file extensions (mirrors ``DocumentLoaderFactory``).
_CORPUS_EXTENSIONS = {".pdf", ".docx", ".txt"}


@dataclass
class IngestedDocumentResult:
    """Outcome of ingesting a single document."""

    document_id: str = ""
    source_uri: str = ""
    file_type: str = ""
    file_hash: str = ""
    text_chars: int = 0
    chunk_count: int = 0
    duplicate_chunks: int = 0
    points_upserted: int = 0
    duplicate: bool = False
    latency_ms: int = 0
    errors: list[str] = field(default_factory=list)
    quality_summary: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        """True for a duplicate (nothing to do) or a fully-upserted document."""
        if self.errors:
            return False
        return self.duplicate or self.points_upserted == self.chunk_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source_uri": self.source_uri,
            "file_type": self.file_type,
            "file_hash": self.file_hash,
            "text_chars": self.text_chars,
            "chunk_count": self.chunk_count,
            "duplicate_chunks": self.duplicate_chunks,
            "points_upserted": self.points_upserted,
            "duplicate": self.duplicate,
            "latency_ms": self.latency_ms,
            "errors": list(self.errors),
            "quality_summary": self.quality_summary,
            "ok": self.ok,
        }


class IngestionPipeline:
    """Load -> clean -> dedup -> chunk -> embed -> index.

    Args:
        indexer: Optional pre-built :class:`QdrantIndexer` (injected for
            tests; default builds the real one lazily).
        loader: Optional loader factory with ``load(path)`` (default
            ``DocumentLoaderFactory``).
        cleaner: Optional cleaner with ``clean(text)`` (default
            ``DocumentCleaner`` — the pipeline reads ``clean_text`` off the
            result, or accepts a plain-string return).
        deduper: Optional :class:`ChunkDeduper` (default in-memory).
        metadata_adapter: Optional :class:`MetadataAdapter` (Phase 2 Day 6)
            — when set, extracted metadata enriches the document metadata
            before chunking (fills missing §5.1 fields only).
        citation_adapter: Optional :class:`CitationAdapter` (Phase 2 Day 6)
            — when set, each chunk's ``citations`` payload is populated from
            the per-chunk citation extraction.
        crossref_adapter: Optional :class:`CrossRefAdapter` (Phase 2 Day 7)
            — when set, each chunk's ``references`` payload is populated from
            the per-chunk cross-reference extraction.
        classifier: Optional :class:`DocumentClassifier` (Phase 2 Day 9)
            — when set, the document's §5.1 ``document_type`` / ``authority``
            are classified from the cleaned text and merged into the document
            metadata (fills missing keys only) before chunking.
        entity_extractor: Optional :class:`LegalEntityExtractor` (§3.4,
            2026-08-09) — when set, each chunk's ``entities`` payload is
            populated from the per-chunk legal-entity extraction (rule-based
            first, spaCy/LLM fallback).
        quality_validator: Optional :class:`ChunkQualityValidator` (Phase 2
            Day 7) — when set, each chunk is graded; the aggregate verdict is
            exposed on the result via ``quality_summary``.
        ocr: Optional :class:`LegalDocumentOCR` (Agent A §3.3, 2026-08-09)
            — when set, image-only PDFs (0 selectable chars, e.g. scanned
            acts) are OCR'd so they produce chunks instead of being dropped
            as empty documents. Lazy, best-effort, graceful degradation.
    """

    def __init__(
        self,
        indexer: Any | None = None,
        loader: Any | None = None,
        cleaner: Any | None = None,
        deduper: Any | None = None,
        metadata_adapter: Any | None = None,
        citation_adapter: Any | None = None,
        crossref_adapter: Any | None = None,
        classifier: Any | None = None,
        entity_extractor: Any | None = None,
        quality_validator: Any | None = None,
        ocr: Any | None = None,
    ) -> None:
        self._indexer = indexer
        self._loader = loader
        self._cleaner = cleaner
        self._deduper = deduper
        self._metadata_adapter = metadata_adapter
        self._citation_adapter = citation_adapter
        self._crossref_adapter = crossref_adapter
        self._classifier = classifier
        self._entity_extractor = entity_extractor
        self._quality_validator = quality_validator
        self._ocr = ocr

    # ------------------------------------------------------------------ #
    # Lazy component accessors
    # ------------------------------------------------------------------ #

    @property
    def indexer(self) -> Any:
        if self._indexer is None:
            from app.rag.qdrant_indexer import QdrantIndexer

            self._indexer = QdrantIndexer()
        return self._indexer

    @property
    def loader(self) -> Any:
        if self._loader is None:
            from app.document_loader import DocumentLoaderFactory

            self._loader = DocumentLoaderFactory
        return self._loader

    @property
    def cleaner(self) -> Any:
        if self._cleaner is None:
            from app.document_cleaner.pipeline import DocumentCleaner

            self._cleaner = DocumentCleaner()
        return self._cleaner

    @property
    def deduper(self) -> Any:
        if self._deduper is None:
            from app.rag.dedup import ChunkDeduper

            self._deduper = ChunkDeduper()
        return self._deduper

    @property
    def metadata_adapter(self) -> Any:
        if self._metadata_adapter is None:
            from app.rag.metadata_adapter import MetadataAdapter

            self._metadata_adapter = MetadataAdapter()
        return self._metadata_adapter

    @property
    def citation_adapter(self) -> Any:
        if self._citation_adapter is None:
            from app.rag.citation_adapter import CitationAdapter

            self._citation_adapter = CitationAdapter()
        return self._citation_adapter

    @property
    def classifier(self) -> Any:
        """Lazy :class:`DocumentClassifier` (Phase 2 Day 9, OPT-IN)."""
        if self._classifier is None:
            from app.rag.document_classifier import DocumentClassifier

            self._classifier = DocumentClassifier()
        return self._classifier

    @property
    def entity_extractor(self) -> Any:
        """Lazy :class:`LegalEntityExtractor` (§3.4, OPT-IN)."""
        if self._entity_extractor is None:
            from app.rag.entity_extractor import LegalEntityExtractor

            self._entity_extractor = LegalEntityExtractor()
        return self._entity_extractor

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def ingest_file(self, file_path: str | Path, document: dict[str, Any] | None = None) -> IngestedDocumentResult:
        """Load a supported corpus file and ingest it.

        When the ``ocr`` component is wired in and the loaded PDF has no
        selectable text (image-only scan), OCR is applied before cleaning so
        the document produces chunks instead of being dropped as empty.
        """
        path = Path(file_path)
        doc_result = self.loader.load(str(path))
        text = getattr(doc_result, "text", "") or ""

        if self._ocr is not None and str(path).lower().endswith(".pdf") and self._ocr.should_ocr(text):
            ocr_text, ocr_applied = self._ocr.fill_scanned_pdf(path, text)
            if ocr_applied and ocr_text:
                # Feed the OCR'd text through the same metadata path as
                # ``ingest_loaded`` but bypass the empty-loader-text guard.
                meta = dict(document or {})
                meta.setdefault("document_id", str(getattr(doc_result, "document_id", "") or ""))
                meta.setdefault("file_type", str(getattr(doc_result, "file_type", "") or "pdf"))
                meta["document_uri"] = str(path)
                meta["ocr_applied"] = True
                return self.ingest_text(ocr_text, meta)

        return self.ingest_loaded(doc_result, source_uri=str(path), document=document)

    def ingest_loaded(self, doc_result: Any, source_uri: str = "", document: dict[str, Any] | None = None) -> IngestedDocumentResult:
        """Ingest an already-loaded :class:`DocumentResult` (R0 adapter)."""
        text = getattr(doc_result, "text", "")
        if isinstance(text, (list, tuple)):
            text = "\n\n".join(str(p) for p in text)
        meta = dict(document or {})
        meta.setdefault("document_id", str(getattr(doc_result, "document_id", "") or ""))
        meta.setdefault("file_type", str(getattr(doc_result, "file_type", "") or ""))
        if source_uri:
            meta["document_uri"] = source_uri
        return self.ingest_text(text, meta)

    def ingest_text(self, text: str, document: dict[str, Any] | None = None) -> IngestedDocumentResult:
        """Ingest raw (or pre-cleaned) legal text.

        Deduplicates by document SHA-256 fingerprint first, then filters
        already-seen chunks, embeds, and upserts the survivors.
        """
        start = time.monotonic()
        meta = dict(document or {})
        file_hash = self.deduper.document_hash(text)

        result = IngestedDocumentResult(
            document_id=str(meta.get("document_id") or ""),
            source_uri=str(meta.get("document_uri") or ""),
            file_type=str(meta.get("file_type") or ""),
            file_hash=file_hash,
            text_chars=len(text),
        )

        if self.deduper.is_duplicate_document(text):
            result.duplicate = True
            result.latency_ms = _elapsed_ms(start)
            logger.info("ingest: document %r is a duplicate (hash %s) — skipped", result.document_id, file_hash[:10])
            return result

        cleaned = text
        if meta.get("pre_cleaned") is not True:
            cleaned = self._clean_text(text)
        if not cleaned or not cleaned.strip():
            result.errors.append("document is empty after cleaning")
            result.latency_ms = _elapsed_ms(start)
            return result

        # Phase 2 (Days 6–9): enrich document metadata + per-chunk citations /
        # references when the adapters are wired in (all OPT-IN; caller-
        # provided metadata always wins via ``enrich_document``).
        if self._metadata_adapter is not None:
            meta = self._metadata_adapter.enrich_document(meta, cleaned)
        # Day 9: document classification — fills missing document_type/authority.
        if self._classifier is not None:
            meta = self._classifier.enrich_document(meta, cleaned)

        # Chunk -> dedup at chunk level -> embed + upsert survivors.
        chunks = self.indexer.chunker.chunk_text(cleaned, meta)
        if self._citation_adapter is not None:
            chunks = [self._citation_adapter.enrich_chunk(c) for c in chunks]
        if self._crossref_adapter is not None:
            chunks = [self._crossref_adapter.enrich_chunk(c) for c in chunks]
        if self._entity_extractor is not None:
            chunks = [self._entity_extractor.enrich_chunk(c) for c in chunks]
        if self._quality_validator is not None:
            # Quality is graded on the produced chunks (pre-dedup): every chunk
            # the pipeline emitted is checked, even ones later filtered as
            # duplicates — the summary reflects corpus production quality.
            result.quality_summary = self._quality_summary(chunks)
        new_chunks, duplicate_hashes = self.deduper.filter_new(chunks)
        result.duplicate_chunks = len(duplicate_hashes)

        index_result = self.indexer.sync_chunks(new_chunks)
        result.chunk_count = index_result.chunk_count
        result.points_upserted = index_result.points_upserted
        result.errors = list(index_result.errors)

        if index_result.ok:
            # Record the document fingerprint + the newly-indexed chunk hashes
            # so a re-run skips them (document-level and chunk-level).
            self.deduper.record(chunks=new_chunks, content_hashes=[file_hash])
            if not result.document_id:
                result.document_id = str(index_result.document_id or "")
            logger.info(
                "ingest: document %r indexed — %d chunks (%d duplicate), %d points",
                result.document_id, result.chunk_count, result.duplicate_chunks, result.points_upserted,
            )
        result.latency_ms = _elapsed_ms(start)
        return result

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _quality_summary(self, chunks: list[Any]) -> dict[str, Any]:
        """Aggregate per-chunk quality verdicts into a JSON-safe summary."""
        verdicts = [self._quality_validator.validate_chunk(c) for c in chunks]
        ok = sum(1 for v in verdicts if v.ok)
        return {
            "checked": len(verdicts),
            "ok": ok,
            "failed": len(verdicts) - ok,
            "issues": [i for v in verdicts for i in v.issues],
        }

    def _clean_text(self, text: str) -> str:
        cleaned = self.cleaner.clean(text)
        # DocumentCleaner returns CleanedDocument(.clean_text); fakes may
        # return a plain string.
        return cleaned.clean_text if hasattr(cleaned, "clean_text") else str(cleaned)


def _full_enrichment_enabled() -> bool:
    """Resolve the ``RAG_FULL_ENRICHMENT`` flag (Flask config, else env).

    Reads ``current_app.config["RAG_FULL_ENRICHMENT"]`` when inside an app
    context (set by ``create_app`` from ``RAG_FULL_ENRICHMENT``); falls back
    to the environment variable so Celery/QStash/plain-function callers
    outside an app context still honour the flag.
    """
    try:
        from flask import current_app

        value = current_app.config.get("RAG_FULL_ENRICHMENT")
        if value is not None:
            # ``create_app`` stores a real bool; tolerate a string override
            # (e.g. tests setting config manually) by parsing it like env.
            if isinstance(value, bool):
                return value
            return str(value).lower() == "true"
    except Exception:  # noqa: BLE001 - outside an app context
        pass
    return os.environ.get("RAG_FULL_ENRICHMENT", "false").lower() == "true"


def make_ingestion_pipeline(full_enrichment: bool | None = None) -> IngestionPipeline:
    """Build the production-default ingestion pipeline.

    Always wires the Phase 2 Day 9 :class:`DocumentClassifier` so every
    production ingestion stamps §5.1 ``document_type`` / ``authority`` on
    the document metadata (fills missing keys only — caller metadata always
    wins).  A :class:`LegalDocumentOCR` (2026-08-09) is also wired in so
    scanned/image-only PDFs are OCR'd (graceful degradation when the OCR
    deps are absent).  When ``full_enrichment`` is enabled (explicitly, or
    via the ``RAG_FULL_ENRICHMENT`` config/env flag), the heavier Phase 2
    adapters are also wired in: :class:`MetadataAdapter` (Day 6),
    :class:`CitationAdapter` (Day 6), :class:`CrossRefAdapter` (Day 7),
    :class:`LegalEntityExtractor` (§3.4), and :class:`ChunkQualityValidator`
    (Day 7) — producing document metadata, per-chunk
    citations/references/entities, and quality summaries on every
    ingestion.  All adapters are cheap to construct and never clobber
    caller metadata.

    Args:
        full_enrichment: Explicit override; ``None`` resolves the flag from
            ``RAG_FULL_ENRICHMENT`` (Flask config, else env var, default off).
    """
    if full_enrichment is None:
        full_enrichment = _full_enrichment_enabled()
    from app.rag.document_classifier import DocumentClassifier
    from app.rag.legal_ocr import LegalDocumentOCR

    # OCR is wired in by default so scanned (image-only) PDFs — e.g. the 2
    # flagged in the 2026-08-09 corpus evaluation — produce chunks instead of
    # being dropped as empty documents. It degrades gracefully: when the OCR
    # deps (easyocr/torch/cv2) are absent, ``should_ocr``/``fill_scanned_pdf``
    # return the loader text unchanged.
    kwargs: dict[str, Any] = {"classifier": DocumentClassifier(), "ocr": LegalDocumentOCR()}
    if full_enrichment:
        from app.rag.chunk_quality import ChunkQualityValidator
        from app.rag.citation_adapter import CitationAdapter
        from app.rag.crossref_adapter import CrossRefAdapter
        from app.rag.entity_extractor import LegalEntityExtractor
        from app.rag.metadata_adapter import MetadataAdapter

        kwargs.update(
            metadata_adapter=MetadataAdapter(),
            citation_adapter=CitationAdapter(),
            crossref_adapter=CrossRefAdapter(),
            entity_extractor=LegalEntityExtractor(),
            quality_validator=ChunkQualityValidator(),
        )
    return IngestionPipeline(**kwargs)


def run_ingest_document(source: str, document: dict[str, Any] | None = None, pipeline: IngestionPipeline | None = None) -> dict[str, Any]:
    """Plain entry point: ingest a file path OR raw text, returning a dict.

    Mirrors the ``run_*`` plain-function pattern in ``app/rag/tasks.py`` so
    routes/tests/Celery can call it without the task wrapper.  When no
    pipeline is supplied, the :func:`make_ingestion_pipeline` production
    default is used (Day 9 ``DocumentClassifier`` wired in, plus the full
    Phase 2 chain when ``RAG_FULL_ENRICHMENT`` is set).

    A mistyped path is NOT silently ingested as text: if ``source`` looks
    like a file path (contains a separator or ends with a supported
    extension) but does not exist, ``FileNotFoundError`` is raised.
    """
    pipeline = pipeline or make_ingestion_pipeline()
    source_path = Path(source)
    if source_path.is_file():
        result = pipeline.ingest_file(source, document)
    elif _looks_like_path(source):
        raise FileNotFoundError(f"File not found: {source}")
    else:
        result = pipeline.ingest_text(source, document)
    return result.to_dict()


def _looks_like_path(source: str) -> bool:
    """Heuristic: contains a path separator or has a corpus extension."""
    if "/" in source or "\\" in source:
        return True
    return Path(source).suffix.lower() in _CORPUS_EXTENSIONS


def ingest_corpus_dir(
    corpus_dir: str,
    document: dict[str, Any] | None = None,
    pipeline: IngestionPipeline | None = None,
    extensions: set[str] | None = None,
) -> dict[str, Any]:
    """Ingest every supported file under ``corpus_dir`` (non-recursive scan).

    Returns a JSON-serializable summary: ``total``, ``indexed``,
    ``duplicates``, ``failed``, and per-file ``results``.  Used by the
    ``rag.ingest_corpus_task`` Celery task / QStash schedule.  When no
    pipeline is supplied, the :func:`make_ingestion_pipeline` production
    default is used (Day 9 ``DocumentClassifier`` wired in).
    """
    pipeline = pipeline or make_ingestion_pipeline()
    exts = extensions or _CORPUS_EXTENSIONS
    files = sorted(p for p in Path(corpus_dir).glob("*") if p.is_file() and p.suffix.lower() in exts)

    summary: dict[str, Any] = {"corpus_dir": str(corpus_dir), "total": 0, "indexed": 0, "duplicates": 0, "failed": 0, "results": []}
    for path in files:
        try:
            res = pipeline.ingest_file(path, document)
            summary["total"] += 1
            if res.duplicate:
                summary["duplicates"] += 1
            elif res.ok:
                summary["indexed"] += 1
            else:
                summary["failed"] += 1
            summary["results"].append(res.to_dict())
        except Exception as exc:  # noqa: BLE001 - one bad file must not abort the corpus
            logger.warning("ingest_corpus_dir: %s failed: %s", path, exc)
            summary["total"] += 1
            summary["failed"] += 1
            # Same shape as IngestedDocumentResult.to_dict() so consumers can
            # iterate ``results`` uniformly (including quality_summary).
            summary["results"].append(
                {
                    "document_id": "", "source_uri": str(path), "file_type": "",
                    "file_hash": "", "text_chars": 0, "chunk_count": 0,
                    "duplicate_chunks": 0, "points_upserted": 0, "duplicate": False,
                    "latency_ms": 0, "errors": [str(exc)], "ok": False,
                    "quality_summary": None,
                }
            )
    logger.info("ingest_corpus_dir: %s -> %s", corpus_dir, summary)
    return summary


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


# End of ingestion.py

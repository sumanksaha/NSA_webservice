"""Tests for the RAG legal-OCR adapter (Agent A §3.3, 2026-08-09).

Covers:
- ``LegalDocumentOCR`` decision logic (``should_ocr`` threshold)
- ``fill_scanned_pdf`` behaviour: OCR applied, text-pass-through, graceful
  degradation when the pipeline is unavailable / OCR yields nothing
- ``OCREngine._try_easyocr`` candidate parsing (injected engine surface)
- ``IngestionPipeline`` OCR wiring: scanned PDFs get OCR'd, text PDFs don't,
  and a missing OCR component leaves the load path unchanged

All tests use mock-injection — no EasyOCR/torch model stack is required.
"""

from __future__ import annotations

from pathlib import Path

from app.rag.ingestion import IngestionPipeline, make_ingestion_pipeline
from app.rag.legal_ocr import LegalDocumentOCR


class _FakePipeline:
    """Fake OCRPipeline: returns per-page results from a canned map."""

    def __init__(self, page_texts=None, error=None):
        self._page_texts = page_texts or {}
        self._error = error
        self.calls = []

    def process_document(self, pdf_path):
        self.calls.append(str(pdf_path))
        if self._error:
            raise self._error
        return [
            type("_R", (), {"page": int(p), "text": t})() for p, t in sorted(self._page_texts.items())
        ]


class _FakePageResult:
    pass


class TestLegalDocumentOCRDecision:
    def test_should_ocr_true_for_empty(self):
        ocr = LegalDocumentOCR()
        assert ocr.should_ocr("") is True
        assert ocr.should_ocr("   \n  ") is True

    def test_should_ocr_true_below_threshold(self):
        ocr = LegalDocumentOCR(min_text_chars=20)
        assert ocr.should_ocr("tiny") is True
        assert ocr.should_ocr("nineteen chars ok") is True  # 17 chars < 20

    def test_should_ocr_false_for_real_text(self):
        ocr = LegalDocumentOCR()
        assert ocr.should_ocr("The Food Safety and Standards Act, 2006 ... enough text") is False

    def test_custom_threshold(self):
        ocr = LegalDocumentOCR(min_text_chars=100)
        assert ocr.should_ocr("fifty chars of text that is not that long actually") is True
        assert ocr.should_ocr("x" * 120) is False


class TestFillScannedPdf:
    def test_ocr_applied_when_scanned(self):
        ocr = LegalDocumentOCR(pipeline=_FakePipeline(page_texts={1: "SECTION 3", 2: "Sub-section (a)"}))
        text, applied = ocr.fill_scanned_pdf("/corpus/scan.pdf", loaded_text="")
        assert applied is True
        assert "SECTION 3" in text
        assert "Sub-section (a)" in text

    def test_ocr_not_applied_when_text_sufficient(self):
        pipeline = _FakePipeline(page_texts={1: "should never be used"})
        ocr = LegalDocumentOCR(pipeline=pipeline)
        text, applied = ocr.fill_scanned_pdf("/corpus/normal.pdf", loaded_text="real selectable text")
        assert applied is False
        assert text == "real selectable text"
        assert pipeline.calls == []  # OCR never invoked

    def test_graceful_degradation_when_pipeline_missing(self):
        ocr = LegalDocumentOCR(pipeline=False)  # simulate unavailable
        text, applied = ocr.fill_scanned_pdf("/corpus/scan.pdf", loaded_text="")
        assert applied is False
        assert text == ""

    def test_ocr_error_returns_text_unchanged(self):
        ocr = LegalDocumentOCR(pipeline=_FakePipeline(error=RuntimeError("boom")))
        text, applied = ocr.fill_scanned_pdf("/corpus/scan.pdf", loaded_text="")
        assert applied is False
        assert text == ""

    def test_ocr_empty_result_returns_text_unchanged(self):
        ocr = LegalDocumentOCR(pipeline=_FakePipeline(page_texts={}))
        text, applied = ocr.fill_scanned_pdf("/corpus/scan.pdf", loaded_text="")
        assert applied is False
        assert text == ""

    def test_available_reflects_pipeline(self):
        assert LegalDocumentOCR(pipeline=_FakePipeline()).available() is True
        assert LegalDocumentOCR(pipeline=False).available() is False


class TestOCREngineEasyOCR:
    def test_easyocr_candidate_parsing(self):
        """recognize() returns easyocr output when the engine is mocked."""
        from app.ocr_pipeline.ocr_engine import OCREngine

        class _FakeReader:
            def readtext(self, image):
                return [
                    ([[10, 10], [100, 10], [100, 30], [10, 30]], "Section 55", 0.95),
                    ([[10, 40], [100, 40], [100, 60], [10, 60]], "of the Act", 0.88),
                    ([[10, 70], [100, 70], [100, 90], [10, 90]], "", 0.5),  # empty text dropped
                ]

        engine = OCREngine(use_gpu=False)
        engine._easyocr = _FakeReader()
        engine._easyocr_langs = ["en"]
        text, conf, engine_name, lang = engine.recognize(None)  # image unused by the fake
        assert engine_name == "easyocr"
        assert "Section 55" in text
        assert "of the Act" in text
        assert "empty text dropped" not in text
        assert 0.9 <= conf <= 1.0

    def test_easyocr_unavailable_falls_through(self, monkeypatch):
        """When easyocr cannot be imported, recognize() tries next strategies."""
        import builtins

        from app.ocr_pipeline.ocr_engine import OCREngine

        real_import = builtins.__import__

        def _blocked(name, *args, **kwargs):
            if name == "easyocr":
                raise ImportError("easyocr not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _blocked)
        engine = OCREngine(use_gpu=False)
        text, conf, engine_name, _lang = engine.recognize(None)  # no engines work
        assert engine_name == "none"
        assert text == ""

    def test_easyocr_lang_mapping(self):
        from app.ocr_pipeline.ocr_engine import OCREngine

        engine = OCREngine(languages=["english", "hindi", "bengali"], use_gpu=False)
        assert engine._to_easyocr_langs() == ["en", "hi", "bn"]
        engine = OCREngine(languages=["hindi", "hindi"], use_gpu=False)
        assert engine._to_easyocr_langs() == ["hi"]
        engine = OCREngine(languages=["klingon"], use_gpu=False)
        assert engine._to_easyocr_langs() == ["en"]  # unknown -> English fallback


class _FakeLoaderPdf:
    """Loader returning a fake PDF DocumentResult with canned text/file_type."""

    def __init__(self, text="", file_type="pdf"):
        self._text = text
        self._file_type = file_type
        self.loaded = []

    def load(self, path):
        self.loaded.append(str(path))
        return _FakeDocResult(text=self._text, document_id="doc-pdf", file_type=self._file_type)


class _FakeDocResult:
    def __init__(self, text, document_id="doc-pdf", file_type="pdf"):
        self.text = text
        self.document_id = document_id
        self.file_type = file_type


class TestIngestionOCRWiring:
    """Uses the REAL LegalDocumentOCR with an injected fake pipeline."""

    def _pipeline_with_ocr(self, fake_pipeline, loader_text="", loader_file_type="pdf"):
        from app.rag.dedup import ChunkDeduper, MemoryHashStore
        from tests.test_ingestion_pipeline import _FakeIndexer

        ocr = LegalDocumentOCR(pipeline=fake_pipeline)
        return (
            IngestionPipeline(
                indexer=_FakeIndexer(chunks=[]),
                loader=_FakeLoaderPdf(text=loader_text, file_type=loader_file_type),
                deduper=ChunkDeduper(store=MemoryHashStore()),
                ocr=ocr,
            ),
            ocr,
        )

    def test_scanned_pdf_is_ocrd_and_metadata_routed(self):
        fake = _FakePipeline(page_texts={1: "OCR CONTENT Section 3"})
        pipeline, ocr = self._pipeline_with_ocr(fake)
        result = pipeline.ingest_file("/corpus/scan.pdf", {"document_id": "doc-pdf"})
        assert result.ok
        assert result.text_chars == len("OCR CONTENT Section 3")
        # Metadata routed through the OCR path (reviewer contract):
        assert result.document_id == "doc-pdf"
        assert result.file_type == "pdf"
        assert result.source_uri == str(Path("/corpus/scan.pdf"))

    def test_text_pdf_skips_ocr(self):
        fake = _FakePipeline(page_texts={1: "should never be used"})
        pipeline, _ocr = self._pipeline_with_ocr(fake, loader_text="real selectable pdf text")
        result = pipeline.ingest_file("/corpus/normal.pdf", {"document_id": "doc-pdf"})
        assert fake.calls == []  # OCR never invoked for text PDFs
        assert result.ok

    def test_non_pdf_empty_file_skips_ocr(self):
        """The OCR fallback is PDF-only — an empty txt must not route to OCR."""
        fake = _FakePipeline(page_texts={1: "should never be used"})
        pipeline, _ocr = self._pipeline_with_ocr(fake, loader_text="", loader_file_type="txt")
        result = pipeline.ingest_file("/corpus/empty.txt", {"document_id": "doc-txt"})
        assert fake.calls == []  # txt never reaches OCR
        assert not result.ok
        assert any("empty after cleaning" in e for e in result.errors)

    def test_no_ocr_component_leaves_load_path_unchanged(self):
        from app.rag.dedup import ChunkDeduper, MemoryHashStore
        from tests.test_ingestion_pipeline import _FakeIndexer

        pipeline = IngestionPipeline(
            indexer=_FakeIndexer(chunks=[]),
            loader=_FakeLoaderPdf(text=""),
            deduper=ChunkDeduper(store=MemoryHashStore()),
        )
        # Empty scanned PDF without OCR -> empty-after-cleaning error (graceful).
        result = pipeline.ingest_file("/corpus/scan.pdf", {"document_id": "doc-pdf"})
        assert not result.ok
        assert any("empty after cleaning" in e for e in result.errors)

    def test_production_default_wires_ocr(self):
        from app.rag.legal_ocr import LegalDocumentOCR

        pipeline = make_ingestion_pipeline()
        assert isinstance(pipeline._ocr, LegalDocumentOCR)

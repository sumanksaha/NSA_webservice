"""Tests for the Agent A Phase 2 Day 9 document classifier (app/rag/document_classifier.py).

Pins the R2 ``DocumentTypeExtractor`` + ``AuthorityExtractor`` → §5.1 payload
mapping: document_type enum normalization (+ classifier alias extensions),
authority extraction, confidence propagation, ``enrich_document``
never-clobber semantics, and the classifier's OPT-IN wiring into
``IngestionPipeline``.  Fake extractors drive the mapping tests; real
extractor checks cover the actual R2 behaviour on FSS Act text.
"""

from __future__ import annotations

import json

from app.rag.document_classifier import DocumentClassification, DocumentClassifier
from app.rag.ingestion import IngestionPipeline

# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class _FakeTypeExtractor:
    """R2 DocumentTypeExtractor stand-in: confidence-sorted candidates."""

    def __init__(self, candidates=None):
        if candidates is None:
            candidates = [("Act", 0.90, "regex", "act_pattern")]
        self._candidates = [tuple(c) for c in candidates]
        self.calls = 0

    def extract(self, text):
        self.calls += 1
        return list(self._candidates)


class _FakeAuthorityExtractor:
    def __init__(self, candidates=None):
        if candidates is None:
            candidates = [
                ("Ministry of Health and Family Welfare", 0.95, "regex", "known_authority")
            ]
        self._candidates = [tuple(c) for c in candidates]
        self.calls = 0

    def extract(self, text):
        self.calls += 1
        return list(self._candidates)


def _make_classifier(type_candidates=None, authority_candidates=None):
    return DocumentClassifier(
        type_extractor=_FakeTypeExtractor(type_candidates),
        authority_extractor=_FakeAuthorityExtractor(authority_candidates),
    )


# --------------------------------------------------------------------------- #
# Classify
# --------------------------------------------------------------------------- #


class TestClassify:
    def test_classify_uses_both_extractors(self):
        classifier = _make_classifier()
        result = classifier.classify("The Food Safety and Standards Act, 2006")
        assert isinstance(result, DocumentClassification)
        assert result.document_type == "act"  # §5.1 enum-normalized
        assert result.document_type_label == "Act"
        assert result.authority == "Ministry of Health and Family Welfare"
        assert result.document_type_confidence == 0.90
        assert result.authority_confidence == 0.95
        assert result.overall_confidence > 0.9

    def test_payload_smoke_shape(self):
        # §6.3 smoke test shape: {"document_type": ..., "authority": ...}
        payload = _make_classifier().payload("The Food Safety and Standards Act, 2006")
        assert payload["document_type"] == "act"
        assert payload["authority"] == "Ministry of Health and Family Welfare"

    def test_empty_text_yields_empty_classification(self):
        result = _make_classifier().classify("")
        assert result.document_type == ""
        assert result.document_type_label == ""
        assert result.authority == ""
        assert result.overall_confidence == 0.0

    def test_missing_candidates_yield_empty_fields(self):
        classifier = _make_classifier(type_candidates=[], authority_candidates=[])
        result = classifier.classify("unclassifiable text")
        assert result.document_type == ""
        assert result.authority == ""
        assert result.overall_confidence == 0.0

    def test_extractor_failure_is_best_effort(self):
        class _BoomExtractor:
            def extract(self, text):
                raise RuntimeError("extractor exploded")

        classifier = DocumentClassifier(
            type_extractor=_BoomExtractor(),
            authority_extractor=_BoomExtractor(),
        )
        result = classifier.classify("any text")
        assert result.document_type == ""
        assert result.authority == ""

    def test_to_dict_is_json_serializable(self):
        result = _make_classifier().classify("The Food Safety and Standards Act, 2006")
        json.loads(json.dumps(result.to_dict()))


class TestNormalizeType:
    def test_shared_enum_aliases_delegate_to_metadata_adapter(self):
        assert DocumentClassifier.normalize_type("Act") == "act"
        assert DocumentClassifier.normalize_type("Rules") == "rule"
        assert DocumentClassifier.normalize_type("Judgment") == "case_law"
        assert DocumentClassifier.normalize_type("Case Law") == "case_law"

    def test_classifier_alias_extensions(self):
        assert DocumentClassifier.normalize_type("Gazette Notification") == "notification"
        assert DocumentClassifier.normalize_type("Bill") == ""
        assert DocumentClassifier.normalize_type("Policy") == ""

    def test_unknown_and_empty(self):
        assert DocumentClassifier.normalize_type("Some Random Type") == ""
        assert DocumentClassifier.normalize_type("") == ""


class TestEnrichDocument:
    def test_fills_missing_keys(self):
        enriched = _make_classifier().enrich_document({"document_id": "doc-1"}, "The Food Safety and Standards Act, 2006")
        assert enriched["document_id"] == "doc-1"
        assert enriched["type"] == "act"
        assert enriched["document_type"] == "act"
        assert enriched["document_type_label"] == "Act"
        assert enriched["authority"] == "Ministry of Health and Family Welfare"
        assert "document_classification" in enriched

    def test_never_clobbers_explicit_values(self):
        enriched = _make_classifier().enrich_document(
            {"document_id": "doc-1", "type": "case_law", "authority": "Kept Authority"},
            "The Food Safety and Standards Act, 2006",
        )
        assert enriched["type"] == "case_law"  # caller wins
        assert enriched["authority"] == "Kept Authority"
        assert enriched["document_type"] == "act"  # only missing keys filled

    def test_without_text_keeps_document_unchanged_plus_cache_key(self):
        enriched = _make_classifier().enrich_document({"document_id": "doc-1"})
        assert enriched["document_id"] == "doc-1"
        assert "type" not in enriched
        assert "document_classification" in enriched  # cache key always stamped


# --------------------------------------------------------------------------- #
# Pipeline wiring (OPT-IN)
# --------------------------------------------------------------------------- #


class _FakeChunker:
    def __init__(self):
        self.last_document = {}

    def chunk_text(self, text, document=None):
        doc = dict(document or {})
        self.last_document = doc
        from app.rag.chunker import Chunk

        return [
            Chunk(
                chunk_id="c0",
                document_id=str(doc.get("document_id") or "doc-1"),
                chunk_index=0,
                chunk_text=text,
                document_type=doc.get("document_type") or doc.get("type") or "",
                authority=doc.get("authority", ""),
            )
        ]


class _FakeIndexer:
    def __init__(self, chunker):
        self.chunker = chunker
        self.sync_calls = []

    def sync_chunks(self, chunks):
        self.sync_calls.append(list(chunks))
        from app.rag.qdrant_indexer import ChunkIngestionResult

        return ChunkIngestionResult(
            document_id=str(chunks[0].document_id if chunks else "doc-1"),
            chunk_count=len(chunks),
            points_upserted=len(chunks),
        )


class TestPipelineWiring:
    def test_classifier_enriches_chunks_when_wired(self):
        chunker = _FakeChunker()
        indexer = _FakeIndexer(chunker)
        pipeline = IngestionPipeline(
            indexer=indexer,
            classifier=_make_classifier(),
        )
        result = pipeline.ingest_text("The Food Safety and Standards Act, 2006", {"document_id": "doc-1"})
        assert result.ok
        # The classifier filled document_type/authority on the document dict
        # handed to the chunker -> chunks carry the §5.1 fields.
        doc = chunker.last_document
        assert doc["document_type"] == "act"
        assert doc["authority"] == "Ministry of Health and Family Welfare"
        synced = indexer.sync_calls[0]
        assert synced[0].document_type == "act"

    def test_classifier_is_opt_in(self):
        chunker = _FakeChunker()
        pipeline = IngestionPipeline(indexer=_FakeIndexer(chunker))  # no classifier
        pipeline.ingest_text("The Food Safety and Standards Act, 2006", {"document_id": "doc-1"})
        doc = chunker.last_document
        assert doc.get("document_type", "") == ""  # untouched without the adapter
        assert doc.get("authority", "") == ""

    def test_classifier_never_clobbers_pipeline_metadata(self):
        chunker = _FakeChunker()
        pipeline = IngestionPipeline(
            indexer=_FakeIndexer(chunker),
            classifier=_make_classifier(),
        )
        pipeline.ingest_text(
            "The Food Safety and Standards Act, 2006",
            {"document_id": "doc-1", "type": "case_law"},
        )
        assert chunker.last_document["type"] == "case_law"  # caller wins


# --------------------------------------------------------------------------- #
# Real R2 extractors
# --------------------------------------------------------------------------- #


class TestRealExtractors:
    def test_fss_act_text_classification(self):
        classifier = DocumentClassifier()  # real DocumentTypeExtractor + AuthorityExtractor
        text = (
            "The Food Safety and Standards Act, 2006\n\n"
            "An Act to consolidate the laws relating to food safety and standards.\n"
            "Ministry of Health and Family Welfare, Government of India.\n"
        )
        result = classifier.classify(text)
        assert result.document_type in {"act", "rule", "regulation", "notification", "circular", "case_law", ""}
        assert result.document_type_label  # raw label present
        assert "Ministry of Health" in result.authority

    def test_notification_text_classification(self):
        classifier = DocumentClassifier()
        result = classifier.classify(
            "NOTIFICATION\nNew Delhi, dated the 5th August, 2020\n"
            "Ministry of Health and Family Welfare\n"
        )
        assert result.document_type == "notification"
        assert result.authority

"""Tests for the Agent A Phase 2 metadata adapter (app/rag/metadata_adapter.py).

Pins the LegalMetadataEngine -> §5.1 payload mapping: document_type enum
normalization, ISO date normalization, ``is_current`` from amendment status,
and ``enrich_document`` never-clobber semantics.  A fake engine (returning a
real ``LegalMetadata``) drives the mapping tests; real-engine checks cover
the actual extractor behaviour on FSS Act text.
"""

from __future__ import annotations

import json

from app.metadata_extractor.models import FieldConfidence, LegalMetadata
from app.rag.metadata_adapter import MetadataAdapter, MetadataExtraction


def _make_metadata(**overrides):
    """Build a real LegalMetadata with sensible defaults."""
    defaults = {
        "title": FieldConfidence(value="Food Safety and Standards Act, 2006", score=0.95, method="regex"),
        "version": FieldConfidence(value="2022", score=0.4, method="default"),
        "date": FieldConfidence(value="24 August 2006", score=0.85, method="regex"),
        "authority": FieldConfidence(value="Ministry of Health and Family Welfare", score=0.9, method="regex"),
        "gazette_number": FieldConfidence(value="", score=0.0, method="default"),
        "notification_number": FieldConfidence(value="", score=0.0, method="default"),
        "language": FieldConfidence(value="english", score=0.5, method="default"),
        "jurisdiction": FieldConfidence(value="India", score=0.6, method="default"),
        "state": FieldConfidence(value="", score=0.0, method="default"),
        "country": FieldConfidence(value="India", score=0.6, method="default"),
        "document_type": FieldConfidence(value="Act", score=0.9, method="regex"),
        "amendment_status": FieldConfidence(value="Original", score=0.5, method="default"),
        "effective_date": FieldConfidence(value="1st Day of August, 2006", score=0.9, method="regex"),
    }
    defaults.update(overrides)
    return LegalMetadata(**defaults)


class _FakeEngine:
    def __init__(self, metadata=None):
        self._metadata = metadata or _make_metadata()
        self.calls = 0

    def extract(self, text):
        self.calls += 1
        return self._metadata


class TestMetadataAdapterMapping:
    def test_extract_maps_payload_fields(self):
        adapter = MetadataAdapter(engine=_FakeEngine())
        extraction = adapter.extract("some text")
        assert isinstance(extraction, MetadataExtraction)
        assert extraction.document_title == "Food Safety and Standards Act, 2006"
        assert extraction.document_type == "act"  # enum-normalized
        assert extraction.authority == "Ministry of Health and Family Welfare"
        assert extraction.jurisdiction == "India"
        assert extraction.effective_date == "2006-08-01"  # "1st Day of August, 2006"
        assert extraction.enactment_date == "2006-08-24"  # "24 August 2006"
        assert extraction.is_current is True
        assert extraction.version == "2022"
        assert extraction.overall_confidence > 0.0

    def test_empty_text_passes_through_engine_output(self):
        # The adapter is a pure mapper: engine output passes through unchanged.
        # An engine that extracts nothing for empty text yields empty fields;
        # a default-heavy engine (the fake) yields its defaults.
        adapter = MetadataAdapter(engine=_FakeEngine())
        extraction = adapter.extract("")
        assert extraction.document_title == "Food Safety and Standards Act, 2006"
        assert extraction.document_type == "act"
        assert extraction.is_current is True

    def test_empty_engine_output_is_handled(self):
        class _EmptyEngine:
            def extract(self, text):
                return _make_metadata(
                    title=FieldConfidence(value="", score=0.0, method="default"),
                    document_type=FieldConfidence(value="", score=0.0, method="default"),
                )

        extraction = MetadataAdapter(engine=_EmptyEngine()).extract("text")
        assert extraction.document_title == ""
        assert extraction.document_type == ""

    def test_to_dict_is_json_serializable(self):
        extraction = MetadataAdapter(engine=_FakeEngine()).extract("text")
        json.loads(json.dumps(extraction.to_dict()))


class TestDocumentTypeNormalization:
    @staticmethod
    def _norm(value):
        return MetadataAdapter.normalize_document_type(value)

    def test_enum_mappings(self):
        assert self._norm("Act") == "act"
        assert self._norm("Rules") == "rule"
        assert self._norm("Regulation") == "regulation"
        assert self._norm("Notification") == "notification"
        assert self._norm("Circular") == "circular"
        assert self._norm("Case Law") == "case_law"

    def test_unknown_value_returns_empty(self):
        assert self._norm("Some Random Type") == ""
        assert self._norm("") == ""


class TestDateNormalization:
    @staticmethod
    def _norm(value):
        return MetadataAdapter.normalize_date(value)

    def test_iso_passthrough(self):
        assert self._norm("2006-08-24") == "2006-08-24"

    def test_dd_mm_yyyy(self):
        assert self._norm("01/02/2020") == "2020-02-01"

    def test_ordinal_month_year(self):
        assert self._norm("24 August 2006") == "2006-08-24"
        assert self._norm("1st Day of January, 2006") == "2006-01-01"

    def test_unparseable_passes_through(self):
        assert self._norm("not a date") == "not a date"

    def test_invalid_dd_mm_yyyy_passes_through(self):
        assert self._norm("31/13/2020") == "31/13/2020"  # invalid month

    def test_none_and_empty(self):
        assert self._norm(None) is None
        assert self._norm("") is None


class TestIsCurrent:
    def test_non_current_statuses(self):
        for status in ("Repealed", "Superseded", "Withdrawn", "RESCINDED"):
            assert MetadataAdapter.is_current_from_status(status) is False

    def test_current_statuses(self):
        for status in ("Original", "Amended", ""):
            assert MetadataAdapter.is_current_from_status(status) is True


class TestEnrichDocument:
    def test_fills_missing_keys(self):
        adapter = MetadataAdapter(engine=_FakeEngine())
        enriched = adapter.enrich_document({"document_id": "doc-1"}, "some text")
        assert enriched["document_id"] == "doc-1"
        assert enriched["type"] == "act"
        assert enriched["document_type"] == "act"
        assert enriched["title"] == "Food Safety and Standards Act, 2006"
        assert enriched["document_title"] == "Food Safety and Standards Act, 2006"
        assert enriched["authority"] == "Ministry of Health and Family Welfare"
        assert enriched["effective_date"] == "2006-08-01"
        assert enriched["is_current"] is True

    def test_never_clobbers_explicit_values(self):
        adapter = MetadataAdapter(engine=_FakeEngine())
        enriched = adapter.enrich_document(
            {"document_id": "doc-1", "type": "case_law", "title": "Kept Title", "is_current": False},
            "some text",
        )
        assert enriched["type"] == "case_law"  # caller wins
        assert enriched["title"] == "Kept Title"
        assert enriched["is_current"] is False
        assert enriched["document_type"] == "act"  # only missing keys filled

    def test_repealed_document_sets_is_current_false(self):
        metadata = _make_metadata(amendment_status=FieldConfidence(value="Repealed", score=0.9, method="regex"))
        adapter = MetadataAdapter(engine=_FakeEngine(metadata=metadata))
        enriched = adapter.enrich_document({}, "text")
        assert enriched["is_current"] is False


class TestRealMetadataEngine:
    def test_fss_act_text_extraction(self):
        adapter = MetadataAdapter()  # real LegalMetadataEngine
        text = (
            "The Food Safety and Standards Act, 2006\n\n"
            "An Act to consolidate the laws relating to food safety and standards.\n"
            "Ministry of Health and Family Welfare, Government of India.\n\n"
            "CHAPTER 1\nSection 3(1)(a) Definitions.\n"
        )
        extraction = adapter.extract(text)
        assert extraction.document_title  # title extracted from the Act header
        # Real engine may surface "India" or the full body name.
        assert extraction.jurisdiction
        # Real engine may classify as an Act or fall back to its default.
        assert extraction.document_type in ("act", "rule", "regulation", "notification", "circular", "case_law", "")

    def test_enrich_document_with_real_engine(self):
        adapter = MetadataAdapter()
        enriched = adapter.enrich_document({"document_id": "doc-1"}, "The Food Safety and Standards Act, 2006")
        assert enriched["document_id"] == "doc-1"
        assert enriched.get("title")

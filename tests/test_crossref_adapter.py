"""Tests for the Agent A Phase 2 cross-reference adapter (app/rag/crossref_adapter.py).

Pins the CrossReferenceEngine -> §5.1/§5.2 mapping: raw reference strings for
the Qdrant payload ``references`` field, structured ``{"target", "kind"}``
dicts for the ``LegalChunk.references`` JSON column, and full-Act section
knowledge (reused ``FSS_ACT_SECTIONS`` — the app's pinned ``KNOWN_SECTIONS``
is untouched).

A fake engine (returning real :class:`CrossReference` objects) drives the
mapping tests; real-engine checks cover actual extraction on FSS Act text.
"""

from __future__ import annotations

import json

from app.cross_reference import CrossReference, ReferenceKind
from app.rag.crossref_adapter import AdaptedReference, CrossRefAdapter


def _ref(kind, target, raw, confidence=0.9):
    return CrossReference(
        kind=kind,
        target=target,
        raw=raw,
        position=0,
        context="... context ...",
        confidence=confidence,
    )


class _FakeEngine:
    def __init__(self, refs):
        self._refs = refs
        self.calls = 0

    def extract_references(self, text):
        self.calls += 1
        return list(self._refs)


class _FakeChunk:
    def __init__(self, chunk_text, references=None):
        self.chunk_text = chunk_text
        self.references = references


class TestCrossRefAdapterMapping:
    def test_extract_maps_reference_fields(self):
        adapter = CrossRefAdapter(
            engine=_FakeEngine([
                _ref(ReferenceKind.SECTION, "55", "Section 55"),
                _ref(ReferenceKind.ANNEXURE, "A", "Annexure A"),
                _ref(ReferenceKind.PARAGRAPH, "3", "paragraph 3"),
            ])
        )
        extracted = adapter.extract("text")
        assert len(extracted) == 3
        section, annexure, paragraph = extracted
        assert isinstance(section, AdaptedReference)
        assert (section.kind, section.target, section.raw) == ("section", "55", "Section 55")
        assert section.known is True  # 55 is a real FSS Act section
        assert (annexure.kind, annexure.target) == ("annexure", "A")
        assert annexure.known is None  # not a section ref
        assert paragraph.kind == "paragraph"
        assert paragraph.confidence == 0.9

    def test_extract_skips_empty_raw(self):
        adapter = CrossRefAdapter(engine=_FakeEngine([_ref(ReferenceKind.SECTION, "55", "")]))
        assert adapter.extract("text") == []

    def test_to_dict_is_json_serializable(self):
        adapter = CrossRefAdapter(engine=_FakeEngine([_ref(ReferenceKind.SECTION, "55", "Section 55")]))
        ref = adapter.extract("text")[0]
        json.loads(json.dumps(ref.to_dict()))


class TestPayloadReferences:
    def test_payload_references_returns_raw_strings(self):
        adapter = CrossRefAdapter(
            engine=_FakeEngine([
                _ref(ReferenceKind.SECTION, "55", "Section 55"),
                _ref(ReferenceKind.ANNEXURE, "A", "Annexure A"),
            ])
        )
        assert adapter.payload_references("text") == ["Section 55", "Annexure A"]


class TestStructuredReferences:
    def test_structured_references_shape(self):
        adapter = CrossRefAdapter(
            engine=_FakeEngine([
                _ref(ReferenceKind.SECTION, "55", "Section 55"),
                _ref(ReferenceKind.PARAGRAPH, "3", "paragraph 3"),
            ])
        )
        structured = adapter.structured_references("text")
        assert structured == [
            {"target": "Section 55", "kind": "section"},
            {"target": "paragraph 3", "kind": "paragraph"},
        ]


class TestEnrichChunk:
    def test_enrich_chunk_sets_references_from_text(self):
        adapter = CrossRefAdapter(engine=_FakeEngine([_ref(ReferenceKind.SECTION, "55", "Section 55")]))
        chunk = _FakeChunk(chunk_text="See Section 55 of the Act")
        adapter.enrich_chunk(chunk)
        assert chunk.references == ["Section 55"]

    def test_enrich_chunk_skips_empty_text(self):
        adapter = CrossRefAdapter(engine=_FakeEngine([]))
        chunk = _FakeChunk(chunk_text="", references=None)
        adapter.enrich_chunk(chunk)
        assert chunk.references is None

    def test_enrich_chunk_returns_chunk(self):
        adapter = CrossRefAdapter(engine=_FakeEngine([]))
        chunk = _FakeChunk(chunk_text="plain text")
        assert adapter.enrich_chunk(chunk) is chunk


class TestSectionKnowledge:
    def test_known_sections_covers_full_act(self):
        adapter = CrossRefAdapter()
        sections = adapter.known_sections
        assert "1" in sections and "104" in sections
        assert "55" in sections and "26" in sections
        assert "105" not in sections  # Act ends at 104

    def test_is_known_section_strips_subclauses(self):
        adapter = CrossRefAdapter()
        assert adapter.is_known_section("26") is True
        assert adapter.is_known_section("26(2)(ii)") is True  # base section 26
        assert adapter.is_known_section("199") is False
        assert adapter.is_known_section("") is False

    def test_app_known_sections_untouched(self):
        """The adapter must NOT expand the app's pinned KNOWN_SECTIONS."""
        from app.cross_reference.engine import KNOWN_SECTIONS

        assert frozenset({"3", "26", "37", "46", "51", "52", "55", "56", "58", "63", "64"}) == KNOWN_SECTIONS
        assert "1" not in KNOWN_SECTIONS  # app set stays small; adapter has full Act


class TestRealCrossReferenceEngine:
    def test_extracts_section_run(self):
        adapter = CrossRefAdapter()  # real engine
        refs = adapter.extract("Liability under Section 55 and Sections 56, 58 and 64 of the Act.")
        sections = [r for r in refs if r.kind == "section"]
        assert {r.target for r in sections} >= {"55", "56", "58", "64"}
        assert all(r.known for r in sections)  # all real FSS Act sections

    def test_extracts_annexure_and_paragraph(self):
        adapter = CrossRefAdapter()
        refs = adapter.extract("See Annexure A and paragraph 3 above.")
        kinds = {r.kind for r in refs}
        assert "annexure" in kinds and "paragraph" in kinds
        assert any(r.target == "A" for r in refs)
        assert any(r.target == "3" and r.kind == "paragraph" for r in refs)

    def test_payload_and_structured_with_real_engine(self):
        adapter = CrossRefAdapter()
        payload = adapter.payload_references("Section 55 and Annexure B")
        structured = adapter.structured_references("Section 55 and Annexure B")
        assert "Section 55" in payload and "Annexure B" in payload
        assert {"target": "Section 55", "kind": "section"} in structured

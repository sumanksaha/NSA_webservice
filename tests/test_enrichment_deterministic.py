"""Tests for Phase 3 deterministic enrichment and Phase 12 validation."""

from __future__ import annotations

from app.rag.enrichment.deterministic import (
    attribute_sections,
    build_deterministic_record,
    build_section_index,
    enrich_document,
    extract_crossref_candidates,
    extract_keywords,
    find_act_document,
    legal_location_of,
    resolve_cross_references,
    sha256,
)
from app.rag.enrichment.validation import validate_record


def _point(cid: str, doc: str = "doc-1", *, text: str = "Body text.", index: int = 0,
           section: str | None = None, title: str | None = None,
           doc_type: str = "regulation", citations: list | None = None,
           references: list | None = None, entities: list | None = None,
           uri: str = "file:///corpus/a.pdf", content_hash: str | None = None) -> dict:
    payload = {
        "chunk_id": cid, "document_id": doc, "document_uri": uri,
        "document_title": "Food Safety and Standards (Licensing) Regulations, 2011",
        "document_type": doc_type, "chunk_text": text, "chunk_index": index,
        "chunk_char_count": len(text), "word_count": len(text.split()),
        "section_number": section, "section_title": title,
        "citations": citations or [], "references": references or [],
        "entities": entities or [], "hierarchy_level": 0,
        "content_hash": content_hash or sha256(text),
    }
    return {"id": cid, "payload": payload}


# --------------------------------------------------------------------------- #
# Section attribution (paragraph inheritance)
# --------------------------------------------------------------------------- #


def test_section_inheritance_after_header() -> None:
    pts = [
        _point("h", text="Section 32: Cognizance of offences", section="32", title="Cognizance of offences", index=0),
        _point("a", text="No court shall take cognizance...", index=1),
        _point("b", text="Any offence may be compounded...", index=2),
    ]
    out = attribute_sections(pts)
    assert out["h"]["section"] == "32" and out["h"]["inherited"] is False
    assert out["a"]["section"] == "32" and out["a"]["inherited"] is True
    assert out["b"]["section"] == "32" and out["b"]["inherited"] is True


def test_header_claims_new_section() -> None:
    pts = [
        _point("a", text="Body before any header.", index=0),
        _point("h", text="Section 55: Appellate authority", section="55", index=1),
        _point("b", text="The appellate authority shall...", index=2),
    ]
    out = attribute_sections(pts)
    assert out["a"]["section"] is None
    assert out["h"]["section"] == "55"
    assert out["b"]["section"] == "55"


def test_body_reference_does_not_claim_section() -> None:
    # "subject to section 32" is a reference, not the chunk's own section.
    pts = [_point("x", text="This is subject to section 32 of the Act.", index=0)]
    out = attribute_sections(pts)
    assert out["x"]["section"] is None


def test_header_line_without_payload_section() -> None:
    pts = [_point("h", text="Section 12: Appeal", index=0), _point("b", text="Follower.", index=1)]
    out = attribute_sections(pts)
    assert out["h"]["section"] == "12"
    assert out["b"]["section"] == "12"


# --------------------------------------------------------------------------- #
# Legal location / keywords
# --------------------------------------------------------------------------- #


def test_legal_location_act_family_and_schedule() -> None:
    pl = _point("x", text="Provided in Schedule 2 of these regulations.")["payload"]
    attr = {"section": "32", "title": "", "inherited": True}
    loc = legal_location_of(pl, attr)
    assert loc["act"]["value"] == "Food Safety and Standards Act, 2006"
    assert loc["section"]["value"] == "32"
    assert loc["schedule"]["value"] == "2"


def test_keywords_sparse_and_no_stopwords() -> None:
    kw = extract_keywords("The Food Business Operator shall comply with the Improvement Notice.")
    joined = " ".join(kw)
    assert "Improvement" in joined
    assert all(w.lower() not in {"the", "shall", "with"} for w in kw)
    assert len(extract_keywords("")) == 0


# --------------------------------------------------------------------------- #
# Cross-reference candidates + resolution
# --------------------------------------------------------------------------- #


def test_crossref_candidates_from_citations() -> None:
    pl = _point("x", citations=["Section 55", {"section": "56", "reference": "Section 56"}])["payload"]
    cands = extract_crossref_candidates(pl)
    assert {c["target"] for c in cands} == {"Section 55", "Section 56"}
    assert all(c["resolved"] is False for c in cands)


def test_crossref_resolution_same_doc_then_act() -> None:
    index = {
        ("doc-1", "55"): ["t1"],
        ("act-9", "55"): ["t2"],
    }
    cands = [
        {"target": "Section 55", "section": "55", "relation": "REFERS_TO",
         "resolved": False, "source": "deterministic", "evidence": "Section 55"},
        {"target": "Section 12", "section": "12", "relation": "REFERS_TO",
         "resolved": False, "source": "deterministic", "evidence": "Section 12"},
    ]
    # same-doc wins; unknown section stays unresolved; act fallback only for doc-1
    out1 = resolve_cross_references(cands, index, "doc-1", "act-9")
    assert out1[0]["resolved"] is True and out1[0]["target_chunk_id"] == "t1"
    assert out1[1]["resolved"] is False and out1[1]["target_chunk_id"] is None

    # doc-2 has no section 55 chunk -> resolves to the Act chunk
    out2 = resolve_cross_references([dict(cands[0])], index, "doc-2", "act-9")
    assert out2[0]["resolved"] is True and out2[0]["target_chunk_id"] == "t2"

    # ambiguous (two act chunks with section 55) stays unresolved
    index2 = {("act-9", "55"): ["t2", "t3"]}
    out3 = resolve_cross_references([dict(cands[0])], index2, "doc-2", "act-9")
    assert out3[0]["resolved"] is False


def test_self_reference_and_duplicate_edges_dropped() -> None:
    # A section-32 chunk that cites section 32 (own section) must not produce
    # a self-loop; two mentions resolving to the same target collapse to one.
    pts = [
        _point("c32", doc="doc-1", text="Under section 32 no court shall take cognizance.",
               section="32", citations=["Section 32", "Section 32"], index=0),
    ]
    index = {("doc-1", "32"): ["c32"]}
    records = enrich_document(pts, index, None)
    xr = records[0]["cross_references"]
    assert xr == []  # both resolved to self and were dropped


def test_duplicate_resolved_target_collapses() -> None:
    # Two mentions of the same target section (payload citation + body text)
    # resolve to one edge.
    pts = [
        _point("a", doc="doc-1",
               text="This is subject to section 55 of the Act.",
               citations=["Section 55"], index=0),
    ]
    index = {("doc-1", "55"): ["t55"]}
    records = enrich_document(pts, index, None)
    xr = records[0]["cross_references"]
    assert len(xr) == 1
    assert xr[0]["resolved"] is True and xr[0]["target_chunk_id"] == "t55"


def test_section_index_and_act_discovery() -> None:
    pts = [
        _point("a", doc="act-9", doc_type="act", text="Food Safety and Standards Act, 2006",
               section="32", index=0),
        _point("b", doc="reg-1", text="...", section="4", index=0),
        _point("c", doc="reg-1", text="...", index=1),
    ]
    index = build_section_index(pts)
    assert index[("act-9", "32")] == [("a", 0)]  # (chunk_id, chunk_index) tuples
    assert find_act_document(pts) == "act-9"


def test_crossref_multi_chunk_section_anchors_first() -> None:
    # A section spanning several chunks resolves to its first (header) chunk
    # with a confidence penalty + anchor marker, not to "ambiguous".
    cands = [
        {"target": "Section 55", "section": "55", "relation": "REFERS_TO",
         "resolved": False, "source": "deterministic", "evidence": "Section 55"},
    ]
    index = {("doc-1", "55"): [("t55a", 3), ("t55h", 0), ("t55c", 7)]}
    out = resolve_cross_references(cands, index, "doc-1", None)
    assert out[0]["resolved"] is True
    assert out[0]["target_chunk_id"] == "t55h"  # min chunk_index = anchor
    assert out[0]["anchor"] is True
    assert out[0]["confidence"] == 0.7


def test_crossref_zero_targets_not_ambiguous() -> None:
    cands = [
        {"target": "Section 999", "section": "999", "relation": "REFERS_TO",
         "resolved": False, "source": "deterministic", "evidence": "Section 999"},
    ]
    out = resolve_cross_references(cands, {}, "doc-1", "act-9")
    assert out[0]["resolved"] is False
    assert out[0]["target_chunk_id"] is None
    assert "ambiguous_targets" not in out[0]  # unresolvable, not ambiguous


# --------------------------------------------------------------------------- #
# Record assembly + full document enrichment
# --------------------------------------------------------------------------- #


def test_build_record_sparse_and_provenance() -> None:
    pl = _point("x", section="32")["payload"]
    rec = build_deterministic_record(
        {"id": "x", "payload": pl},
        {"section": "32", "title": "", "inherited": True},
        [],
    )
    assert rec["enrichment_version"] == "1.0"
    assert rec["status"] == "ENRICHED"
    assert rec["provenance"]["llm_used"] is False
    assert rec["legal_location"]["section"]["source"] == "deterministic"
    assert rec["retrieval_summary"]["source"] == "unknown"
    assert rec["original_text"] == pl["chunk_text"]
    assert rec["original_sha256"] == pl["content_hash"]


def test_enrich_document_end_to_end_with_resolution() -> None:
    pts = [
        _point("h", doc="reg-1", text="Regulation 2.1: Definitions", section="2.1", index=0),
        _point("a", doc="reg-1", text="Subject to section 32 of the Act.", citations=["Section 32"], index=1),
        _point("b", doc="reg-1", text="Follower clause.", index=2),
    ]
    index = {("act-9", "32"): ["t32"], ("reg-1", "2.1"): ["h"]}
    records = enrich_document(pts, index, "act-9")
    by_id = {r["chunk_id"]: r for r in records}
    assert by_id["a"]["legal_location"]["section"]["value"] == "2.1"  # inherited
    xr = by_id["a"]["cross_references"][0]
    assert xr["resolved"] is True and xr["target_chunk_id"] == "t32"  # act fallback


# --------------------------------------------------------------------------- #
# Phase 12 validation invariants
# --------------------------------------------------------------------------- #


def _record(cid: str = "x") -> dict:
    return build_deterministic_record(
        _point(cid, section="32"),
        {"section": "32", "title": "", "inherited": True},
        [],
    )


def test_validation_ok_for_deterministic_record() -> None:
    rec = _record()
    vr = validate_record(rec, _point("x", section="32")["payload"])
    assert vr.ok, vr.issues


def test_validation_immutability_mismatch() -> None:
    rec = _record()
    rec["original_text"] = "TAMPERED"
    vr = validate_record(rec, _point("x")["payload"])
    assert not vr.ok
    assert any("original_text differs" in i for i in vr.issues)


def test_validation_hash_mismatch() -> None:
    rec = _record()
    rec["original_sha256"] = "deadbeef"
    vr = validate_record(rec, _point("x", section="32")["payload"])
    assert not vr.ok
    assert any("original_sha256 differs" in i for i in vr.issues)


def test_validation_llm_explicit_needs_evidence() -> None:
    rec = _record()
    rec["obligations"] = [{"actor": "FBO", "action": "comply", "source": "llm", "kind": "explicit"}]
    vr = validate_record(rec, _point("x", section="32")["payload"])
    assert not vr.ok
    assert any("lacks evidence_span" in i for i in vr.issues)


def test_validation_resolved_requires_target() -> None:
    rec = _record()
    rec["cross_references"] = [
        {"target": "Section 55", "section": "55", "relation": "REFERS_TO",
         "resolved": True, "target_chunk_id": None, "confidence": 0.5}
    ]
    vr = validate_record(rec, _point("x", section="32")["payload"])
    assert not vr.ok
    assert any("lacks target_chunk_id" in i for i in vr.issues)


def test_validation_unresolved_must_not_carry_target() -> None:
    rec = _record()
    rec["cross_references"] = [
        {"target": "Section 55", "relation": "REFERS_TO", "resolved": False,
         "target_chunk_id": "t1", "confidence": 0.5}
    ]
    vr = validate_record(rec, _point("x", section="32")["payload"])
    assert not vr.ok
    assert any("unresolved cross_reference carries target_chunk_id" in i for i in vr.issues)


def test_validation_duplicate_entities() -> None:
    rec = _record()
    rec["entities"] = [
        {"name": "FSSAI", "type": "authority"},
        {"name": "FSSAI", "type": "authority"},
    ]
    vr = validate_record(rec, _point("x", section="32")["payload"])
    assert not vr.ok
    assert any("duplicate entity" in i for i in vr.issues)


def test_validation_confidence_range() -> None:
    rec = _record()
    rec["confidence"] = 1.7
    vr = validate_record(rec, _point("x", section="32")["payload"])
    assert not vr.ok
    assert any("out of [0, 1]" in i for i in vr.issues)

"""Tests for the corpus-wide KG backfill index (P0 payload fix)."""

from __future__ import annotations

from app.rag.retrieval.result import RetrievedChunk
from kg.backfill import build_index_from_chunks, build_index_from_payloads
from kg.hybrid import backfill_key, backfill_key_variants


def test_payload_index_uses_act_name_not_document_title():
    """``document_title`` is the file title; the KG instrument is ``act_name``."""
    payload = {
        "document_title": "Compendium Licensing Regulations 04 08 2021",
        "act_name": "Food Safety and Standards Act, 2006",
        "section_number": "57",
        "chunk_text": "No person shall manufacture without a licence.",
    }
    index = build_index_from_payloads([payload])
    assert backfill_key("Food Safety and Standards Act, 2006", "57") in index
    assert backfill_key("Compendium Licensing Regulations 04 08 2021", "57") not in index


def test_payload_index_uses_clause_number_when_section_is_null():
    payload = {
        "act_name": "Food Safety and Standards Act, 2006",
        "section_number": None,
        "clause_number": "3.04",
        "chunk_text": "Processing tables shall have a cleanable top.",
    }
    index = build_index_from_payloads([payload])
    assert backfill_key("Food Safety and Standards Act, 2006", "3.04") in index


def test_payload_index_indexes_provision_spans_and_ids():
    payload = {
        "act_name": "Food Safety and Standards Act, 2006",
        "provision_spans": [{"section": "s57", "subsection": []}],
        "sections_covered": ["59"],
        "provision_ids": ["fssai:s58"],
        "chunk_text": "Licence condition body.",
    }
    index = build_index_from_payloads([payload])
    assert backfill_key("Food Safety and Standards Act, 2006", "57") in index
    assert backfill_key("Food Safety and Standards Act, 2006", "58") in index
    assert backfill_key("Food Safety and Standards Act, 2006", "59") in index


def test_longest_body_wins_and_leading_article_normalises():
    payloads = [
        {
            "act_name": "The Food Safety and Standards Act, 2006",
            "section_number": "16",
            "chunk_text": "short",
        },
        {
            "act_name": "Food Safety and Standards Act, 2006",
            "section_number": "16",
            "chunk_text": "a much longer statutory body " * 10,
        },
    ]
    index = build_index_from_payloads(payloads)
    key = backfill_key("Food Safety and Standards Act, 2006", "16")
    assert index[key].startswith("a much longer statutory body")


def test_build_index_from_chunks_uses_act_name_and_clause_number():
    chunk = RetrievedChunk(
        chunk_id="c1",
        score=0.9,
        text="Clause body text.",
        document_title="Compendium Licensing Regulations",
        act_name="Food Safety and Standards Act, 2006",
        clause_number="3.04",
    )
    index = build_index_from_chunks([chunk])
    key = backfill_key("Food Safety and Standards Act, 2006", "3.04")
    assert index[key] == "Clause body text."


def test_backfill_key_variants_cover_graph_and_corpus_forms():
    variants = backfill_key_variants("Food Safety and Standards Act, 2006", "fssai:s57")
    assert backfill_key("Food Safety and Standards Act, 2006", "57") in variants


def test_cross_domain_mapper_prefers_longest_chunk_and_merges_ids():
    from kg.queries import _row_to_cross_domain_result

    out = _row_to_cross_domain_result(
        {
            "provision_id": "P1",
            "provision_number": "16",
            "title": "Duties",
            "provision_text": "full provision text",
            "status": "current",
            "domain": "FOOD_SAFETY",
            "instrument_title": "Food Safety and Standards Act, 2006",
            "authorities": ["FSSAI"],
            "chunk_texts": ["Section 16", "A much longer supporting chunk body."],
            "source_chunk_ids": ["c16"],
            "source_qdrant_ids": ["q16"],
        }
    )
    assert out["provision_text"] == "full provision text"
    assert out["status"] == "current"
    assert out["chunk_text"] == "A much longer supporting chunk body."
    assert out["source_chunk_ids"] == ["c16", "q16"]

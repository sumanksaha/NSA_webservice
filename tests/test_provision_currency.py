"""Provision currency agreement: one question, one verdict.

"Is this chunk current?" used to bounce across three modules with three
vocabularies (payload status in ``temporal_validity``, repeal-regex in
``sufficiency``, version families in ``provision_versions``). These tests
pin that the verdicts agree — the interface is the test surface.
"""

from types import SimpleNamespace

from app.rag.agent.sufficiency import _temporal_conflicts, chunk_temporally_invalid
from app.rag.retrieval import evidence_selector
from app.rag.retrieval.legal_hierarchy import parse_section_chain
from app.rag.retrieval.provision_versions import group_versions, is_current_version
from app.rag.retrieval.temporal_validity import VALIDITY_INVALID, VALIDITY_VALID, is_valid


def _chunk(chunk_id, text, status="unknown", **kwargs):
    return SimpleNamespace(
        chunk_id=chunk_id,
        text=text,
        status=status,
        act_name=kwargs.get("act_name", "Food Safety and Standards Act, 2006"),
        section_number=kwargs.get("section_number", "31"),
        effective_from=kwargs.get("effective_from"),
        effective_to=kwargs.get("effective_to"),
    )


def _validity(chunk):
    return is_valid(chunk.chunk_id, chunk=chunk, allow_graph=False).status


def test_payload_repeal_without_repeal_words_is_invalid_everywhere():
    # The locality bug: payload says repealed, text never mentions it.
    chunk = _chunk("c1", "Section 31 lays down the penalty.", status="repealed")
    assert _validity(chunk) == VALIDITY_INVALID
    assert chunk_temporally_invalid(chunk.__dict__) is True


def test_text_repeal_without_payload_is_invalid_everywhere():
    chunk = _chunk("c2", "Section 31 repealed by the 2023 Amendment Act.")
    assert _validity(chunk) == VALIDITY_INVALID
    assert chunk_temporally_invalid(chunk.__dict__) is True


def test_omitted_vocabulary_survives_the_seam():
    chunk = _chunk("c3", "Section 218 omitted by the Amendment Act, 1984.")
    assert _validity(chunk) == VALIDITY_INVALID
    assert chunk_temporally_invalid(chunk.__dict__) is True


def test_substituted_text_is_invalid():
    chunk = _chunk("c3b", "Section 12 substituted by the 2021 Amendment Act.")
    assert _validity(chunk) == VALIDITY_INVALID
    assert chunk_temporally_invalid(chunk.__dict__) is True


def test_plain_amendment_is_not_invalid():
    chunk = _chunk("c4", "Section 31 as amended by the 2020 Rules.", status="current")
    assert _validity(chunk) == VALIDITY_VALID
    assert chunk_temporally_invalid(chunk.__dict__) is False


def test_repeal_then_reenact_resolves_valid():
    chunk = _chunk(
        "c5",
        "Section 31 repealed by the 2020 Act. Section 31 re-enacted by the 2022 Act.",
    )
    assert _validity(chunk) == VALIDITY_VALID
    assert chunk_temporally_invalid(chunk.__dict__) is False


def test_version_family_agrees_with_validity():
    current = _chunk("c1", "Section 31 lays down the penalty.", status="current")
    old = _chunk("c2", "Section 31 repealed by the 2023 Act.", status="repealed")
    families = group_versions([current, old])
    assert len(families) == 1
    assert is_current_version(current, next(iter(families.values()))) is True
    assert is_current_version(old, next(iter(families.values()))) is False
    assert _validity(current) == VALIDITY_VALID
    assert _validity(old) == VALIDITY_INVALID


def test_temporal_conflicts_uses_the_seam():
    task = SimpleNamespace(temporal_scope="2021")
    repealed = {"chunk_id": "c1", "text": "Section 31 lays down the penalty.", "status": "repealed"}
    fresh = {"chunk_id": "c2", "text": "Section 32 defines food.", "status": "current"}
    assert _temporal_conflicts([repealed, fresh], task) == ["c1"]


def test_chain_parsing_has_one_home():
    assert not hasattr(evidence_selector, "section_base_chain")
    assert not hasattr(evidence_selector, "parse_chain")
    assert parse_section_chain("31(2)(a)") == ["31", "2", "a"]

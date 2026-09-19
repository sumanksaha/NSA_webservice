"""Tests for the three-stage reranker (app/rag/planning/three_stage_reranker.py).

Regression: the scorer read ``chunk.metadata`` / ``QueryClassifier.QueryType``,
neither of which exists — any ``rerank()`` call raised ``AttributeError``.
Scores now read the real ``RetrievedChunk`` fields (``act_name`` /
``section_number`` / ``authority``) and the module-level ``QueryType``.
"""

from __future__ import annotations

from app.rag.planning.three_stage_reranker import ThreeStageReranker
from app.rag.retrieval.result import RetrievedChunk


def _chunk(section=None, act=None, authority=None):
    return RetrievedChunk(
        chunk_id=f"c-{section}",
        score=0.5,
        text=f"text for section {section}",
        section_number=section,
        act_name=act or "",
        authority=authority or "",
    )


def test_rerank_runs_without_attribute_error():
    """Previously crashed on chunk.metadata on every call."""
    out = ThreeStageReranker().rerank(
        "penalty under section 51 of FSS Act",
        [_chunk("51", "FSS Act", "FSSAI")],
    )
    assert len(out) == 1


def test_identity_scoring_prefers_act_and_section_match():
    out = ThreeStageReranker().rerank(
        "penalty under section 51 of FSS Act",
        [_chunk("52", "FSS Act", "FSSAI"), _chunk("51", "FSS Act", "FSSAI")],
    )
    assert [c.chunk_id for c in out] == ["c-51", "c-52"]


def test_rerank_empty_is_noop():
    assert ThreeStageReranker().rerank("anything", []) == []

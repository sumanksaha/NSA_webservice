"""Tests for V8 evidence-set selector.

No Qdrant / network / Flask context required.
"""

from __future__ import annotations

import pytest

from evaluation.benchmark import BenchmarkQuestion, GoldUnit
from evaluation.evidence_set_selector import (
    CandidateItem,
    HybridEvidenceSetSelector,
    HierarchyAwareSelector,
    LegalStructureDiversitySelector,
    MMRSelector,
    TopKSelector,
    STRATEGIES,
    STRATEGY_NAMES,
    _jaccard,
    _tokenize,
    build_candidates,
    candidates_to_arm_result,
    compute_redundancy,
)
from evaluation.metrics import score_question
from evaluation.resolution import FamilyMap


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _chunk(
    key, score=0.5, rank=1, text="", family="fssai", section=None,
    doc_id="doc1", hl=1, chunk_index=None,
    act_name="Food Safety and Standards Act, 2006",
    sections_covered=None, parent_chunk_id=None,
):
    sk = [(family, section)] if family else [(None, section)]
    if sections_covered:
        for sec in sections_covered:
            sk.append((family, sec))
    payload = {
        "chunk_id": key, "chunk_text": text, "document_id": doc_id,
        "hierarchy_level": hl, "chunk_index": chunk_index or rank,
        "act_name": act_name, "section_number": section,
        "authority": "FSSAI", "instrument_id": doc_id,
        "sections_covered": sections_covered or [],
        "parent_chunk_id": parent_chunk_id,
    }
    return CandidateItem(
        rank=rank, score=score, kind="chunk", key=key, family=family,
        section=section, section_keys=list(sk), text=text,
        document_id=doc_id, hierarchy_level=hl, parent_key=parent_chunk_id,
        chunk_index=chunk_index or rank, authority="FSSAI",
        instrument_id=doc_id, text_tokens=_tokenize(text), payload=payload,
    )


def _kg(pid, score=0.5, rank=21, text="", family="fssai", section="92",
        instrument="Food Safety and Standards Act, 2006"):
    sk = [(family, section)] if family else [(None, section)]
    provision = {
        "provision_id": pid, "provision_number": section or "",
        "title": text, "instrument_title": instrument,
        "legal_domain": "FOOD_SAFETY", "status": "current",
    }
    return CandidateItem(
        rank=rank, score=score, kind="kg", key=pid, family=family,
        section=section, section_keys=list(sk), text=text,
        document_id=instrument, hierarchy_level=0, parent_key=None,
        chunk_index=rank, authority=instrument, instrument_id=pid,
        text_tokens=_tokenize(text), payload=provision,
    )

# --------------------------------------------------------------------------- #
# Tokenizer / Jaccard
# --------------------------------------------------------------------------- #
class TestTokenizer:
    def test_tokenize_basic(self):
        tokens = _tokenize("Section 92 of the FSS Act 2006")
        assert "section" in tokens
        assert "act" in tokens
        assert "2006" in tokens
        assert "the" not in tokens

    def test_tokenize_empty(self):
        assert _tokenize("") == frozenset()
        assert _tokenize(None) == frozenset()

    def test_jaccard_identical(self):
        a = _tokenize("Section 92 requires license")
        b = _tokenize("Section 92 requires license")
        assert _jaccard(a, b) == 1.0

    def test_jaccard_disjoint(self):
        a = _tokenize("License required under Act")
        b = _tokenize("Penalty fine imprisonment")
        assert _jaccard(a, b) == 0.0

    def test_jaccard_partial(self):
        a = _tokenize("section 92 license requirements")
        b = _tokenize("section 92 penalty requirements")
        assert 0.0 < _jaccard(a, b) < 1.0


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def family_map():
    return FamilyMap()


@pytest.fixture
def fake_payload_index():
    idx = {}
    for i in range(1, 21):
        idx[f"chunk_{i}"] = {
            "chunk_text": f"Section {i} of the FSS Act deals with safety requirements.",
            "document_id": f"doc_{i % 3}",
            "hierarchy_level": 1 if i % 3 == 0 else 2,
            "chunk_index": i,
            "act_name": "Food Safety and Standards Act, 2006",
            "section_number": str(i),
            "authority": "FSSAI",
            "instrument_id": f"doc_{i % 3}",
            "sections_covered": [],
            "parent_chunk_id": None,
        }
    return idx


@pytest.fixture
def fake_arm_result():
    return {
        "arm": "F_test",
        "chunk_ids": [f"chunk_{i}" for i in range(1, 21)] + ["chunk_1"],
        "kg_provisions": [
            {
                "provision_id": f"kg_{i}",
                "provision_number": str(100 + i),
                "title": f"Section {100+i} KG provision text about penalties",
                "instrument_title": "Food Safety and Standards Act, 2006",
                "legal_domain": "FOOD_SAFETY",
                "status": "current",
            }
            for i in range(1, 6)
        ],
        "latency_ms": 150,
        "error": None,
        "retriever": "reranker",
    }


# --------------------------------------------------------------------------- #
# build_candidates
# --------------------------------------------------------------------------- #
class TestBuildCandidates:
    def test_chunk_count(self, fake_arm_result, fake_payload_index, family_map):
        candidates = build_candidates(fake_arm_result, fake_payload_index, family_map)
        assert len(candidates) == 25  # 20 unique chunks + 5 KG

    def test_kinds(self, fake_arm_result, fake_payload_index, family_map):
        candidates = build_candidates(fake_arm_result, fake_payload_index, family_map)
        chunks = [c for c in candidates if c.kind == "chunk"]
        kgs = [c for c in candidates if c.kind == "kg"]
        assert len(chunks) == 20
        assert len(kgs) == 5

    def test_scores_descending(self, fake_arm_result, fake_payload_index, family_map):
        candidates = build_candidates(fake_arm_result, fake_payload_index, family_map)
        scores = [c.score for c in candidates]
        assert scores == sorted(scores, reverse=True)
        assert scores[0] == pytest.approx(1.0)
        assert scores[-1] > 0.0

    def test_kg_scored_lower(self, fake_arm_result, fake_payload_index, family_map):
        candidates = build_candidates(fake_arm_result, fake_payload_index, family_map)
        assert candidates[20].score < candidates[19].score

    def test_dedup(self, fake_payload_index, family_map):
        arm = {
            "arm": "F_test", "chunk_ids": ["chunk_1", "chunk_1", "chunk_2"],
            "kg_provisions": [], "latency_ms": 0, "error": None, "retriever": "r",
        }
        candidates = build_candidates(arm, fake_payload_index, family_map)
        assert len(candidates) == 2

    def test_missing_payload_skipped(self, fake_payload_index, family_map):
        arm = {
            "arm": "F_test", "chunk_ids": ["nonexistent", "chunk_1"],
            "kg_provisions": [], "latency_ms": 0, "error": None, "retriever": "r",
        }
        candidates = build_candidates(arm, fake_payload_index, family_map)
        assert len(candidates) == 1
        assert candidates[0].key == "chunk_1"


# --------------------------------------------------------------------------- #
# Strategy A — TopKSelector
# --------------------------------------------------------------------------- #
class TestTopK:
    def test_returns_top_k(self):
        cands = [_chunk(f"c{i}", score=1.0 - i * 0.05, rank=i + 1) for i in range(10)]
        sel = TopKSelector().select(cands, 5)
        assert len(sel) == 5
        assert [s.rank for s in sel] == [1, 2, 3, 4, 5]

    def test_fewer_than_k(self):
        sel = TopKSelector().select([_chunk("c1")], 5)
        assert len(sel) == 1


# --------------------------------------------------------------------------- #
# Strategy B — MMRSelector
# --------------------------------------------------------------------------- #
class TestMMR:
    def test_diversity_over_relevance(self):
        """Chunk 2 has higher score than chunk 3, but chunk 2 is textually
        identical to chunk 1 — MMR should prefer chunk 3."""
        ta = "Section 31 adjudication of penalties food safety requirements"
        tb = "Section 31 adjudication of penalties food safety requirements"
        tc = "Section 92 grant of licence for food businesses"
        cands = [
            _chunk("c1", score=0.95, rank=1, text=ta, section="31"),
            _chunk("c2", score=0.80, rank=2, text=tb, section="31"),
            _chunk("c3", score=0.60, rank=3, text=tc, section="92"),
        ]
        sel = MMRSelector(lambda_param=0.7).select(cands, 2)
        assert len(sel) == 2
        assert sel[0].key == "c1"
        assert sel[1].key == "c3"

    def test_lambda_one_reduces_to_topk(self):
        cands = [_chunk(f"c{i}", score=1.0 - i * 0.1, rank=i + 1,
                         text=f"unique text number {i} section") for i in range(5)]
        mmr = MMRSelector(lambda_param=1.0).select(cands, 3)
        topk = TopKSelector().select(cands, 3)
        assert [c.key for c in mmr] == [c.key for c in topk]

    def test_fewer_than_k(self):
        cands = [_chunk("c1", rank=1, text="alpha"), _chunk("c2", rank=2, text="beta")]
        sel = MMRSelector().select(cands, 5)
        assert len(sel) == 2

    def test_returns_k_items(self):
        cands = [_chunk(f"c{i}", score=0.9 - i * 0.05, rank=i + 1,
                         text=f"text {i} section {i}") for i in range(10)]
        sel = MMRSelector().select(cands, 5)
        assert len(sel) == 5

        assert len(sel) == 5


# --------------------------------------------------------------------------- #
# Strategy C — LegalStructureDiversitySelector
# --------------------------------------------------------------------------- #
class TestLegalStructureDiversity:
    def test_section_spread(self):
        """4 chunks in 2 sections — selector picks one per section."""
        cands = [
            _chunk("c1", score=0.95, rank=1, section="92", text="section 92 food"),
            _chunk("c2", score=0.80, rank=2, section="92", text="section 92 safety"),
            _chunk("c3", score=0.70, rank=3, section="55", text="section 55 license"),
            _chunk("c4", score=0.60, rank=4, section="55", text="section 55 business"),
        ]
        sel = LegalStructureDiversitySelector().select(cands, 2)
        assert len(sel) == 2
        sections = {s.section for s in sel}
        assert len(sections) == 2
        assert sel[0].key == "c1"
        assert sel[1].key == "c3"

    def test_fills_remaining_by_score(self):
        """Extra slots filled by score after one-per-section."""
        cands = [
            _chunk("c1", score=0.95, rank=1, section="92"),
            _chunk("c2", score=0.80, rank=2, section="55"),
            _chunk("c3", score=0.70, rank=3, section="55"),
            _chunk("c4", score=0.60, rank=4, section="55"),
        ]
        sel = LegalStructureDiversitySelector().select(cands, 3)
        assert len(sel) == 3
        assert sel[2].key == "c3"


# --------------------------------------------------------------------------- #
# Strategy D — HierarchyAwareSelector
# --------------------------------------------------------------------------- #
class TestHierarchyAware:
    def test_preserves_section_subsection_chain(self):
        """Chunks forming section->subsection chain (HL 1->2->3):
        selecting deepest child pulls in parent."""
        cands = [
            _chunk("parent", score=0.30, rank=1, section="31", hl=1,
                   text="Section 31 adjudication of penalties", doc_id="doc1",
                   chunk_index=1),
            _chunk("child", score=0.90, rank=2, section="31", hl=2,
                   text="Sub-section 31(2) penalty computation", doc_id="doc1",
                   chunk_index=2),
            _chunk("child2", score=0.85, rank=3, section="31", hl=3,
                   text="Clause 31(2)(a) fine amount", doc_id="doc1",
                   chunk_index=3),
        ]
        sel = HierarchyAwareSelector().select(cands, 2)
        assert len(sel) == 2
        keys = {s.key for s in sel}
        assert "child2" in keys
        assert "child" in keys  # parent pulled in

    def test_no_parent_when_not_in_pool(self):
        """If parent is not in the candidate pool, child is still selected."""
        cands = [
            _chunk("child", score=0.90, rank=1, section="31", hl=3,
                   text="Clause 31(2)(a) fine amount", doc_id="doc1",
                   chunk_index=3),
        ]
        sel = HierarchyAwareSelector().select(cands, 2)
        assert len(sel) == 1
        assert sel[0].key == "child"

    def test_returns_k(self):
        cands = [_chunk(f"c{i}", score=0.9 - i * 0.05, rank=i + 1) for i in range(10)]
        sel = HierarchyAwareSelector().select(cands, 5)
        assert len(sel) == 5



# --------------------------------------------------------------------------- #
# Strategy E — HybridEvidenceSetSelector
# --------------------------------------------------------------------------- #
class TestHybrid:
    def test_kg_complementarity(self):
        """Chunks cover sections 92 and 55; KG offers section 82 -> selected."""
        cands = [
            _chunk("c1", score=0.95, rank=1, section="92", text="section 92 food"),
            _chunk("c2", score=0.90, rank=2, section="92", text="section 92 safety"),
            _chunk("c3", score=0.85, rank=3, section="55", text="section 55 license"),
            _chunk("c4", score=0.80, rank=4, section="55", text="section 55 business"),
            _chunk("c5", score=0.70, rank=5, section="31", text="section 31 penalties"),
            _kg("kg1", score=0.50, rank=6, text="Section 82 finances authority", section="82"),
            _kg("kg2", score=0.45, rank=7, text="Section 31 adjudication penalty", section="31"),
        ]
        sel = HybridEvidenceSetSelector().select(cands, 5)
        assert len(sel) == 5
        kg_selected = [s for s in sel if s.kind == "kg"]
        # KG covering section 82 (not covered by chunks) should be selected
        assert any(s.section == "82" for s in kg_selected)

    def test_returns_k(self):
        cands = [_chunk(f"c{i}", score=0.9 - i * 0.05, rank=i + 1) for i in range(10)]
        sel = HybridEvidenceSetSelector().select(cands, 5)
        assert len(sel) == 5

    def test_chunk_then_kg_ordering(self):
        """Chunks should be preferred over KG (higher upstream scores)."""
        cands = [
            _chunk(f"c{i}", score=0.5 - i * 0.02, rank=i + 1, section=f"{i}")
            for i in range(10)
        ] + [_kg("kg1", score=0.10, rank=11, section="99")]
        sel = HybridEvidenceSetSelector().select(cands, 8)
        assert len(sel) == 8
        assert all(s.kind == "chunk" for s in sel)



# --------------------------------------------------------------------------- #
# candidates_to_arm_result
# --------------------------------------------------------------------------- #
class TestCandidatesToArmResult:
    def test_round_trip(self):
        cands = [_chunk("c1", section="92"), _chunk("c2", section="55")] + [
            _kg("kg1", section="31"), _kg("kg2", section="82")
        ]
        result = candidates_to_arm_result(cands, "V8_E_hybrid")
        assert result["arm"] == "V8_E_hybrid"
        assert result["chunk_ids"] == ["c1", "c2"]
        assert result["kg_provisions"] == [c.payload for c in cands if c.kind == "kg"]
        assert result["retriever"] == "evidence_set_selector"
        assert result["error"] is None

    def test_preserves_order(self):
        cands = [
            _chunk("c3", score=0.6, rank=3),
            _chunk("c1", score=0.9, rank=1),
            _chunk("c2", score=0.7, rank=2),
        ]
        result = candidates_to_arm_result(cands, "V8_A_topk")
        assert result["chunk_ids"] == ["c3", "c1", "c2"]


# --------------------------------------------------------------------------- #
# compute_redundancy
# --------------------------------------------------------------------------- #
class TestComputeRedundancy:
    def test_all_same_section(self):
        """4 items all in section 92 -> dup_rate ~1.0, HHI=1.0."""
        cands = [_chunk(f"c{i}", section="92", doc_id="doc1") for i in range(4)]
        r = compute_redundancy(cands)
        assert r["duplicate_provision_rate"] > 0.99
        assert r["same_section_concentration"] == pytest.approx(1.0)
        assert r["same_document_concentration"] == pytest.approx(1.0)

    def test_all_different_sections(self):
        """4 items, 4 different sections -> dup_rate=0.0, HHI=0.25."""
        cands = [_chunk(f"c{i}", section=str(i + 1)) for i in range(4)]
        r = compute_redundancy(cands)
        assert r["duplicate_provision_rate"] < 0.01
        assert r["same_section_concentration"] == pytest.approx(0.25, abs=0.01)

    def test_mixed_sections(self):
        """4 items: 2 in sec 92, 2 in sec 55 -> dup_rate=0.5, HHI=0.5."""
        cands = [
            _chunk("c1", section="92", doc_id="doc1"),
            _chunk("c2", section="92", doc_id="doc1"),
            _chunk("c3", section="55", doc_id="doc2"),
            _chunk("c4", section="55", doc_id="doc2"),
        ]
        r = compute_redundancy(cands)
        assert r["duplicate_provision_rate"] == pytest.approx(0.5, abs=0.01)
        assert r["same_section_concentration"] == pytest.approx(0.5, abs=0.01)
        assert r["same_document_concentration"] == pytest.approx(0.5, abs=0.01)

    def test_empty(self):
        r = compute_redundancy([])
        assert r["duplicate_provision_rate"] == 0.0
        assert r["same_section_concentration"] == 0.0
        assert r["same_document_concentration"] == 0.0


# --------------------------------------------------------------------------- #
# Strategy registry
# --------------------------------------------------------------------------- #
class TestRegistry:
    def test_all_strategies_present(self):
        for name in ["V8_A_topk", "V8_B_mmr", "V8_C_legal_diversity",
                     "V8_D_hierarchy", "V8_E_hybrid"]:
            assert name in STRATEGIES

    def test_each_has_description(self):
        for name in STRATEGY_NAMES:
            selector, desc = STRATEGIES[name]
            assert desc
            assert hasattr(selector, "select")


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
class TestDeterminism:
    def test_mmr_deterministic(self):
        cands = [_chunk(f"c{i}", score=0.9 - i * 0.05, rank=i + 1,
                         text=f"text {i} section {i}") for i in range(10)]
        r1 = MMRSelector().select(cands, 5)
        r2 = MMRSelector().select(cands, 5)
        assert [c.key for c in r1] == [c.key for c in r2]

    def test_hybrid_deterministic(self):
        cands = [_chunk(f"c{i}", score=0.9 - i * 0.05, rank=i + 1,
                         section=str(i)) for i in range(10)]
        cands += [_kg("kg1", score=0.20, section="99")]
        r1 = HybridEvidenceSetSelector().select(cands, 5)
        r2 = HybridEvidenceSetSelector().select(cands, 5)
        assert [c.key for c in r1] == [c.key for c in r2]



# --------------------------------------------------------------------------- #
# Smoke test — integration with score_question
# --------------------------------------------------------------------------- #
class TestScoreQuestionIntegration:
    """Full pipeline: build_candidates -> select -> score_question."""

    @pytest.fixture
    def fm(self):
        return FamilyMap()

    def _make_payload_index(self):
        return {
            "c92": {
                "chunk_text": "Section 92 of the FSS Act 2006 grants powers to the Authority.",
                "document_id": "fss_act", "hierarchy_level": 1, "chunk_index": 10,
                "act_name": "Food Safety and Standards Act, 2006",
                "section_number": "92", "authority": "FSSAI",
                "instrument_id": "fss_act", "sections_covered": ["92"],
                "parent_chunk_id": None,
            },
            "c55": {
                "chunk_text": "Section 55 requires food businesses to obtain a license.",
                "document_id": "fss_act", "hierarchy_level": 1, "chunk_index": 5,
                "act_name": "Food Safety and Standards Act, 2006",
                "section_number": "55", "authority": "FSSAI",
                "instrument_id": "fss_act", "sections_covered": ["55"],
                "parent_chunk_id": None,
            },
            "c31": {
                "chunk_text": "Section 31 lists the penalties for contravention.",
                "document_id": "fss_act", "hierarchy_level": 1, "chunk_index": 3,
                "act_name": "Food Safety and Standards Act, 2006",
                "section_number": "31", "authority": "FSSAI",
                "instrument_id": "fss_act", "sections_covered": ["31"],
                "parent_chunk_id": None,
            },
        }

    def _make_question(self):
        return BenchmarkQuestion(
            raw={"question_id": "TEST_001", "question": "What does Section 92 say?"},
            gold_units=[
                GoldUnit(
                    provision_id="fssai:s92", family="fssai", section="92",
                    act="Food Safety and Standards Act, 2006",
                    collection="fssai_legal_768", document_id=None,
                    gain=2.0, role="primary",
                ),
                GoldUnit(
                    provision_id="fssai:s55", family="fssai", section="55",
                    act="Food Safety and Standards Act, 2006",
                    collection="fssai_legal_768", document_id=None,
                    gain=1.0, role="acceptable",
                ),
            ],
        )

    def _make_arm_result(self):
        return {
            "arm": "F_test",
            "chunk_ids": ["c92", "c55", "c31", "c92", "c55"],
            "kg_provisions": [
                {
                    "provision_id": "FSS_ACT_SEC_82",
                    "provision_number": "82",
                    "title": "Finances of the Authority",
                    "instrument_title": "Food Safety and Standards Act, 2006",
                    "legal_domain": "FOOD_SAFETY", "status": "current",
                },
            ],
            "latency_ms": 100, "error": None, "retriever": "reranker",
        }

    def test_topk_recall(self, fm):
        q = self._make_question()
        pi = self._make_payload_index()
        arm = self._make_arm_result()

        candidates = build_candidates(arm, pi, fm)
        selected = TopKSelector().select(candidates, 5)
        result = candidates_to_arm_result(selected, "V8_A_topk")
        metrics = score_question(q, result, pi, fm)

        # c92 at rank 1 covers primary; c55 at rank 2 covers acceptable
        assert metrics.recall.get(1, 0.0) == 0.5
        assert metrics.recall.get(5, 0.0) == 1.0
        assert 0.0 <= metrics.mrr <= 1.0
        assert 0.0 <= metrics.ndcg.get(10, 0.0) <= 1.0
        assert metrics.error is None

    def test_topk_matches_baseline(self, fm):
        q = self._make_question()
        pi = self._make_payload_index()
        arm = self._make_arm_result()

        candidates = build_candidates(arm, pi, fm)
        selected = TopKSelector().select(candidates, 3)
        result = candidates_to_arm_result(selected, "V8_A_topk")
        metrics = score_question(q, result, pi, fm)

        baseline = {
            "arm": "F_baseline_k3", "chunk_ids": arm["chunk_ids"][:3],
            "kg_provisions": [], "latency_ms": 0, "error": None, "retriever": "reranker",
        }
        bm = score_question(q, baseline, pi, fm)
        assert metrics.recall.get(10, 0.0) == pytest.approx(bm.recall.get(10, 0.0))

    def test_hybrid_includes_kg(self, fm):
        q = self._make_question()
        pi = self._make_payload_index()
        arm = self._make_arm_result()

        candidates = build_candidates(arm, pi, fm)
        # 3 unique chunks + 1 KG = 4 candidates; k=4 -> all selected
        selected = HybridEvidenceSetSelector().select(candidates, 4)
        assert len(selected) == 4
        assert any(c.kind == "kg" for c in selected)

    def test_all_strategies_valid_metrics(self, fm):
        q = self._make_question()
        pi = self._make_payload_index()
        arm = self._make_arm_result()

        candidates = build_candidates(arm, pi, fm)
        for name in STRATEGY_NAMES:
            selector, _ = STRATEGIES[name]
            selected = selector.select(candidates, 3)
            result = candidates_to_arm_result(selected, name)
            metrics = score_question(q, result, pi, fm)
            assert metrics.error is None
            assert 0.0 <= metrics.recall.get(10, 0.0) <= 1.0
            assert 0.0 <= metrics.mrr <= 1.0


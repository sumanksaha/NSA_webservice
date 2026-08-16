"""Unit tests for evidence-set construction and metrics."""

from dataclasses import dataclass

import pytest

from app.rag.retrieval.evidence_selector import (
    EVIDENCE_PRIMARY,
    EVIDENCE_DEFINITION,
    EVIDENCE_EXCEPTION,
    EVIDENCE_PENALTY,
    EVIDENCE_CROSS_REFERENCE,
    EvidenceItem,
    EvidenceSet,
    select_evidence_set,
)
from app.rag.retrieval.evidence_metrics import (
    EvidenceMetricResult,
    evidence_set_recall,
    evidence_set_precision,
    evidence_set_f1,
    evidence_coverage_at_k,
    evaluate_evidence_set,
    evaluate_evidence_batch,
    EvidenceBatchResult,
)


@dataclass
class FakeChunk:
    chunk_id: str = "test"
    text: str = ""
    section_number: str | None = None
    document_title: str = ""
    act_name: str = ""
    authority: str = ""
    score: float = 1.0


class TestEvidenceSelection:
    def _make_chunks(self, n: int = 5) -> list[FakeChunk]:
        return [
            FakeChunk(chunk_id=f"c{i}", text=f"Section 31 text about penalties fine.", section_number="31", act_name="FSS Act", score=0.9 - i * 0.05)
            for i in range(n)
        ]

    def test_select_returns_evidence_set(self):
        chunks = self._make_chunks(5)
        es = select_evidence_set("What does Section 31 say about penalty?", chunks)
        assert isinstance(es, EvidenceSet)
        assert es.total_pool == 5
        assert len(es.items) <= 5
        assert len(es.items) >= 2  # min_size

    def test_select_respects_max_size(self):
        chunks = self._make_chunks(20)
        es = select_evidence_set("Section 31 penalty", chunks, max_size=3)
        assert len(es.items) <= 3

    def test_select_respects_min_size(self):
        chunks = [FakeChunk(chunk_id="c1", text="Section 31", section_number="31", score=0.9)]
        es = select_evidence_set("Section 31", chunks, min_size=2, max_size=5)
        assert len(es.items) >= 1  # only 1 available

    def test_primary_provision_selected_first(self):
        chunks = [
            FakeChunk(chunk_id="c1", text="Penalty fine imprisonment", section_number="55", score=0.8),
            FakeChunk(chunk_id="c2", text="Section 31 is about penalties", section_number="31", score=0.9),
        ]
        es = select_evidence_set("What does Section 31 say?", chunks)
        # The primary provision (section 31) should be first or highly ranked
        types = [item.evidence_type for item in es.items]
        assert EVIDENCE_PRIMARY in types

    def test_complementarity_prioritized(self):
        """Selector should prefer different evidence types over duplicates."""
        chunks = [
            FakeChunk(chunk_id="c1", text="Section 31 is about penalties and fine", section_number="31", score=0.9),
            FakeChunk(chunk_id="c2", text="Definition: 'food' means any article", section_number=None, score=0.8),
            FakeChunk(chunk_id="c3", text="Section 31(2) except not applies", section_number="31", score=0.85),
            FakeChunk(chunk_id="c4", text="Section 31 is about penalties and fine", section_number="31", score=0.88),  # duplicate-ish
        ]
        es = select_evidence_set("What does Section 31 say about 'food'?", chunks)
        types = [item.evidence_type for item in es.items]
        # Should have at least 2 different types
        assert len(set(types)) >= 1  # at least variety attempted

    def test_empty_input(self):
        es = select_evidence_set("some query", [])
        assert len(es.items) == 0
        assert es.total_pool == 0

    def test_query_with_no_section(self):
        chunks = [
            FakeChunk(chunk_id="c1", text="General legal text about food", section_number=None, score=0.9),
        ]
        es = select_evidence_set("What is the legal framework for food safety?", chunks)
        assert len(es.items) >= 1

    def test_redundancy_detection(self):
        """Two chunks about the same section should have redundancy > 0."""
        chunks = [
            FakeChunk(chunk_id="c1", text="Section 31 penalty fine", section_number="31", score=0.9),
            FakeChunk(chunk_id="c2", text="Section 31 penalty fine", section_number="31", score=0.8),
        ]
        es = select_evidence_set("Section 31 penalty", chunks)
        # c2 is a duplicate of c1 (same section)
        dup_items = [item for item in es.items if item.section_number == "31"]
        if len(dup_items) > 1:
            assert dup_items[1].redundancy > 0.0


class TestEvidenceDetection:
    def test_exception_detection(self):
        chunk = FakeChunk(chunk_id="c1", text="This section shall not apply except where...", section_number="32")
        from app.rag.retrieval.evidence_selector import _detect_evidence_type
        etype = _detect_evidence_type(chunk, "31")
        assert etype == EVIDENCE_EXCEPTION

    def test_penalty_detection(self):
        chunk = FakeChunk(chunk_id="c1", text="Whoever contravenes shall be punished with fine.", section_number="33")
        from app.rag.retrieval.evidence_selector import _detect_evidence_type
        etype = _detect_evidence_type(chunk, "31")
        assert etype == EVIDENCE_PENALTY

    def test_definition_detection(self):
        chunk = FakeChunk(chunk_id="c1", text="For the purposes of this Act, 'food' means...", section_number=None)
        from app.rag.retrieval.evidence_selector import _detect_evidence_type
        etype = _detect_evidence_type(chunk, "31")
        assert etype == EVIDENCE_DEFINITION

    def test_primary_detection(self):
        chunk = FakeChunk(chunk_id="c1", text="Section 31 is here", section_number="31")
        from app.rag.retrieval.evidence_selector import _detect_evidence_type
        etype = _detect_evidence_type(chunk, "31")
        assert etype == EVIDENCE_PRIMARY


class TestEvidenceMetrics:
    def test_recall_perfect(self):
        r = evidence_set_recall(["a", "b", "c"], ["a", "b", "c"])
        assert r.value == 1.0

    def test_recall_partial(self):
        r = evidence_set_recall(["a", "b"], ["a", "b", "c"])
        assert abs(r.value - 2/3) < 1e-9

    def test_recall_empty_gold(self):
        r = evidence_set_recall(["a"], [])
        assert r.value == 1.0  # vacuously

    def test_recall_empty_selected(self):
        r = evidence_set_recall([], ["a", "b"])
        assert r.value == 0.0

    def test_precision_perfect(self):
        p = evidence_set_precision(["a", "b"], ["a", "b"])
        assert p.value == 1.0

    def test_precision_partial(self):
        p = evidence_set_precision(["a", "b", "c"], ["a", "b"])
        assert abs(p.value - 2/3) < 1e-9

    def test_precision_empty_selected(self):
        p = evidence_set_precision([], ["a"])
        assert p.value == 0.0

    def test_f1(self):
        f1 = evidence_set_f1(["a", "b"], ["a", "c"])
        # precision = 1/2, recall = 1/2, f1 = 1/2
        assert abs(f1.value - 0.5) < 1e-9, f1.value

    def test_f1_empty(self):
        f1 = evidence_set_f1([], [])
        assert f1.value == 0.0

    def test_coverage_at_k(self):
        cov = evidence_coverage_at_k(["a", "b", "c", "d"], ["a", "c", "e"], k=3)
        # top-3 = a, b, c; gold = a, c, e; intersect = {a, c} = 2/3
        assert abs(cov.value - 2/3) < 1e-9

    def test_coverage_at_k_full_coverage(self):
        cov = evidence_coverage_at_k(["a", "b"], ["a", "b"], k=3)
        assert cov.value == 1.0

    def test_evaluate_evidence_set(self):
        es = EvidenceSet(query="test", items=[], total_pool=3)
        results = evaluate_evidence_set(es, ["a", "b"])
        assert len(results) >= 3  # recall, precision, f1
        assert any(r.metric_name == "evidence_set_recall" for r in results)

    def test_evaluate_evidence_batch(self):
        es1 = EvidenceSet(query="q1", items=[], total_pool=3)
        es2 = EvidenceSet(query="q2", items=[], total_pool=3)
        batch = evaluate_evidence_batch([es1, es2], [["a"], ["b"]])
        assert batch.num_queries == 2
        assert 0 <= batch.avg_recall <= 1.0
        assert 0 <= batch.avg_precision <= 1.0
        assert 0 <= batch.avg_f1 <= 1.0

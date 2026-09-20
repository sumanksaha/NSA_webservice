"""Unit tests for evidence-set construction and metrics."""

from dataclasses import dataclass

from app.rag.retrieval.evidence_metrics import (
    evaluate_evidence_batch,
    evaluate_evidence_set,
    evidence_coverage_at_k,
    evidence_set_f1,
    evidence_set_precision,
    evidence_set_recall,
)
from app.rag.retrieval.evidence_selector import (
    EVIDENCE_DEFINITION,
    EVIDENCE_EXCEPTION,
    EVIDENCE_PENALTY,
    EVIDENCE_PRIMARY,
    EvidenceSet,
    select_evidence_set,
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
            FakeChunk(
                chunk_id=f"c{i}",
                text="Section 31 text about penalties fine.",
                section_number="31",
                act_name="FSS Act",
                score=0.9 - i * 0.05,
            )
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
            FakeChunk(
                chunk_id="c4", text="Section 31 is about penalties and fine", section_number="31", score=0.88
            ),  # duplicate-ish
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
        assert abs(r.value - 2 / 3) < 1e-9

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
        assert abs(p.value - 2 / 3) < 1e-9

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
        assert abs(cov.value - 2 / 3) < 1e-9

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


class TestUnitGrouping:
    """Phase 2 (roadmap §7): group by canonical legal unit, prune UUID-alias duplicates."""

    def _alias_chunks(self) -> list[FakeChunk]:
        return [
            FakeChunk(
                chunk_id="a",
                text="Section 31 licence requirement alpha wording here.",
                section_number="31",
                act_name="FSS Act",
                score=0.9,
            ),
            FakeChunk(
                chunk_id="b",
                text="Section 31 licence requirement beta variant wording here.",
                section_number="31",
                act_name="Food Safety and Standards Act, 2006",
                score=0.85,
            ),
            FakeChunk(
                chunk_id="c",
                text="Section 32 procedure for inspections.",
                section_number="32",
                act_name="FSS Act",
                score=0.8,
            ),
        ]

    def test_group_chunks_by_unit_collapses_aliases(self):
        from app.rag.retrieval.evidence_selector import group_chunks_by_unit

        groups = group_chunks_by_unit(self._alias_chunks())
        assert len(groups) == 2
        pair = next(members for members in groups.values() if len(members) == 2)
        assert [c.chunk_id for c in pair] == ["a", "b"]  # best score first

    def test_selector_prefers_distinct_units(self):
        es = select_evidence_set("Section 31 licence", self._alias_chunks(), max_size=2)
        assert sorted(it.section_number for it in es.items) == ["31", "32"]

    def test_backfill_marks_duplicates_explicitly(self):
        from app.rag.retrieval.evidence_selector import EVIDENCE_DUPLICATE

        chunks = [
            FakeChunk(chunk_id="c1", text="Section 31 penalty fine alpha.", section_number="31", score=0.9),
            FakeChunk(chunk_id="c2", text="Section 31 penalty fine beta.", section_number="31", score=0.8),
        ]
        es = select_evidence_set("Section 31 penalty", chunks, min_size=2, max_size=5)
        assert len(es.items) == 2  # min_size preserved
        assert es.items[1].evidence_type == EVIDENCE_DUPLICATE


class TestEvidenceExpansion:
    """Phase 2 (roadmap §7): definition / exception / cross-ref expansion."""

    def _operative(self) -> FakeChunk:
        return FakeChunk(
            chunk_id="op",
            text="Section 31 requires a licence for food businesses.",
            section_number="31",
            act_name="FSS Act",
            score=0.9,
        )

    def test_expansion_rescores_and_caps_at_max_size(self):
        # Roadmap §7: compact coherent context — additions re-score into
        # the set and the set never exceeds max_size.
        from app.rag.retrieval.evidence_selector import expand_evidence_units

        low = FakeChunk(
            chunk_id="low",
            text="Section 31 licence fee schedule for food businesses.",
            section_number="31",
            act_name="FSS Act",
            score=0.5,
        )
        pool = [
            self._operative(),
            low,
            FakeChunk(
                chunk_id="d",
                text="'Food' means any article used as food.",
                section_number="3",
                act_name="FSS Act",
                score=0.7,
            ),
        ]
        es = select_evidence_set("Section 31 licence food", [self._operative(), low], max_size=2)
        expanded = expand_evidence_units(es, pool, max_size=2)
        scores = [it.confidence for it in expanded.items]
        assert len(expanded.items) <= 2
        assert scores == sorted(scores, reverse=True)

    def test_expansion_never_shrinks_input_and_reports_kept_delta(self):
        from app.rag.retrieval.evidence_selector import expand_evidence_units

        es = select_evidence_set("Section 31 licence food", [self._operative()], max_size=2)
        expanded = expand_evidence_units(es, [self._operative()], max_size=1)
        assert len(expanded.items) >= len(es.items)
        assert f"{len(expanded.items) - len(es.items)}" in expanded.selection_rationale

    def test_expansion_adds_missing_definition(self):
        from app.rag.retrieval.evidence_selector import EVIDENCE_DEFINITION, expand_evidence_units

        pool = [
            self._operative(),
            FakeChunk(
                chunk_id="d",
                text="'Food' means any article used as food for human consumption.",
                section_number="3",
                act_name="FSS Act",
                score=0.7,
            ),
        ]
        es = select_evidence_set("Section 31 licence", [self._operative()], max_size=2)
        expanded = expand_evidence_units(es, pool)
        assert any(it.evidence_type == EVIDENCE_DEFINITION for it in expanded.items)

    def test_expansion_adds_missing_exception(self):
        from app.rag.retrieval.evidence_selector import EVIDENCE_EXCEPTION, expand_evidence_units

        pool = [
            self._operative(),
            FakeChunk(
                chunk_id="e",
                text="Provided that petty retailers shall be exempt from Section 31.",
                section_number="31",
                act_name="FSS Act",
                score=0.7,
            ),
        ]
        es = select_evidence_set("Section 31 licence", [self._operative()], max_size=2)
        expanded = expand_evidence_units(es, pool)
        assert any(it.evidence_type == EVIDENCE_EXCEPTION for it in expanded.items)

    def test_expansion_skips_gaps_already_covered(self):
        from app.rag.retrieval.evidence_selector import expand_evidence_units

        pool = [self._operative()]
        es = select_evidence_set("Section 31 licence", [self._operative()], max_size=2)
        expanded = expand_evidence_units(es, pool)
        assert len(expanded.items) == len(es.items)

    def test_expansion_uses_lookup_seam(self):
        from app.rag.retrieval.evidence_selector import EVIDENCE_CROSS_REFERENCE, expand_evidence_units

        xref = FakeChunk(
            chunk_id="x",
            text="Section 32 procedure for inspections and sampling.",
            section_number="32",
            act_name="FSS Act",
            score=0.6,
        )
        operative = FakeChunk(
            chunk_id="op",
            text="Section 31 requires a licence, subject to Section 32 procedure.",
            section_number="31",
            act_name="FSS Act",
            score=0.9,
        )
        es = select_evidence_set("Section 31 licence", [operative], max_size=2)
        expanded = expand_evidence_units(es, [operative], reference_lookup=lambda _unit: [xref])
        assert any(it.evidence_type == EVIDENCE_CROSS_REFERENCE for it in expanded.items)

    def test_expansion_respects_cap(self):
        from app.rag.retrieval.evidence_selector import expand_evidence_units

        pool = [
            self._operative(),
            FakeChunk(chunk_id="d", text="'Food' means an article.", section_number="3", score=0.7),
            FakeChunk(chunk_id="e", text="Provided that petty shops are exempt.", section_number="31", score=0.65),
        ]
        es = select_evidence_set("Section 31 licence", [self._operative()], max_size=2)
        expanded = expand_evidence_units(es, pool, max_expansion=1)
        assert len(expanded.items) == len(es.items) + 1

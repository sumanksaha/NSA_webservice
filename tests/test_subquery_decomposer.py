"""P1 item 7 — compound-query decomposition and coverage-preserving merge."""

from __future__ import annotations

from app.rag.retrieval.result import RetrievedChunk
from app.rag.retrieval.subquery_decomposer import SubQueryDecomposer


def _chunk(cid: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=cid, score=score, text="")


class TestSubQueryDecomposer:
    def test_keeps_single_fact_query(self):
        q = "Is a licence needed to sell packaged water?"
        assert SubQueryDecomposer().decompose(q) == [q]

    def test_does_not_split_a_noun_phrase(self):
        q = "What is the Food Safety and Standards Act, 2006?"
        assert SubQueryDecomposer().decompose(q) == [q]

    def test_section_pair_keeps_legacy_output(self):
        q = "What do Section 33 and Section 38 require?"
        assert SubQueryDecomposer().decompose(q) == [
            "Section 33 provisions",
            "Section 38 provisions",
        ]

    def test_penalty_and_procedure_split(self):
        q = "What is the penalty for selling unsafe food and the procedure for recalling it?"
        parts = SubQueryDecomposer().decompose(q)
        assert len(parts) == 2
        assert "penalty" in parts[0]
        assert "procedure" in parts[1]
        assert parts[1].lower().startswith("what")

    def test_penalty_and_authority_split(self):
        q = "What is the penalty under Section 55 and who may impose it?"
        parts = SubQueryDecomposer().decompose(q)
        assert len(parts) == 2
        assert "Section 55" in parts[0]
        assert "who" in parts[1].lower()

    def test_semicolon_list_splits(self):
        q = "What is the licence fee; what is the renewal deadline?"
        parts = SubQueryDecomposer().decompose(q)
        assert len(parts) == 2

    def test_quoted_conjunction_is_not_split(self):
        q = 'What does "packaged and labelled" mean under the Act?'
        assert SubQueryDecomposer().decompose(q) == [q]

    def test_caps_fan_out(self):
        q = "What is the penalty and what is the procedure and what is the appeal and what is the deadline and who decides?"
        assert len(SubQueryDecomposer().decompose(q)) <= 4


class TestCoveragePreservingMerge:
    def test_each_subquery_keeps_a_slot(self):
        from app.rag.tasks import _merge_subquery_chunks

        # Sub-query A has the two highest-scoring chunks; B's evidence would
        # be dropped by a plain score-sort truncation to 2.
        groups = [
            [_chunk("a1", 0.99), _chunk("a2", 0.98)],
            [_chunk("b1", 0.10), _chunk("b2", 0.09)],
        ]
        merged = _merge_subquery_chunks(groups, top_k=2)
        ids = [c.chunk_id for c in merged]
        assert "a1" in ids
        assert "b1" in ids

    def test_dedupes_across_subqueries(self):
        from app.rag.tasks import _merge_subquery_chunks

        groups = [[_chunk("shared", 0.9)], [_chunk("shared", 0.9), _chunk("b1", 0.5)]]
        merged = _merge_subquery_chunks(groups, top_k=5)
        assert [c.chunk_id for c in merged].count("shared") == 1

    def test_single_group_is_plain_score_sort(self):
        from app.rag.tasks import _merge_subquery_chunks

        merged = _merge_subquery_chunks([[_chunk("a", 0.1), _chunk("b", 0.9)]], top_k=1)
        assert [c.chunk_id for c in merged] == ["b"]

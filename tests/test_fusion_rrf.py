"""Tests for the candidate-fusion repair (RRF interleave, 2026-08-12).

Covers:
- ``kg.hybrid.rrf_fuse_chunks`` — production RRF over ranked chunk lists.
- ``evaluation.fusion.rrf_fuse_items`` — offline RRF over RankedItem lists.
- ``evaluation.metrics.build_ranked_items`` — fused_items path (KG evidence
  interleaved by rank instead of tail-appended after every chunk).
"""

from __future__ import annotations

from app.rag.retrieval.result import RetrievedChunk


def _chunk(cid: str, score: float = 1.0) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=cid, score=score, text="", document_title=f"doc-{cid}")


# --------------------------------------------------------------------------- #
# kg.hybrid.rrf_fuse_chunks
# --------------------------------------------------------------------------- #


class TestRrfFuseChunks:
    def test_fuses_and_orders_by_rrf_score(self):
        from kg.hybrid import rrf_fuse_chunks

        dense = [_chunk("a"), _chunk("b"), _chunk("c")]
        sparse = [_chunk("b"), _chunk("d")]
        fused = rrf_fuse_chunks([dense, sparse], rrf_k=60.0, top_k=10)

        # b appears in both lists -> highest RRF score, ranks first.
        assert [c.chunk_id for c in fused[:3]] == ["b", "a", "d"]
        assert fused[0].score > fused[1].score > fused[2].score
        # Scores are the fused RRF scores (b: 1/61 + 1/62).
        assert abs(fused[0].score - (1 / 61 + 1 / 62)) < 1e-9

    def test_dedupes_by_chunk_id(self):
        from kg.hybrid import rrf_fuse_chunks

        fused = rrf_fuse_chunks([[_chunk("x"), _chunk("y")], [_chunk("y"), _chunk("z")]], top_k=10)
        ids = [c.chunk_id for c in fused]
        assert len(ids) == len(set(ids))
        assert ids[0] == "y"  # agreement boost

    def test_top_k_cap(self):
        from kg.hybrid import rrf_fuse_chunks

        lists = [[_chunk(f"c{i}") for i in range(10)], [_chunk(f"d{i}") for i in range(10)]]
        fused = rrf_fuse_chunks(lists, top_k=5)
        assert len(fused) == 5

    def test_empty_lists(self):
        from kg.hybrid import rrf_fuse_chunks

        assert rrf_fuse_chunks([[], []], top_k=5) == []

    def test_dedupe_kg_drops_redundant_kg_chunk(self):
        """A KG-Provision chunk covering (act, section) a vector chunk already
        covers is dropped before scoring — it must not occupy a fused slot."""
        from kg.hybrid import rrf_fuse_chunks

        vec = RetrievedChunk(
            chunk_id="v1",
            score=0.9,
            text="",
            document_title="Food Safety and Standards Act, 2006",
            section_number="16",
        )
        kg = RetrievedChunk(
            chunk_id="KG:prov1",
            score=0.0,
            text="",
            document_title="Food Safety and Standards Act, 2006",
            section_number="16",
            document_type="KG-Provision",
        )
        novel = RetrievedChunk(
            chunk_id="KG:prov2",
            score=0.0,
            text="",
            document_title="Air (Prevention and Control of Pollution) Act, 1981",
            section_number="3",
            document_type="KG-Provision",
        )
        fused = rrf_fuse_chunks([[vec], [kg, novel]], rrf_k=60.0, top_k=10)
        ids = [c.chunk_id for c in fused]
        assert "KG:prov1" not in ids  # redundant — same (act, section) as v1
        assert "KG:prov2" in ids  # novel provision survives
        assert ids[0] == "v1"

    def test_dedupe_kg_off_keeps_redundant_kg_chunk(self):
        """dedupe_kg=False preserves the pre-repair behaviour (KG kept)."""
        from kg.hybrid import rrf_fuse_chunks

        vec = RetrievedChunk(
            chunk_id="v1",
            score=0.9,
            text="",
            document_title="Food Safety and Standards Act, 2006",
            section_number="16",
        )
        kg = RetrievedChunk(
            chunk_id="KG:prov1",
            score=0.0,
            text="",
            document_title="Food Safety and Standards Act, 2006",
            section_number="16",
            document_type="KG-Provision",
        )
        fused = rrf_fuse_chunks([[vec], [kg]], rrf_k=60.0, top_k=10, dedupe_kg=False)
        assert "KG:prov1" in [c.chunk_id for c in fused]

    def test_dedupe_kg_normalises_leading_the(self):
        """Act-title normalisation matches 'The Food Safety…' to 'Food Safety…'."""
        from kg.hybrid import rrf_fuse_chunks

        vec = RetrievedChunk(
            chunk_id="v1",
            score=0.9,
            text="",
            document_title="Food Safety and Standards Act, 2006",
            section_number="16",
        )
        kg = RetrievedChunk(
            chunk_id="KG:prov1",
            score=0.0,
            text="",
            document_title="The Food Safety and Standards Act, 2006",
            section_number="16",
            document_type="KG-Provision",
        )
        fused = rrf_fuse_chunks([[vec], [kg]], rrf_k=60.0, top_k=10)
        assert "KG:prov1" not in [c.chunk_id for c in fused]

    def test_kg_interleaves_by_rrf_rank_not_tail(self):
        """A rank-1 KG chunk ties the rank-1 vector chunk and outranks ranks 2+ —
        it is interleaved by merit, never tail-appended after all chunks."""
        from kg.hybrid import rrf_fuse_chunks

        dense = [_chunk(f"v{i}", 0.9) for i in range(20)]
        kg = [_chunk("KG:provision", 0.0)]
        fused = rrf_fuse_chunks([dense, kg], rrf_k=60.0, top_k=20)
        ids = [c.chunk_id for c in fused]
        # 1/61 (kg@rank1) > 1/63 (chunk@rank3): KG must sit inside the top 3.
        assert ids.index("KG:provision") <= 2
        assert fused[-1].chunk_id != "KG:provision"

    def test_equal_rrf_tie_breaks_by_input_list_order(self):
        """Tie policy: equal RRF scores keep first-appearance order (vector
        lists first), so a KG item never displaces an equally-ranked vector
        item — deterministic across runs."""
        from kg.hybrid import rrf_fuse_chunks

        dense = [_chunk("d1"), _chunk("d2")]  # rank 1, 2
        kg = [_chunk("k1")]  # rank 1
        # d1@1 and k1@1 both score 1/61 -> tie.  Stable sort must keep d1
        # (first in input order) above k1; d2@2 (1/62) trails both.
        fused = rrf_fuse_chunks([dense, kg], rrf_k=60.0, top_k=5)
        assert [c.chunk_id for c in fused] == ["d1", "k1", "d2"]

        # Deterministic: same inputs, same lists, same order every time.
        again = rrf_fuse_chunks([dense, kg], rrf_k=60.0, top_k=5)
        assert [c.chunk_id for c in again] == ["d1", "k1", "d2"]


# --------------------------------------------------------------------------- #
# evaluation.fusion.rrf_fuse_items
# --------------------------------------------------------------------------- #


class TestRrfFuseItems:
    def test_dedupe_kg_items_drops_covered_provision(self):
        from evaluation.fusion import dedupe_kg_items
        from evaluation.metrics import RankedItem

        chunks = [
            RankedItem(kind="chunk", key="c1", family="fssai", section="16"),
            RankedItem(kind="chunk", key="c2", family="env", section="3"),
        ]
        kg = [
            RankedItem(kind="kg", key="p1", family="fssai", section="16"),  # redundant
            RankedItem(kind="kg", key="p2", family="env", section="21"),  # novel
        ]
        kept = dedupe_kg_items(chunks, kg)
        assert [i.key for i in kept] == ["p2"]

    def test_dedupe_kg_items_keeps_all_when_chunks_empty(self):
        from evaluation.fusion import dedupe_kg_items
        from evaluation.metrics import RankedItem

        kg = [RankedItem(kind="kg", key="p1", family="fssai", section="16")]
        assert dedupe_kg_items([], kg) == kg

    def test_rrf_over_item_lists(self):
        from evaluation.fusion import rrf_fuse_items
        from evaluation.metrics import RankedItem

        dense = [
            RankedItem(kind="chunk", key="c1", family="fssai", section="16"),
            RankedItem(kind="chunk", key="c2", family="fssai", section="31"),
        ]
        kg = [RankedItem(kind="kg", key="p1", family="fssai", section="16")]
        fused = rrf_fuse_items(dense, kg, top_k=10)

        # c1 (dense@1) and p1 (kg@1) tie on RRF score; both outrank c2.  The
        # repair property: p1 is interleaved inside the top 2 — not appended
        # after every chunk.
        keys = [i.key for i in fused]
        assert set(keys) == {"c1", "c2", "p1"}
        assert keys.index("p1") <= 1
        assert keys[-1] == "c2"

    def test_kg_interleaves_by_rank(self):
        """KG item at rank 1 must outrank a chunk at rank 20."""
        from evaluation.fusion import rrf_fuse_items
        from evaluation.metrics import RankedItem

        dense = [RankedItem(kind="chunk", key=f"c{i}", family="fssai", section=str(i)) for i in range(1, 21)]
        kg = [RankedItem(kind="kg", key="p1", family="fssai", section="1")]
        fused = rrf_fuse_items(dense, kg, top_k=20)
        keys = [i.key for i in fused]
        # 1/61 (kg@1) > 1/81 (chunk@20) -> p1 sits well above the tail (it
        # ties c1 at 1/61, so it can only be position 0 or 1).
        assert keys.index("p1") <= 1


# --------------------------------------------------------------------------- #
# evaluation.metrics.build_ranked_items — fused_items path
# --------------------------------------------------------------------------- #


class TestBuildRankedItemsFused:
    def _arm_result(self) -> dict:
        # Legacy shape: 3 vector chunks + 1 KG provision tail-appended.
        return {
            "chunk_ids": ["p1", "p2", "p3"],
            "kg_provisions": [
                {
                    "provision_id": "KG_1",
                    "provision_number": "9",
                    "instrument_title": "Food Safety and Standards Act, 2006",
                }
            ],
        }

    def test_legacy_tail_appends_kg_after_chunks(self):
        """Baseline behaviour (pre-repair) — KG always ranks after chunks."""
        from evaluation.metrics import build_ranked_items

        payload_index = {
            "p1": {"act_name": "Food Safety and Standards Act, 2006", "section_number": "16"},
            "p2": {"act_name": "Food Safety and Standards Act, 2006", "section_number": "31"},
            "p3": {"act_name": "Food Safety and Standards Act, 2006", "section_number": "32"},
        }
        family_map = _family_map()
        items = build_ranked_items(self._arm_result(), payload_index, family_map)
        assert [i.key for i in items] == ["p1", "p2", "p3", "KG_1"]
        assert items[-1].kind == "kg"

    def test_fused_items_override_tail_order(self):
        """Repaired path: fused_items ranking is used verbatim (KG interleaved)."""
        from evaluation.metrics import build_ranked_items

        payload_index = {
            "p1": {"act_name": "Food Safety and Standards Act, 2006", "section_number": "16"},
        }
        result = dict(self._arm_result())
        result["fused_items"] = [
            {"kind": "kg", "key": "KG_1", "family": "fssai", "section": "9"},
            {"kind": "chunk", "key": "p1", "family": "fssai", "section": "16"},
            {"kind": "chunk", "key": "p2", "family": "fssai", "section": "31"},
        ]
        items = build_ranked_items(result, payload_index, _family_map())
        assert [i.key for i in items] == ["KG_1", "p1", "p2"]
        assert items[0].kind == "kg"
        assert items[0].family == "fssai"

    def test_score_question_credits_fused_kg_at_rank(self):
        """A gold unit covered only by a fused KG item at rank 1 must score MRR=1."""
        from evaluation.metrics import score_question

        q = _question_with_gold(["fssai:s9(1)"])
        payload_index = {}
        result = {
            "arm": "G_ds_kg_rrf",
            "chunk_ids": [],
            "kg_provisions": [
                {
                    "provision_id": "KG_1",
                    "provision_number": "9",
                    "instrument_title": "Food Safety and Standards Act, 2006",
                }
            ],
            "fused_items": [{"kind": "kg", "key": "KG_1", "family": "fssai", "section": "9"}],
        }
        m = score_question(q, result, payload_index, _family_map())
        assert m.mrr == 1.0
        assert m.recall[1] == 1.0


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _family_map():
    """Minimal FamilyMap whose alias table resolves fssai titles without the registry."""
    from evaluation.resolution import FamilyMap

    fm = FamilyMap.__new__(FamilyMap)
    fm.family_to_acts = {"fssai": ["Food Safety and Standards Act, 2006"]}
    fm.act_to_family = {"food safety and standards act 2006": "fssai"}
    fm.alias_list = [("fssai", "food safety and standards")]
    return fm


def _question_with_gold(provision_ids: list[str]):
    from evaluation.benchmark import BenchmarkQuestion, GoldUnit

    q = BenchmarkQuestion(raw={"question_id": "Q001", "question": "test", "domains": ["FOOD_SAFETY"]})
    q.gold_units = [
        GoldUnit(
            provision_id=pid,
            family=pid.split(":", 1)[0],
            section="9",
            act="Food Safety and Standards Act, 2006",
            collection=None,
            document_id=None,
            gain=2.0,
            role="primary",
        )
        for pid in provision_ids
    ]
    return q

"""Offline retrieval metrics for the KG fusion P1 work."""

from __future__ import annotations

from app.rag.retrieval.result import RetrievedChunk
from evaluation.benchmark import GoldUnit
from evaluation.kg_retrieval_metrics import (
    aggregate,
    dedupe_stats,
    fusion_comparison,
    payload_quality,
    score_items,
)
from evaluation.metrics import RankedItem


def _family_map():
    from evaluation.resolution import FamilyMap

    fm = FamilyMap.__new__(FamilyMap)
    fm.family_to_acts = {"fssai": ["Food Safety and Standards Act, 2006"]}
    fm.act_to_family = {"food safety and standards act 2006": "fssai"}
    fm.alias_list = [("fssai", "food safety and standards")]
    return fm


def _chunk(cid: str, section: str, *, kg: bool = False, body: str = "") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        score=1.0,
        text=body,
        act_name="Food Safety and Standards Act, 2006",
        document_title="Food Safety and Standards Act, 2006",
        section_number=section,
        document_type="KG-Provision" if kg else "act",
    )


def _gold(section: str, *, gain: float = 2.0) -> GoldUnit:
    return GoldUnit(
        provision_id=f"fssai:s{section}",
        family="fssai",
        section=section,
        act="Food Safety and Standards Act, 2006",
        collection=None,
        document_id=None,
        gain=gain,
        role="primary",
    )


def test_payload_quality_counts_stub_and_backfill():
    provisions = [
        {"provision_id": "p1", "instrument_title": "FSS Act", "provision_number": "1", "text": "x" * 300},
        {"provision_id": "p2", "instrument_title": "FSS Act", "provision_number": "2", "text": "x" * 100},
        {"provision_id": "p3", "instrument_title": "FSS Act", "provision_number": "3"},
    ]
    q = payload_quality(provisions, min_body_chars=200)
    assert q["n"] == 3
    assert q["injectable"] == 1
    assert q["stub"] == 2
    assert q["body_coverage"] == 0.3333
    assert q["stub_rate"] == 0.6667

    # Backfill rescues p2; p3 still has no body.
    from kg.hybrid import backfill_key

    backfill = {backfill_key("FSS Act", "2"): "y" * 400}
    q = payload_quality(provisions, min_body_chars=200, backfill=backfill)
    assert q["injectable"] == 2
    assert q["stub"] == 1


def test_dedupe_stats_uses_act_section_and_source_ids():
    vector = [_chunk("v16", "16")]
    provisions = [
        {
            "provision_id": "p16",
            "instrument_title": "Food Safety and Standards Act, 2006",
            "provision_number": "16",
            "text": "x" * 300,
        },
        {
            "provision_id": "p3",
            "instrument_title": "Air (Prevention and Control of Pollution) Act, 1981",
            "provision_number": "3",
            "text": "x" * 300,
        },
    ]
    stats = dedupe_stats(vector, provisions)
    assert stats["kg_total"] == 2
    assert stats["dedupe_dropped"] == 1
    assert stats["dedupe_rate"] == 0.5

    # source_chunk_ids is the other bridge: a KG provision that names the
    # retrieved chunk is redundant even when its act/section spelling differs.
    provisions[1]["source_chunk_ids"] = ["v16"]
    stats = dedupe_stats(vector, provisions)
    assert stats["dedupe_dropped"] == 2


def test_score_items_recall_mrr_ndcg():
    items = [
        RankedItem(kind="chunk", key="v1", family="fssai", section="1"),
        RankedItem(kind="chunk", key="v2", family="fssai", section="2"),
        RankedItem(kind="chunk", key="v3", family="fssai", section="3"),
    ]
    units = [_gold("2"), _gold("3")]
    scored = score_items(items, units)
    assert scored["recall@5"] == 1.0
    assert scored["mrr"] == 0.5  # first gold at rank 2
    assert scored["ndcg@5"] > 0.0
    assert scored["unit_ranks"]["fssai:s2"] == 2


def test_fair_fusion_keeps_vector_gold_that_raw_rrf_drops():
    # 20 vector chunks, gold at both ends of the window.  A rank-1 KG chunk
    # pushes the raw interleave's tail out; fair fusion keeps the vector top-k.
    vector = [_chunk(f"v{i}", str(i)) for i in range(20)]
    kg = [_chunk("KG:novel", "99", kg=True, body="z" * 300)]
    payload_index = {
        f"v{i}": {"act_name": "Food Safety and Standards Act, 2006", "section_number": str(i)}
        for i in range(20)
    }
    units = [_gold("0"), _gold("19")]
    row = fusion_comparison(
        vector,
        kg,
        units,
        payload_index=payload_index,
        family_map=_family_map(),
        top_k=20,
    )
    assert row["displacement"]["lost_raw"] == ["fssai:s19"]
    assert row["displacement"]["lost_fair"] == []
    assert row["fair"]["recall@20"] == 1.0
    assert row["n_fair_kg"] == 0  # no spare slots in a full 20-slot window


def test_fair_fusion_uses_spare_slots_for_kg():
    vector = [_chunk(f"v{i}", str(i)) for i in range(5)]
    kg = [_chunk("KG:novel", "99", kg=True, body="z" * 300)]
    payload_index = {
        f"v{i}": {"act_name": "Food Safety and Standards Act, 2006", "section_number": str(i)}
        for i in range(5)
    }
    row = fusion_comparison(
        vector,
        kg,
        [_gold("0")],
        payload_index=payload_index,
        family_map=_family_map(),
        top_k=20,
    )
    assert row["n_fair_kg"] == 1
    assert row["fair"]["recall@20"] == 1.0


def test_aggregate_sums_displacement_and_means_metrics():
    row = {
        "vector": {"mrr": 1.0, "ndcg@10": 1.0, "recall@10": 1.0},
        "raw_rrf": {"mrr": 0.5, "ndcg@10": 0.5, "recall@10": 0.5},
        "fair": {"mrr": 1.0, "ndcg@10": 1.0, "recall@10": 1.0},
        "displacement": {"lost_raw": ["a"], "lost_fair": []},
        "kg_only_gold": ["b"],
    }
    agg = aggregate([row, row])
    assert agg["n"] == 2
    assert agg["raw_rrf"]["mrr"] == 0.5
    assert agg["vector_gold_lost_raw"] == 2
    assert agg["vector_gold_lost_fair"] == 0
    assert agg["kg_only_gold"] == 2

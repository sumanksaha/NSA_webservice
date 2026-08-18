"""Metric computation for every arm and every question (protocol §5–§11, §19).

All metrics are computed from the *ranked item list* of an arm result:

* chunk items  — Qdrant point ids, resolved to ``(family, section)`` keys via
  the payload index (``evaluation.resolution``)
* KG items     — provision dicts, resolved via instrument title + number

A gold unit is "hit" at the first rank where a covering item appears.  The
ranked list of an arm is defined as its chunk ids (in retrieval order) then
its KG provisions (in KG order).  ``recall@pool`` additionally measures the
union candidate set (chunks + KG) so KG expansions that land beyond rank 20
are still counted for the KG help/harm analysis.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from evaluation.benchmark import GoldUnit
from evaluation.config import (
    BOOTSTRAP_ITERATIONS,
    BOOTSTRAP_SEED,
    MCNAMER_ALPHA,
    RETRIEVAL_KS,
)
from evaluation.resolution import FamilyMap, norm_act_name, norm_section

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Ranked-item abstraction
# --------------------------------------------------------------------------- #
@dataclass
class RankedItem:
    kind: str  # "chunk" | "kg"
    key: str  # unique-ish id (chunk point id / kg provision_id)
    family: str | None = None
    section: str | None = None


def _kg_item_keys(p: dict[str, Any], family_map: FamilyMap) -> list[tuple[str | None, str | None]]:
    families = family_map.family_s_for_act(p.get("instrument_title") or p.get("title"))
    section = norm_section(p.get("provision_number"))
    return [(f, section) for f in families] or [(None, section)]


def build_ranked_items(
    arm_result: dict[str, Any],
    payload_index: dict[str, dict],
    family_map: FamilyMap,
) -> list[RankedItem]:
    """Chunk ids (payload-resolved) then KG provisions — in retrieval order.

    Fusion repair (2026-08-12): when the arm result carries a pre-ranked
    ``fused_items`` list (dense + sparse + KG interleaved by RRF — see
    ``evaluation/fusion.py``), that fused ranking is used verbatim instead of
    the legacy tail-concatenation (chunks first, KG provisions appended after
    every chunk, which structurally hid KG evidence from Recall@K / MRR /
    nDCG).  ``fused_items`` entries are ``{"kind", "key", "family",
    "section"}`` dicts.
    """
    fused = arm_result.get("fused_items")
    if fused:
        items: list[RankedItem] = []
        for d in fused:
            items.append(
                RankedItem(
                    kind=d.get("kind", "chunk"),
                    key=str(d.get("key", "")),
                    family=d.get("family"),
                    section=d.get("section"),
                )
            )
        return items

    items: list[RankedItem] = []
    seen: set[str] = set()
    for cid in arm_result.get("chunk_ids", []):
        cid = str(cid)
        if cid in seen:
            continue
        seen.add(cid)
        payload = payload_index.get(cid)
        if payload is None:
            continue
        # V5 resolution fix (2026-08-12): derive (family, section) keys via the
        # canonical fixed resolver (act_name + document_title unioned) instead
        # of the old ``act_name or document_title`` either/or, which hid
        # sub-instrument families named only in document_title (e.g. wbmo).
        # This raises the measured union-pool ceiling 0.6583 -> 0.7050.
        from evaluation.resolution import payload_to_keys

        for family, section in payload_to_keys(payload, family_map):
            items.append(RankedItem(kind="chunk", key=cid, family=family, section=section))
    for p in arm_result.get("kg_provisions", []):
        pid = p.get("provision_id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        for family, section in _kg_item_keys(p, family_map):
            items.append(RankedItem(kind="kg", key=pid, family=family, section=section))
    return items


def item_covers(item: RankedItem, unit: GoldUnit) -> bool:
    if item.family is None:
        return False
    if item.family != unit.family:
        return False
    if unit.section is None:
        return True
    return item.section is not None and item.section == unit.section


# --------------------------------------------------------------------------- #
# Per-question scoring
# --------------------------------------------------------------------------- #
@dataclass
class QuestionMetrics:
    question_id: str
    arm: str
    unit_ranks: dict[str, int | None] = field(default_factory=dict)  # provision_id -> 1-based rank
    pool_covered: set[str] = field(default_factory=set)  # provision ids in union pool
    recall: dict[int, float] = field(default_factory=dict)  # strict (primary+acceptable)
    recall_all: dict[int, float] = field(default_factory=dict)  # all gold units
    mrr: float = 0.0
    ndcg: dict[int, float] = field(default_factory=dict)
    precision: dict[int, float] = field(default_factory=dict)
    n_items: int = 0
    latency_ms: int = 0
    error: str | None = None
    retriever: str = ""


def _idcg(gains_sorted: list[float], k: int) -> float:
    dcg = 0.0
    for i in range(min(k, len(gains_sorted))):
        dcg += gains_sorted[i] / math.log2(i + 2)
    return dcg or 1e-9


def score_question(
    question: Any,
    arm_result: dict[str, Any],
    payload_index: dict[str, dict],
    family_map: FamilyMap,
) -> QuestionMetrics:
    units = question.relevant_units()  # primary + acceptable
    all_units = question.recall_units()
    m = QuestionMetrics(
        question_id=question.question_id,
        arm=arm_result.get("arm", "?"),
        latency_ms=int(arm_result.get("latency_ms", 0)),
        error=arm_result.get("error"),
        retriever=arm_result.get("retriever", ""),
    )
    items = build_ranked_items(arm_result, payload_index, family_map)
    m.n_items = len(items)

    # unit_ranks over ranked items (first hit)
    for unit in units:
        for i, item in enumerate(items):
            if item_covers(item, unit):
                m.unit_ranks[unit.provision_id] = i + 1
                break
        else:
            m.unit_ranks[unit.provision_id] = None

    # pool coverage: every item (incl. KG beyond rank 20)
    pool_items = items
    for unit in all_units:
        for item in pool_items:
            if item_covers(item, unit):
                m.pool_covered.add(unit.provision_id)
                break

    # Recall@K — strict (primary+acceptable)
    for k in RETRIEVAL_KS:
        hit = sum(1 for r in m.unit_ranks.values() if r is not None and r <= k)
        m.recall[k] = hit / max(len(units), 1)
    # Recall@K — all gold units (incl. supporting)
    for k in RETRIEVAL_KS:
        if not all_units:
            m.recall_all[k] = 0.0
            continue
        hit = 0
        for unit in all_units:
            r = m.unit_ranks.get(unit.provision_id)
            if r is not None and r <= k:
                hit += 1
        m.recall_all[k] = hit / len(all_units)

    # MRR over relevant units
    ranks = [r for r in m.unit_ranks.values() if r is not None]
    m.mrr = 1.0 / min(ranks) if ranks else 0.0

    # nDCG@k
    gains_by_rank: dict[int, float] = {}
    for unit in units:
        r = m.unit_ranks.get(unit.provision_id)
        if r is not None:
            gains_by_rank[r] = gains_by_rank.get(r, 0.0) + unit.gain
    ideal = sorted((u.gain for u in units), reverse=True)
    for k in (5, 10, 20):
        dcg = sum(g / math.log2(r + 1) for r, g in gains_by_rank.items() if r <= k)
        m.ndcg[k] = dcg / _idcg(ideal, k)

    # Precision@K — relevant items in top-K / K
    for k in (5, 10, 20):
        top = items[:k]
        rel = sum(1 for item in top if any(item_covers(item, u) for u in units))
        m.precision[k] = rel / k
    return m


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def aggregate(scores: list[QuestionMetrics]) -> dict[str, Any]:
    """Mean metrics across questions for one arm."""
    n = len(scores)
    if n == 0:
        return {}
    out: dict[str, Any] = {"n": n}
    for k in RETRIEVAL_KS:
        out[f"recall@{k}"] = round(sum(s.recall[k] for s in scores) / n, 4)
        out[f"recall_all@{k}"] = round(sum(s.recall_all[k] for s in scores) / n, 4)
    out["mrr"] = round(sum(s.mrr for s in scores) / n, 4)
    for k in (5, 10, 20):
        out[f"ndcg@{k}"] = round(sum(s.ndcg[k] for s in scores) / n, 4)
        out[f"precision@{k}"] = round(sum(s.precision[k] for s in scores) / n, 4)
    out["avg_latency_ms"] = round(sum(s.latency_ms for s in scores) / n, 1)
    out["errors"] = sum(1 for s in scores if s.error)
    return out


# --------------------------------------------------------------------------- #
# Legal evidence metrics (protocol §8) — computed on ARM F's evidence set
# --------------------------------------------------------------------------- #
def legal_evidence(
    question: Any,
    arm_result: dict[str, Any],
    payload_index: dict[str, dict],
    family_map: FamilyMap,
) -> dict[str, Any]:
    """Per-question binary legal-evidence axes + composite score."""
    items = build_ranked_items(arm_result, payload_index, family_map)
    units = question.relevant_units()
    top20 = items[:20]

    # instrument / provision / authority / jurisdiction / temporal
    gold_families = {u.family for u in units}
    instrument_correct = any(item.family in gold_families for item in top20) and bool(gold_families)

    provision_hits = [u.provision_id for u in units if item_covers_any(u, top20)]
    provision_correct = bool(provision_hits)
    completeness = len({u.provision_id for u in units if item_covers_any(u, top20)}) == len(units)
    sufficiency = any(u.role == "primary" and item_covers_any(u, top20) for u in units)

    # authorities — from chunk payloads + KG provision authorities
    retrieved_authorities: set[str] = set()
    for cid in arm_result.get("chunk_ids", [])[:20]:
        pl = payload_index.get(str(cid)) or {}
        a = pl.get("authority")
        if a:
            retrieved_authorities.add(norm_act_name(str(a)))
        t = pl.get("document_title") or ""
        for name in question.gold_authorities:
            if name and norm_act_name(name) in norm_act_name(t):
                retrieved_authorities.add(norm_act_name(name))
    authority_correct = False
    for name in question.gold_authorities:
        n = norm_act_name(name)
        if n and any(n in ra or ra in n for ra in retrieved_authorities):
            authority_correct = True
            break

    # jurisdiction — component-token overlap
    gold_jur = norm_act_name(question.jurisdiction)
    retrieved_jur: set[str] = set()
    for cid in arm_result.get("chunk_ids", [])[:20]:
        pl = payload_index.get(str(cid)) or {}
        j = pl.get("jurisdiction")
        if j:
            retrieved_jur.add(norm_act_name(str(j)))
    jurisdiction_correct = False
    for tok in gold_jur.split():
        if len(tok) >= 3 and any(tok in rj or rj in tok for rj in retrieved_jur):
            jurisdiction_correct = True
            break

    # temporal — no gold label in the benchmark; flag consistency only
    statuses = {
        str(pl.get("status") or "")
        for cid in arm_result.get("chunk_ids", [])[:20]
        if (pl := payload_index.get(str(cid)))
    }
    temporal_correct = None  # gold signal missing
    if "Temporal" in question.question_types:
        temporal_correct = "repealed" not in " ".join(statuses).lower()

    axes = {
        "instrument_correct": instrument_correct,
        "provision_correct": provision_correct,
        "authority_correct": authority_correct,
        "jurisdiction_correct": jurisdiction_correct,
        "completeness": completeness,
        "evidence_sufficiency": sufficiency,
    }
    binary = [v for v in axes.values() if isinstance(v, bool)]
    composite = round(sum(binary) / len(binary), 3) if binary else None
    return {
        "axes": axes,
        "composite_legal_evidence_score": composite,
        "temporal_correct": temporal_correct,
        "gold_families": sorted(gold_families),
        "retrieved_families": sorted({f for f, _ in [(i.family, i.section) for i in top20] if f}),
        "n_gold_units": len(units),
    }


def item_covers_any(unit: GoldUnit, items: list) -> bool:
    from evaluation.metrics import item_covers

    return any(item_covers(i, unit) for i in items)


# --------------------------------------------------------------------------- #
# KG help / harm (protocol §9)
# --------------------------------------------------------------------------- #
def kg_incremental(
    c_metrics: QuestionMetrics,
    e_metrics: QuestionMetrics,
    kg_arm_result: dict[str, Any],
    question: Any,
    family_map: FamilyMap,
) -> dict[str, Any]:
    """KG contribution per question: C (hybrid) vs E (hybrid + KG expansion).

    * ``helped`` — gold units the KG pool covers that the hybrid pool missed
      (a wrong/insufficient retrieval turned correct — protocol §9).
    * ``harm`` — KG returned provisions from an instrument family outside the
      question's gold families (cross-family noise that could mislead).
    * ``noise`` — KG provisions that match no gold unit at all.
    """
    c_hit = set(c_metrics.pool_covered)
    e_hit = set(e_metrics.pool_covered)
    helped = e_hit - c_hit
    gold_families = {u.family for u in question.relevant_units()}
    kg_families: set[str] = set()
    kg_provision_ids: set[str] = set()
    for p in kg_arm_result.get("kg_provisions", []):
        for f, _ in _kg_item_keys(p, family_map):
            if f:
                kg_families.add(f)
        if p.get("provision_id"):
            kg_provision_ids.add(p["provision_id"])
    harm = bool(kg_families - gold_families) and bool(kg_families)
    kg_noise = sum(
        1
        for p in kg_arm_result.get("kg_provisions", [])
        if not any(
            item_covers(
                RankedItem(kind="kg", key=p.get("provision_id", ""), family=f, section=s),
                u,
            )
            for u in question.recall_units()
            for f, s in _kg_item_keys(p, family_map)
        )
    )
    return {
        "question_id": question.question_id,
        "kg_provision_count": len(kg_arm_result.get("kg_provisions", [])),
        "kg_unique_ids": len(kg_provision_ids),
        "kg_families": sorted(kg_families),
        "gold_families": sorted(gold_families),
        "hybrid_pool_gold": sorted(c_hit),
        "hybrid_kg_pool_gold": sorted(e_hit),
        "kg_added_gold": sorted(helped),
        "kg_helped": bool(helped),
        "kg_harm": harm,
        "kg_noise_count": kg_noise,
    }


# --------------------------------------------------------------------------- #
# Statistical significance (protocol §19)
# --------------------------------------------------------------------------- #
def paired_bootstrap_ci(
    scores_a: list[float],
    scores_b: list[float],
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """95% percentile bootstrap CI for the mean difference (B - A)."""
    a = np.asarray(scores_a, dtype=float)
    b = np.asarray(scores_b, dtype=float)
    n = len(a)
    if n == 0:
        return {"mean_diff": 0.0, "ci95": (0.0, 0.0), "n": 0}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(iterations, n))
    diffs = (b[idx] - a[idx]).mean(axis=1)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {
        "mean_diff": float(diffs.mean()),
        "ci95": (float(lo), float(hi)),
        "n": n,
        "iterations": iterations,
    }


def mcnemar(
    correct_a: list[bool],
    correct_b: list[bool],
    alpha: float = MCNAMER_ALPHA,
) -> dict[str, Any]:
    """McNemar's test for paired binary outcomes (protocol §19)."""
    b = sum(1 for x, y in zip(correct_a, correct_b, strict=False) if x and not y)
    c = sum(1 for x, y in zip(correct_a, correct_b, strict=False) if not x and y)
    n_discordant = b + c
    if n_discordant == 0:
        p = 1.0
        stat = 0.0
    else:
        # exact binomial sign test (preferred when discordant pairs are few)
        p = 2 * min(
            sum(math.comb(n_discordant, k) * 0.5**n_discordant for k in range(min(b, c) + 1)),
            0.5,
        )
        stat = (abs(b - c) - 1) ** 2 / n_discordant if n_discordant else 0.0
    return {
        "a_only_correct": b,
        "b_only_correct": c,
        "discordant_pairs": n_discordant,
        "p_value": round(float(p), 6),
        "statistic": round(float(stat), 6),
        "significant_at_5pct": p < alpha,
    }

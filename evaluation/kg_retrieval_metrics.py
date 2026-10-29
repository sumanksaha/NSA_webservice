"""Offline retrieval-side metrics for the KG fusion P1 work.

The live A/B answers "does the KG change the final answer?"; this module
answers the cheaper questions that must hold first:

* **payload quality** — how many injected provisions carry a real statutory
  body, how many are pointer-only stubs, and what the body-length
  distribution looks like;
* **dedupe rate** — how many graph provisions merely restate a retrieved
  chunk and therefore spend no prompt slot;
* **gold coverage / MRR / nDCG** — vector-only vs raw RRF vs the fair merge
  (``rrf_fuse_chunks(preserve_vector_top_k=True)``);
* **displacement** — vector gold that the raw interleave drops from the
  truncated window, and whether the fair merge keeps it.

Everything here is pure: no Neo4j, no Qdrant, no LLM.  A row is a dict with
``vector_chunks`` (dicts or ``RetrievedChunk``), ``kg_provisions`` (KG query
dicts), and ``gold_units`` (dicts with ``family``/``section``/``gain``).
The CLI reads JSONL rows and prints a JSON summary.

Usage:
    python -m evaluation.kg_retrieval_metrics --rows rows.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from evaluation.metrics import RankedItem, item_covers
from evaluation.resolution import FamilyMap, norm_section, payload_to_keys

DEFAULT_MIN_BODY_CHARS = 200
DEFAULT_TOP_K = 20
DEFAULT_RRF_K = 60.0
_KS = (5, 10, 20)


# --------------------------------------------------------------------------- #
# Payload quality
# --------------------------------------------------------------------------- #
def _backfilled_body(provision: dict[str, Any], backfill: dict[tuple[str, str], str] | None) -> str:
    from kg.hybrid import backfill_key_variants, provision_body_text

    body = provision_body_text(provision)
    if not backfill:
        return body
    instrument = str(provision.get("instrument_title") or provision.get("title") or "")
    number = str(provision.get("provision_number") or "")
    for key in backfill_key_variants(instrument, number, str(provision.get("provision_id") or "")):
        candidate = backfill.get(key) or ""
        if len(candidate) > len(body):
            body = candidate
    return body


def payload_quality(
    provisions: Iterable[dict[str, Any]],
    *,
    min_body_chars: int = DEFAULT_MIN_BODY_CHARS,
    backfill: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    """Body coverage and stub rate for a set of KG provisions.

    A provision counts as *injectable* when its resolved body reaches
    ``min_body_chars``.  The production gate uses the same threshold
    (``RAG_KG_MIN_BODY_CHARS``, default 200).
    """
    bodies = [len(_backfilled_body(p, backfill)) for p in provisions]
    n = len(bodies)
    injectable = sum(1 for length in bodies if length >= min_body_chars)
    stubs = n - injectable
    return {
        "n": n,
        "injectable": injectable,
        "stub": stubs,
        "body_coverage": round(injectable / n, 4) if n else 0.0,
        "stub_rate": round(stubs / n, 4) if n else 0.0,
        "median_body_chars": round(float(statistics.median(bodies)), 1) if bodies else 0.0,
        "min_body_chars": min_body_chars,
    }


# --------------------------------------------------------------------------- #
# Dedupe rate
# --------------------------------------------------------------------------- #
def dedupe_stats(
    vector_chunks: list[Any],
    kg_provisions: Iterable[dict[str, Any]],
    *,
    backfill: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    """How many KG provisions are redundant with the vector pool.

    Uses the production dedupe path (act/section normalisation and
    ``source_chunk_ids``), not a parallel reimplementation.
    """
    from kg.hybrid import _dedupe_kg_over_chunks, provisions_to_retrieved_chunks

    kg_chunks = provisions_to_retrieved_chunks(kg_provisions, limit=10_000, backfill=backfill)
    kept = _dedupe_kg_over_chunks([list(vector_chunks), kg_chunks])[1]
    total = len(kg_chunks)
    dropped = total - len(kept)
    return {
        "kg_total": total,
        "kg_kept": len(kept),
        "dedupe_dropped": dropped,
        "dedupe_rate": round(dropped / total, 4) if total else 0.0,
    }


# --------------------------------------------------------------------------- #
# Ranked items
# --------------------------------------------------------------------------- #
def _items_from_chunks(
    chunks: Iterable[Any],
    payload_index: dict[str, dict],
    family_map: FamilyMap,
) -> list[RankedItem]:
    items: list[RankedItem] = []
    for chunk in chunks:
        cid = str(getattr(chunk, "chunk_id", "") or "")
        if not cid:
            continue
        payload = payload_index.get(cid) or {
            "act_name": getattr(chunk, "act_name", "") or getattr(chunk, "document_title", ""),
            "section_number": getattr(chunk, "section_number", ""),
        }
        for family, section in payload_to_keys(payload, family_map):
            items.append(RankedItem(kind="chunk", key=cid, family=family, section=section))
    return items


def _items_from_kg_chunks(chunks: Iterable[Any], family_map: FamilyMap) -> list[RankedItem]:
    items: list[RankedItem] = []
    for chunk in chunks:
        cid = str(getattr(chunk, "chunk_id", "") or "")
        if not cid:
            continue
        section = norm_section(getattr(chunk, "section_number", ""))
        title = getattr(chunk, "act_name", "") or getattr(chunk, "document_title", "")
        families = family_map.family_s_for_act(title)
        for family in families or [None]:
            items.append(RankedItem(kind="kg", key=cid, family=family, section=section))
    return items


def _dedupe_items(items: Iterable[RankedItem]) -> list[RankedItem]:
    out: list[RankedItem] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for item in items:
        key = (item.key, item.family, item.section)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def score_items(
    items: Iterable[RankedItem],
    gold_units: Iterable[Any],
    *,
    ks: tuple[int, ...] = _KS,
) -> dict[str, Any]:
    """Recall@K, MRR, nDCG@K and precision@K for one ranked item list."""
    ranked = _dedupe_items(items)
    units = list(gold_units)
    unit_ranks: dict[str, int | None] = {}
    for unit in units:
        pid = str(getattr(unit, "provision_id", "") or f"{unit.family}:{unit.section}")
        for rank, item in enumerate(ranked, start=1):
            if item_covers(item, unit):
                unit_ranks[pid] = rank
                break
        else:
            unit_ranks[pid] = None

    out: dict[str, Any] = {"n_items": len(ranked), "n_units": len(units)}
    for k in ks:
        hit = sum(1 for rank in unit_ranks.values() if rank is not None and rank <= k)
        out[f"recall@{k}"] = round(hit / max(len(units), 1), 4)
    ranks = [rank for rank in unit_ranks.values() if rank is not None]
    out["mrr"] = round(1.0 / min(ranks), 4) if ranks else 0.0

    gains_by_rank: dict[int, float] = {}
    for unit in units:
        pid = str(getattr(unit, "provision_id", "") or f"{unit.family}:{unit.section}")
        rank = unit_ranks.get(pid)
        if rank is not None:
            gains_by_rank[rank] = gains_by_rank.get(rank, 0.0) + float(getattr(unit, "gain", 1.0))
    ideal = sorted((float(getattr(unit, "gain", 1.0)) for unit in units), reverse=True)
    for k in (5, 10, 20):
        dcg = sum(gain / math.log2(rank + 1) for rank, gain in gains_by_rank.items() if rank <= k)
        idcg = sum(gain / math.log2(i + 2) for i, gain in enumerate(ideal[:k])) or 1e-9
        out[f"ndcg@{k}"] = round(dcg / idcg, 4)
    for k in (5, 10, 20):
        top = ranked[:k]
        relevant = sum(1 for item in top if any(item_covers(item, unit) for unit in units))
        out[f"precision@{k}"] = round(relevant / k, 4)
    out["unit_ranks"] = unit_ranks
    return out


def _covered_units(items: Iterable[RankedItem], units: Iterable[Any]) -> set[str]:
    covered: set[str] = set()
    for unit in units:
        pid = str(getattr(unit, "provision_id", "") or f"{unit.family}:{unit.section}")
        if any(item_covers(item, unit) for item in items):
            covered.add(pid)
    return covered


# --------------------------------------------------------------------------- #
# Fusion comparison
# --------------------------------------------------------------------------- #
def fusion_comparison(
    vector_chunks: list[Any],
    kg_chunks: list[Any],
    gold_units: Iterable[Any],
    *,
    payload_index: dict[str, dict] | None = None,
    family_map: FamilyMap | None = None,
    top_k: int = DEFAULT_TOP_K,
    rrf_k: float = DEFAULT_RRF_K,
) -> dict[str, Any]:
    """Compare vector-only, raw RRF and fair merge on the same candidates.

    ``kg_chunks`` are already-converted ``RetrievedChunk`` objects (see
    :func:`kg.hybrid.provisions_to_retrieved_chunks`).  The fair merge must
    keep every vector gold unit that vector-only retrieval had inside
    ``top_k``; raw RRF is allowed to drop it.
    """
    import copy

    from kg.hybrid import rrf_fuse_chunks

    payload_index = payload_index or {}
    family_map = family_map or FamilyMap()
    units = list(gold_units)

    vector_items = _items_from_chunks(vector_chunks, payload_index, family_map)
    kg_items = _items_from_kg_chunks(kg_chunks, family_map)
    item_by_id: dict[str, list[RankedItem]] = {}
    for item in (*vector_items, *kg_items):
        item_by_id.setdefault(item.key, []).append(item)

    def _fused_items(fused: list[Any]) -> list[RankedItem]:
        out: list[RankedItem] = []
        for chunk in fused:
            out.extend(item_by_id.get(str(getattr(chunk, "chunk_id", "")), []))
        return out

    vector_top = vector_items[:top_k]
    raw = rrf_fuse_chunks(
        [[copy.copy(c) for c in vector_chunks], [copy.copy(c) for c in kg_chunks]],
        rrf_k=rrf_k,
        top_k=top_k,
        preserve_vector_top_k=False,
    )
    fair = rrf_fuse_chunks(
        [[copy.copy(c) for c in vector_chunks], [copy.copy(c) for c in kg_chunks]],
        rrf_k=rrf_k,
        top_k=top_k,
        preserve_vector_top_k=True,
    )

    vector_covered = _covered_units(vector_top, units)
    raw_covered = _covered_units(_fused_items(raw), units)
    fair_covered = _covered_units(_fused_items(fair), units)
    kg_covered = _covered_units(kg_items, units)
    return {
        "top_k": top_k,
        "vector": score_items(vector_top, units),
        "raw_rrf": score_items(_fused_items(raw), units),
        "fair": score_items(_fused_items(fair), units),
        "kg_only_gold": sorted(kg_covered - vector_covered),
        "displacement": {
            "vector_gold": sorted(vector_covered),
            "lost_raw": sorted(vector_covered - raw_covered),
            "lost_fair": sorted(vector_covered - fair_covered),
        },
        "n_raw": len(raw),
        "n_fair": len(fair),
        "n_fair_kg": sum(1 for c in fair if str(getattr(c, "document_type", "")) == "KG-Provision"),
    }


# --------------------------------------------------------------------------- #
# Aggregation / CLI
# --------------------------------------------------------------------------- #
def aggregate(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Mean the per-question fusion metrics; sum the payload/dedupe counts."""
    rows = list(rows)
    if not rows:
        return {"n": 0}

    def _mean(path: str) -> float:
        values = []
        for row in rows:
            cur: Any = row
            for part in path.split("."):
                cur = (cur or {}).get(part) if isinstance(cur, dict) else None
            if isinstance(cur, (int, float)):
                values.append(float(cur))
        return round(sum(values) / len(values), 4) if values else 0.0

    return {
        "n": len(rows),
        "vector": {
            "mrr": _mean("vector.mrr"),
            "ndcg@10": _mean("vector.ndcg@10"),
            "recall@10": _mean("vector.recall@10"),
        },
        "raw_rrf": {
            "mrr": _mean("raw_rrf.mrr"),
            "ndcg@10": _mean("raw_rrf.ndcg@10"),
            "recall@10": _mean("raw_rrf.recall@10"),
        },
        "fair": {
            "mrr": _mean("fair.mrr"),
            "ndcg@10": _mean("fair.ndcg@10"),
            "recall@10": _mean("fair.recall@10"),
        },
        "vector_gold_lost_raw": sum(len(row.get("displacement", {}).get("lost_raw", [])) for row in rows),
        "vector_gold_lost_fair": sum(len(row.get("displacement", {}).get("lost_fair", [])) for row in rows),
        "kg_only_gold": sum(len(row.get("kg_only_gold", [])) for row in rows),
    }


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True, help="JSONL rows with vector_chunks/kg_provisions/gold_units")
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    ap.add_argument("--min-body-chars", type=int, default=DEFAULT_MIN_BODY_CHARS)
    args = ap.parse_args()

    from app.rag.retrieval.result import RetrievedChunk
    from evaluation.benchmark import GoldUnit

    family_map = FamilyMap()
    out_rows: list[dict[str, Any]] = []
    payloads: list[dict[str, Any]] = []
    for raw in _load_rows(Path(args.rows)):
        vector = [
            c if isinstance(c, RetrievedChunk) else RetrievedChunk.from_dict(c)
            for c in (raw.get("vector_chunks") or [])
        ]
        kg = [
            c if isinstance(c, RetrievedChunk) else RetrievedChunk.from_dict(c)
            for c in (raw.get("kg_chunks") or [])
        ]
        provisions = list(raw.get("kg_provisions") or [])
        payloads.extend(provisions)
        payload_index = {
            str(c.chunk_id): {
                "act_name": getattr(c, "act_name", "") or getattr(c, "document_title", ""),
                "section_number": getattr(c, "section_number", ""),
            }
            for c in vector
        }
        units = [
            GoldUnit(
                provision_id=str(u.get("provision_id") or ""),
                family=str(u.get("family") or ""),
                section=str(u["section"]) if u.get("section") is not None else None,
                act=str(u.get("act") or ""),
                collection=u.get("collection"),
                document_id=u.get("document_id"),
                gain=float(u.get("gain", 1.0)),
                role=str(u.get("role") or "primary"),
            )
            for u in (raw.get("gold_units") or [])
        ]
        row = fusion_comparison(
            vector,
            kg,
            units,
            payload_index=payload_index,
            family_map=family_map,
            top_k=args.top_k,
        )
        row["question_id"] = raw.get("question_id")
        row.update(dedupe_stats(vector, provisions))
        out_rows.append(row)

    summary = aggregate(out_rows)
    summary["payload"] = payload_quality(payloads, min_body_chars=args.min_body_chars)
    json.dump(summary, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

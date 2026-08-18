"""RANKING_CEILING_V3 — Task 3: legal-aware reranker (offline, no LLM).

Reranks the cached union candidate pool (dense@200 ∪ sparse@200 ∪ KG@200)
with **query-only** legal features — the query's detected section number and
Act name (from the question text, as in a production query rewriter) matched
against each candidate chunk's payload, plus a lexical-overlap term.

Features (all derivable at serving time, no gold labels):
    sec_match    query-detected section == payload section_number
    act_match    query-detected Act family == payload family
    exact        both act + section match
    lex          token overlap(query, chunk_text)

score = RRF(dense, sparse, KG)  +  w_sec*sec + w_act*act + w_exact*exact + w_lex*lex

Measures how much of the "gold in 11-500" recoverable rate converts to R@10.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.query_expansion import detect_act, detect_section

_STOP = frozenset({
    "the", "a", "an", "of", "and", "or", "to", "in", "for", "under", "what",
    "which", "who", "how", "is", "are", "does", "do", "be", "by", "on", "at",
    "with", "from", "as", "that", "this", "its", "it", "not", "shall", "may",
    "act", "section", "sec", "food", "safety", "standards",
})


def tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP}


def legal_features(payload: dict, query: str, family_map) -> dict[str, float]:
    """Query-only legal features for one candidate payload."""
    sec_q, _sub = detect_section(query)
    act_q = detect_act(query)
    payload_sec = re.match(r"\s*(\d{1,4})", str(payload.get("section_number") or "") or "")
    psec = payload_sec.group(1) if payload_sec else None

    act_match = 0.0
    if act_q:
        # V5 resolution fix: consult BOTH act_name and document_title (unioned),
        # matching evaluation.resolution.payload_to_keys semantics.
        from evaluation.resolution import payload_to_keys

        fams = {f for f, _ in payload_to_keys(payload, family_map)}
        # canonical act -> family
        q_fam = family_map.family_for_act(act_q)
        if q_fam and q_fam in fams:
            act_match = 1.0

    sec_match = 1.0 if (sec_q and psec == sec_q) else 0.0
    exact = 1.0 if (sec_match and act_match) else 0.0

    text = str(payload.get("chunk_text") or payload.get("text") or "")
    q_toks = tokenize(query)
    t_toks = tokenize(text)
    lex = len(q_toks & t_toks) / len(q_toks) if q_toks and t_toks else 0.0
    return {"sec_match": sec_match, "act_match": act_match, "exact": exact, "lex": lex}


def rrf_scores(item_lists: list[list[dict]], rrf_k: float = 60.0) -> dict[str, float]:
    """RRF credit per candidate key (chunk id or kg provision key)."""
    scores: dict[str, float] = {}
    for items in item_lists:
        for rank, it in enumerate(items):
            key = str(it.get("key") or it.get("provision_id") or "")
            scores[key] = scores.get(key, 0.0) + 1.0 / (rank + 1 + rrf_k)
    return scores


def build_pool(dense_rec, sparse_rec, kg_rec, payload_index, family_map,
               slice_depth: int | None = None, kg_slice: int = 200) -> list[dict]:
    """Union pool items with kind/key/payload access; dedup by key.

    ``slice_depth`` (when not None) truncates dense/sparse chunk_ids to the
    first *slice_depth* and kg_provisions to the first 200 — so the depth
    parameter is honest: the pool really is dense@D ∪ sparse@D ∪ KG@200, and
    the RRF credit range matches it.
    """
    from evaluation.metrics import build_ranked_items

    def _sliced(rec: dict | None, n: int | None) -> dict | None:
        if rec is None or n is None:
            return rec
        out = dict(rec)
        out["chunk_ids"] = list(out.get("chunk_ids", []))[:n]
        return out

    items: list[dict] = []
    seen: set[str] = set()

    def add(kind: str, key: str, payload: dict | None):
        if not key or key in seen:
            return
        seen.add(key)
        items.append({"kind": kind, "key": key, "payload": payload or {}})

    for it in build_ranked_items(_sliced(dense_rec, slice_depth) or {}, payload_index, family_map):
        add("chunk", it.key, payload_index.get(it.key))
    for it in build_ranked_items(_sliced(sparse_rec, slice_depth) or {}, payload_index, family_map):
        add("chunk", it.key, payload_index.get(it.key))
    for p in (kg_rec or {}).get("kg_provisions", [])[:kg_slice]:
        add("kg", str(p.get("provision_id") or ""), p)
    return items


def rerank(pool: list[dict], query: str, family_map,
           rrf: dict[str, float], weights: dict[str, float]) -> list[dict]:
    """Score every pool candidate and return pool sorted by score desc."""
    scored = []
    for it in pool:
        feats = legal_features(it["payload"], query, family_map)
        r = rrf.get(it["key"], 0.0)
        s = (r
             + weights["sec"] * feats["sec_match"]
             + weights["act"] * feats["act_match"]
             + weights["exact"] * feats["exact"]
             + weights["lex"] * feats["lex"])
        it = dict(it)
        it["score"] = s
        it["feats"] = feats
        scored.append(it)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def rank_of(pool: list[dict], unit, payload_index, family_map) -> int | None:
    """First-hit rank of a gold unit in a ranked pool (chunks + KG)."""
    from evaluation.metrics import RankedItem, _kg_item_keys, item_covers
    from evaluation.resolution import matches_gold

    for i, it in enumerate(pool):
        if it["kind"] == "chunk":
            if matches_gold(payload_index.get(it["key"]) or {}, unit, family_map):
                return i + 1
        else:
            # KG provision -> RankedItem via the same key derivation as
            # metrics.build_ranked_items (instrument title -> families).
            for family, section in _kg_item_keys(it.get("payload") or {}, family_map):
                if item_covers(
                    RankedItem(kind="kg", key=it["key"], family=family, section=section),
                    unit,
                ):
                    return i + 1
    return None


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from app import create_app
    from evaluation.benchmark import load_questions
    from evaluation.report_ceiling import load_payload_index
    from evaluation.resolution import FamilyMap

    app = create_app()
    with app.app_context():
        payload_index = load_payload_index()
        family_map = FamilyMap()
        questions = {q.question_id: q for q in load_questions()}

        raw_dir = Path(os.environ.get("RERANK_RAW_DIR", "evaluation/out/ceiling_v3/raw"))
        kg_slice = int(os.environ.get("RERANK_KG_DEPTH", "200"))

        def load(arm: str) -> dict[str, dict]:
            recs = {}
            p = raw_dir / f"{arm}.jsonl"
            if p.exists():
                for line in p.open(encoding="utf-8"):
                    line = line.strip()
                    if line:
                        r = json.loads(line)
                        recs[r["question_id"]] = r
            return recs

        # Pool selection: base arms (A/B) or the V3 expanded-query arms.
        # RERANK_EXPANDED=1 measures the true *stacked* effect of query
        # expansion + legal rerank on the same union composition.
        expanded = os.environ.get("RERANK_EXPANDED", "0") == "1"
        dense_arm, sparse_arm = ("V3_dense", "V3_sparse") if expanded else ("A_dense", "B_sparse")
        dense = load(dense_arm)
        sparse = load(sparse_arm)
        kg = load("D_kg")
        # Union slice depth per arm (200 = report baseline; 500 = deep pool).
        try:
            slice_depth = int(os.environ.get("RERANK_DEPTH", "200"))
        except ValueError:
            slice_depth = 200

        def hit_at(pool, q, k):
            rel = q.relevant_units()
            for unit in rel:
                r = rank_of(pool, unit, payload_index, family_map)
                if r is not None and r <= k:
                    return True
            return False

        # weight grid
        grids = {
            "base_rrf": {"sec": 0.0, "act": 0.0, "exact": 0.0, "lex": 0.0},
            "sec_only": {"sec": 3.0, "act": 0.0, "exact": 0.0, "lex": 0.0},
            "sec_act": {"sec": 2.0, "act": 1.5, "exact": 0.0, "lex": 0.0},
            "full_legal": {"sec": 2.0, "act": 1.0, "exact": 4.0, "lex": 0.5},
            "lex_heavy": {"sec": 2.0, "act": 1.0, "exact": 3.0, "lex": 2.0},
        }

        # Integrity check: do full_legal and lex_heavy ever produce different
        # top-10 candidate sets? (They produced identical metrics; if the sets
        # never differ, that identity is structural, not coincidental.)
        top10_diff = 0
        out = {}
        # Precompute the base RRF ranking once per question; every grid's
        # conversion baseline is the RRF-ranked pool (not the naive build
        # order), so "converted into top-10" means: not in top-10 under base
        # RRF, but in top-10 after the legal rerank.
        per_q = {}
        for qid, q in questions.items():
            d, s, k = dense.get(qid), sparse.get(qid), kg.get(qid)
            if not (d and s and k):
                continue
            pool = build_pool(d, s, k, payload_index, family_map, slice_depth=slice_depth, kg_slice=kg_slice)
            if not pool:
                continue
            # Align with the report's union composition: dense@D + sparse@D + KG@200
            rrf = rrf_scores([
                [{"key": c} for c in d.get("chunk_ids", [])[:slice_depth]],
                [{"key": c} for c in s.get("chunk_ids", [])[:slice_depth]],
                [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:kg_slice]],
            ])
            base_ranked = rerank(pool, q.question, family_map, rrf, grids["base_rrf"])
            per_q[qid] = (q, pool, rrf, base_ranked)

        for gname, w in grids.items():
            # unit-level recall (fraction of relevant units hit <= K) — matches
            # the report's recall@K so numbers are directly comparable
            recall = {10: 0.0, 20: 0.0, 50: 0.0}
            # binary any-hit rate (per-question success)
            any_hits = {10: 0, 20: 0, 50: 0}
            n = 0
            conversions = 0
            for qid, (q, pool, rrf, base_ranked) in per_q.items():
                reranked = rerank(pool, q.question, family_map, rrf, w)
                n += 1
                rel = q.relevant_units()
                for kk in (10, 20, 50):
                    unit_hits = 0
                    for unit in rel:
                        r = rank_of(reranked, unit, payload_index, family_map)
                        if r is not None and r <= kk:
                            unit_hits += 1
                    recall[kk] += unit_hits / max(len(rel), 1)
                    any_hits[kk] += int(unit_hits > 0)
                # conversion: gold outside top-10 under base RRF, inside top-10
                # after the legal rerank
                base_hit10 = hit_at(base_ranked, q, 10)
                new_hit10 = hit_at(reranked, q, 10)
                if not base_hit10 and new_hit10:
                    conversions += 1
                # integrity: structural identity of full_legal vs lex_heavy
                if gname == "lex_heavy":
                    fl = rerank(pool, q.question, family_map, rrf, grids["full_legal"])
                    if {it["key"] for it in fl[:10]} != {it["key"] for it in reranked[:10]}:
                        top10_diff += 1
            out[gname] = {
                f"R@{k}": round(recall[k] / max(n, 1), 4) for k in (10, 20, 50)
            }
            out[gname]["any_hit_R@10"] = round(any_hits[10] / max(n, 1), 4)
            out[gname]["any_hit_R@20"] = round(any_hits[20] / max(n, 1), 4)
            out[gname]["n"] = n
            out[gname]["conversions_to_top10"] = conversions
        out["_meta"] = {
            "pool_arms": f"{dense_arm}+{sparse_arm}+D_kg",
            "slice_depth": slice_depth,
            "full_legal_vs_lex_heavy_top10_diffs": top10_diff,
        }


        # write deliverable next to the raw arms (keeps each experiment dir
        # self-contained and never overwrites a prior experiment's files)
        out_dir = raw_dir.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = "rerank_legal_expanded.json" if expanded else "rerank_legal.json"
        if slice_depth != 200:
            suffix = "_expanded" if expanded else ""
            fname = f"rerank_legal_depth{slice_depth}{suffix}.json"
        (out_dir / fname).write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

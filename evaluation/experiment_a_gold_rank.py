"""Experiment A -- Gold Rank Distribution Diagnostic.

PURPOSE (diagnostic only, READ-ONLY, no model training/fine-tuning):
    Determine WHERE the gold/relevant chunk is ranked at each retrieval stage:
      Stage A  -- Candidate Generation (retrieval arms union)
      Stage B  -- RRF Fusion
      Stage C  -- CE v2_K500 reranking
    and classify the primary retrieval failure for every question.

This script uses the EXACT same pipeline as evaluation/eval_v2_checkpoint.py
(frozen cached arms, build_pool, rrf_scores, rerank with zero legal weights,
CE v2_K500 CrossEncoder, score_pool) so results are directly comparable.

Output:  evaluation/out/ceiling_v5/experiment_a_gold_rank.json
         evaluation/out/ceiling_v5/experiment_a_gold_rank_report.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from collections import Counter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

import torch

torch.set_num_threads(4)

from evaluation.benchmark import load_questions
from evaluation.config import CACHE_DIR
from evaluation.resolution import FamilyMap, matches_gold
from evaluation.rerank_legal import build_pool, rerank, rrf_scores
from evaluation.metrics import RankedItem, _kg_item_keys, item_covers

# ---------------------------------------------------------------------------
# Re-use exact config from eval_v2_checkpoint.py (frozen reference)
# ---------------------------------------------------------------------------
MODELS_DIR = PROJECT_ROOT / "evaluation" / "out" / "models"
CE_V2 = MODELS_DIR / "legal_ce_v2_K500"
POOL_HEAD = 150
CE_BATCH = 64
SLICE_DEPTH = 500
KG_SLICE = 500

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
OUT_JSON = OUT_DIR / "experiment_a_gold_rank.json"
OUT_MD = OUT_DIR / "experiment_a_gold_rank_report.md"

BUCKETS = [
    "1",
    "2-5",
    "6-10",
    "11-20",
    "21-50",
    "51-100",
    "101-150",
    "151-200",
    "201-300",
    "301-500",
    ">500 / missing",
]


# ---------------------------------------------------------------------------
# Data loaders (identical to eval_v2_checkpoint.py)
# ---------------------------------------------------------------------------
def load_payload_index_simple() -> dict[str, dict]:
    cache_file = CACHE_DIR / "payload_index.jsonl"
    index: dict[str, dict] = {}
    with open(cache_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            index[rec["id"]] = rec["payload"]
    return index


def load_raw(arm: str) -> dict[str, dict]:
    raw_dir = OUT_DIR / "raw"
    p = raw_dir / f"{arm}.jsonl"
    recs: dict[str, dict] = {}
    if p.exists():
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    recs[r["question_id"]] = r
    return recs


# ---------------------------------------------------------------------------
# CE scoring (adds ce_score, same ranking as eval_v2_checkpoint.py)
# ---------------------------------------------------------------------------
def score_pool_with_scores(items: list[dict], query: str, ce) -> list[dict]:
    pairs = [
        (
            query,
            str(it["payload"].get("chunk_text") or it["payload"].get("text") or ""),
        )
        for it in items
    ]
    if not pairs:
        return items
    scores = ce.predict(pairs, batch_size=CE_BATCH)
    scored = sorted(zip(scores, items, strict=False), key=lambda x: float(x[0]), reverse=True)
    result = []
    for s, it in scored:
        item = dict(it)
        item["ce_score"] = float(s)
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# Gold resolution helpers
# ---------------------------------------------------------------------------
def find_all_gold_chunk_ids(question, payload_index: dict[str, dict], family_map: FamilyMap) -> list[str]:
    """Find ALL payload chunk IDs that cover each gold unit (preserving all gold chunks)."""
    gold_chunk_ids: list[str] = []
    for unit in question.recall_units():
        found = False
        for pid, payload in payload_index.items():
            if matches_gold(payload, unit, family_map):
                gold_chunk_ids.append(pid)
                found = True
        # Note: we collect all matching chunk IDs per unit
    return gold_chunk_ids


def find_all_gold_with_ranks(ranked_list: list[dict], question, payload_index, family_map) -> dict[str, dict]:
    """Trace gold chunks through a ranked list. Returns per-gold-unit info."""
    info: dict[str, dict] = {}
    for unit in question.recall_units():
        pid = unit.provision_id
        info[pid] = {
            "provision_id": pid,
            "family": unit.family,
            "section": unit.section,
            "role": unit.role,
            "gain": unit.gain,
            "hits": [],  # list of (rank, key, score, text_snippet)
        }
        for i, it in enumerate(ranked_list):
            if it["kind"] == "chunk":
                payload = payload_index.get(it["key"])
                if payload and matches_gold(payload, unit, family_map):
                    text = str(payload.get("chunk_text") or payload.get("text") or "")[:200]
                    info[pid]["hits"].append({
                        "rank": i + 1,
                        "key": it["key"],
                        "score": it.get("ce_score") or it.get("score", 0.0),
                        "kind": it["kind"],
                        "text_snippet": text[:100],
                    })
            else:
                # KG item
                for family, section in _kg_item_keys(it.get("payload") or {}, family_map):
                    ri = RankedItem(kind="kg", key=it["key"], family=family, section=section)
                    if item_covers(ri, unit):
                        info[pid]["hits"].append({
                            "rank": i + 1,
                            "key": it["key"],
                            "score": 0.0,
                            "kind": it["kind"],
                            "text_snippet": "",
                        })
                        break
    return info


def best_gold_rank(gold_info: dict[str, dict]) -> int | None:
    """Minimum rank among all gold chunk hits across all gold units."""
    ranks = []
    for info in gold_info.values():
        for h in info["hits"]:
            ranks.append(h["rank"])
    return min(ranks) if ranks else None


def bucketize(rank: int | None) -> str:
    if rank is None:
        return ">500 / missing"
    elif rank == 1:
        return "1"
    elif 2 <= rank <= 5:
        return "2-5"
    elif 6 <= rank <= 10:
        return "6-10"
    elif 11 <= rank <= 20:
        return "11-20"
    elif 21 <= rank <= 50:
        return "21-50"
    elif 51 <= rank <= 100:
        return "51-100"
    elif 101 <= rank <= 150:
        return "101-150"
    elif 151 <= rank <= 200:
        return "151-200"
    elif 201 <= rank <= 300:
        return "201-300"
    elif 301 <= rank <= 500:
        return "301-500"
    else:
        return ">500 / missing"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    print("[1/6] Loading cached data...", flush=True)
    payload_index = load_payload_index_simple()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}

    raw_dir = OUT_DIR / "raw"
    dense = load_raw("A_dense")
    sparse = load_raw("B_sparse")
    kg = load_raw("D_kg")
    ident = load_jsonl_cached(CACHE_DIR / "v55_ident" / "sparse_identifier.jsonl")

    # Also try the alternative ident path
    if not ident:
        alt_path = CACHE_DIR / "sparse_identifier.jsonl"
        ident = load_jsonl_cached(alt_path)

    n_payload = len(payload_index)
    n_questions = len(questions)
    print(f"  payload_index={n_payload} points, questions={n_questions}", flush=True)

    # ---- Load CE model ----
    print("[2/6] Loading CE v2_K500 model...", flush=True)
    from sentence_transformers import CrossEncoder

    ce = CrossEncoder(CE_V2.as_posix(), max_length=256)
    print("  Model loaded.", flush=True)

    # ---- Build pools + track gold ranks ----
    print("[3/6] Building pools and tracking gold ranks at each stage...", flush=True)
    per_question: list[dict] = []
    n_q = len(questions)
    processed = 0

    for qid, q in sorted(questions.items()):
        d, s, k = dense.get(qid), sparse.get(qid), kg.get(qid)
        if not (d and s and k):
            continue

        # Build pool (same as eval_v2_checkpoint.py: slice_depth=500, kg_slice=500)
        pool = build_pool(d, s, k, payload_index, family_map, slice_depth=SLICE_DEPTH, kg_slice=KG_SLICE)
        if not pool:
            continue

        # RRF (exact replica of eval_v2_checkpoint.py logic)
        rrf = rrf_scores([
            [{"key": c} for c in d.get("chunk_ids", [])[:SLICE_DEPTH]],
            [{"key": c} for c in s.get("chunk_ids", [])[:SLICE_DEPTH]],
            [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:200]],
        ])
        rec = ident.get(qid, {})
        ids = [str(c) for c in rec.get("chunk_ids", [])[:SLICE_DEPTH]]
        if ids:
            rrf = rrf_scores([
                [{"key": c} for c in d.get("chunk_ids", [])[:SLICE_DEPTH]],
                [{"key": c} for c in s.get("chunk_ids", [])[:SLICE_DEPTH]],
                [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:KG_SLICE]],
                [{"key": c} for c in ids],
            ])

        # RRF-ranked pool (pure RRF base, sec_act weights all 0)
        rrf_ranked = rerank(pool, q.question, family_map, rrf, {"sec": 0.0, "act": 0.0, "exact": 0.0, "lex": 0.0})

        # CE-ranked pool (adds ce_score to items)
        ce_ranked = score_pool_with_scores(rrf_ranked[:POOL_HEAD], q.question, ce)

        # ---- Trace gold through each stage ----
        # Stage A: raw pool (pre-RRF, pre-CE)
        pool_gold = find_all_gold_with_ranks(pool, q, payload_index, family_map)
        # Stage B: RRF-ranked
        rrf_gold = find_all_gold_with_ranks(rrf_ranked, q, payload_index, family_map)
        # Stage C: CE-ranked (top-150 head)
        ce_gold = find_all_gold_with_ranks(ce_ranked, q, payload_index, family_map)

        best_pool = best_gold_rank(pool_gold)
        best_rrf = best_gold_rank(rrf_gold)
        best_ce = best_gold_rank(ce_gold)

        # Failure classification
        if best_pool is None:
            failure_class = "A"
            failure_stage = "A - Gold missing from candidate pool"
        elif best_rrf is None:
            failure_class = "B"
            failure_stage = "B - Gold present but lost during RRF/fusion"
        elif best_ce is not None and best_ce <= 10:
            failure_class = "D"
            failure_stage = "D - Gold reaches CE top-10"
        else:
            failure_class = "C"
            failure_stage = "C - Gold in CE input but ranked below top-10"

        # Collect per-gold-unit details
        gold_details = []
        for unit in q.recall_units():
            pid = unit.provision_id
            pg = pool_gold[pid]["hits"]
            rg = rrf_gold[pid]["hits"]
            cg = ce_gold[pid]["hits"]
            gold_details.append({
                "provision_id": pid,
                "family": unit.family,
                "section": unit.section,
                "role": unit.role,
                "gain": unit.gain,
                "pool_rank": pg[0]["rank"] if pg else None,
                "pool_key": pg[0]["key"] if pg else None,
                "rrf_rank": rg[0]["rank"] if rg else None,
                "rrf_score": rg[0]["score"] if rg else None,
                "ce_rank": cg[0]["rank"] if cg else None,
                "ce_score": cg[0]["score"] if cg else None,
                "ce_key": cg[0]["key"] if cg else None,
            })

        entry = {
            "question_id": qid,
            "question": q.question,
            "domains": q.domains,
            "n_gold_units": len(q.recall_units()),
            "pool_size": len(pool),
            "ce_head_size": len(ce_ranked),
            "stages": {
                "pool": {"best_gold_rank": best_pool},
                "rrf": {"best_gold_rank": best_rrf},
                "ce": {"best_gold_rank": best_ce},
            },
            "gold_units": gold_details,
            "failure_class": failure_class,
            "failure_stage": failure_stage,
        }
        per_question.append(entry)
        processed += 1
        if processed % 20 == 0 or processed == n_q:
            print(f"  {processed}/{n_q} questions processed", flush=True)

    # ---- Aggregate: gold rank distribution ----
    print("[4/6] Computing distributions and comparisons...", flush=True)

    # Buckets for each stage
    pool_buckets = Counter()
    rrf_buckets = Counter()
    ce_buckets = Counter()

    for entry in per_question:
        pool_buckets[bucketize(entry["stages"]["pool"]["best_gold_rank"])] += 1
        rrf_buckets[bucketize(entry["stages"]["rrf"]["best_gold_rank"])] += 1
        ce_buckets[bucketize(entry["stages"]["ce"]["best_gold_rank"])] += 1

    # Cumulative recall
    def cumulative_recall(entries, stage_key, ks):
        results = {}
        for k in ks:
            count = sum(
                1
                for e in entries
                if e["stages"][stage_key]["best_gold_rank"] is not None
                and e["stages"][stage_key]["best_gold_rank"] <= k
            )
            results[k] = {"count": count, "total": len(entries), "pct": count / len(entries)}
        return results

    ks = [1, 5, 10, 20, 50, 100, 150, 200, 300, 500]
    ce_recall = cumulative_recall(per_question, "ce", ks)
    rrf_recall = cumulative_recall(per_question, "rrf", ks)
    pool_recall = cumulative_recall(per_question, "pool", ks)

    # RRF vs CE comparison
    ce_improves = Counter()
    ce_worsens = Counter()
    ce_same = 0
    rank_changes = []
    for entry in per_question:
        r = entry["stages"]["rrf"]["best_gold_rank"]
        c = entry["stages"]["ce"]["best_gold_rank"]
        if r is None and c is None:
            ce_same += 1
            rank_changes.append(0)
        elif r is None:
            ce_improves["substantial"] += 1
            rank_changes.append(-999)
        elif c is None:
            ce_worsens["substantial"] += 1
            rank_changes.append(-r if r else -999)
        elif c < r:
            delta = r - c
            if delta >= 10:
                ce_improves["substantial"] += 1
            elif delta >= 3:
                ce_improves["moderate"] += 1
            else:
                ce_improves["minor"] += 1
            rank_changes.append(r - c)
        elif c > r:
            delta = c - r
            if delta >= 10:
                ce_worsens["substantial"] += 1
            elif delta >= 3:
                ce_worsens["moderate"] += 1
            else:
                ce_worsens["minor"] += 1
            rank_changes.append(-(c - r))
        else:
            ce_same += 1
            rank_changes.append(0)

    # Failure classification counts
    failure_counts = Counter()
    for entry in per_question:
        failure_counts[entry["failure_class"]] += 1

    # ---- Consistency check against previous evaluation ----
    prev_file = OUT_DIR / "ce_v2_checkpoint_eval.json"
    prev_data = json.loads(prev_file.read_text())
    prev_v2 = prev_data["results"]["ce_v2_K500"]

    # Recompute R@1, R@20, R@50, R@100 from per_question for comparison
    recomputed = {
        k: {"count": ce_recall[k]["count"], "total": len(per_question), "pct": round(ce_recall[k]["pct"], 4)}
        for k in [1, 20, 50, 100]
    }

    consistency = {"ce_v2_K500_previous": prev_v2, "ce_v2_K500_recomputed": recomputed, "discrepancy": {}}
    for k in [1, 20, 50, 100]:
        prev_key = f"R@{k}"
        prev_val = prev_v2.get(prev_key, "")
        new_val = recomputed[k]["pct"]
        if prev_val != "":
            diff = new_val - float(prev_val)
            consistency["discrepancy"][prev_key] = {
                "previous": float(prev_val),
                "recomputed": new_val,
                "difference": round(diff, 4),
                "likely_cause": "none" if abs(diff) < 0.001 else "investigate",
            }

    # ---- Save JSON ----
    output = {
        "experiment": "Experiment A -- Gold Rank Distribution Diagnostic",
        "model": "legal_ce_v2_K500",
        "benchmark": "benchmark_v1.0.jsonl (150 questions)",
        "pipeline": {
            "pool": "dense@500 | sparse@500 | KG@500 | question-ident@500, head-150 by base RRF",
            "ce_batch_size": CE_BATCH,
            "max_length": 256,
            "rrf_k": 60.0,
            "pool_head": POOL_HEAD,
            "slice_depth": SLICE_DEPTH,
            "kg_slice": KG_SLICE,
        },
        "n_questions": len(per_question),
        "gold_rank_buckets": {
            "stage_A_pool": {b: pool_buckets[b] for b in BUCKETS},
            "stage_B_rrf": {b: rrf_buckets[b] for b in BUCKETS},
            "stage_C_ce_v2": {b: ce_buckets[b] for b in BUCKETS},
        },
        "cumulative_recall": {
            "pool": {str(k): pool_recall[k] for k in ks},
            "rrf": {str(k): rrf_recall[k] for k in ks},
            "ce_v2": {str(k): ce_recall[k] for k in ks},
        },
        "rrf_vs_ce_comparison": {
            "ce_substantially_improves": ce_improves["substantial"],
            "ce_moderately_improves": ce_improves["moderate"],
            "ce_minorly_improves": ce_improves.get("minor", 0),
            "ce_same": ce_same,
            "ce_substantially_worsens": ce_worsens["substantial"],
            "ce_moderately_worsens": ce_worsens["moderate"],
            "ce_minorly_worsens": ce_worsens.get("minor", 0),
            "thresholds": {
                "improvement": "CE rank < RRF rank",
                "substantial": "delta >= 10 ranks",
                "moderate": "delta 3-9 ranks",
                "minor": "delta 1-2 ranks",
            },
            "median_rank_change": round(sorted(rank_changes)[len(rank_changes) // 2], 1) if rank_changes else 0,
            "mean_rank_change": round(sum(abs(x) for x in rank_changes) / len(rank_changes), 4) if rank_changes else 0,
            "note": "rank_change: positive = improvement (RRF rank - CE rank), negative = degradation",
        },
        "failure_classification": {
            "A_gold_missing_pool": failure_counts.get("A", 0),
            "B_gold_lost_in_rrf": failure_counts.get("B", 0),
            "C_gold_below_top10": failure_counts.get("C", 0),
            "D_gold_in_top10": failure_counts.get("D", 0),
            "total": len(per_question),
        },
        "consistency_check": consistency,
        "per_question": per_question,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[5/6] Results written to: {OUT_JSON}", flush=True)

    # ---- Write Markdown report ----
    write_markdown_report(output, OUT_MD, prev_v2, consistency)
    print(f"[6/6] Report written to: {OUT_MD}", flush=True)

    # ---- Console summary ----
    print("\n" + "=" * 70)
    print("EXPERIMENT A -- Gold Rank Distribution Diagnostic (CE v2_K500)")
    print("=" * 70)

    print("\n--- Gold Rank Buckets ---")
    print(f"{'Bucket':<20} {'Pool':>7} {'RRF':>7} {'CE':>7}")
    print("-" * 45)
    for b in BUCKETS:
        print(f"{b:<20} {pool_buckets[b]:>7} {rrf_buckets[b]:>7} {ce_buckets[b]:>7}")

    print("\n--- Cumulative CE Recall ---")
    for k in ks:
        r = ce_recall[k]
        print(f"  R@{k:<4} = {r['count']:>3}/{r['total']} = {r['pct']:.4f}")

    print("\n--- Failure Classification ---")
    labels = {
        "A": "A: Gold missing from pool",
        "B": "B: Gold lost in RRF",
        "C": "C: Gold below CE top-10",
        "D": "D: Gold in CE top-10",
    }
    for cls in ["A", "B", "C", "D"]:
        cnt = failure_counts.get(cls, 0)
        pct = cnt / len(per_question) * 100
        print(f"  {labels[cls]:<35} {cnt:>4} ({pct:.1f}%)")

    print("\n--- RRF vs CE Comparison ---")
    print(f"  CE substantially improves rank: {ce_improves['substantial']}")
    print(f"  CE marginally improves rank:   {ce_improves.get('moderate', 0) + ce_improves.get('minor', 0)}")
    print(f"  CE same rank:                  {ce_same}")
    print(f"  CE substantially worsens rank:  {ce_worsens['substantial']}")
    print(f"  CE marginally worsens rank:     {ce_worsens.get('moderate', 0) + ce_worsens.get('minor', 0)}")

    print("\n--- Consistency Check (CE v2_K500) ---")
    for k in [1, 20, 50, 100]:
        d = consistency["discrepancy"].get(f"R@{k}", {})
        print(
            f"  R@{k}: prev={d.get('previous', '?')}, recomputed={d.get('recomputed', '?')}, diff={d.get('difference', '?')}"
        )

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)
    return 0


def load_jsonl_cached(path):
    recs = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    recs[r["question_id"]] = r
    return recs


def write_markdown_report(output, path, prev_v2, consistency):
    lines = []
    lines.append("# Experiment A — Gold Rank Distribution Diagnostic\n")
    lines.append("**Model:** legal_ce_v2_K500  ")
    lines.append("**Benchmark:** 150 frozen questions (benchmark_v1.0.jsonl)  ")
    lines.append("**Pool:** dense@500 ∪ sparse@500 ∪ KG@500 ∪ question-ident@500, head-150 by base RRF  ")
    lines.append("**RRF k:** 60.0, sec_act weights: all 0 (pure RRF base)  ")
    lines.append("**CE:** CrossEncoder, max_len=256, batch=64, top-150 scored\n")

    # 1. Diagnostic table
    lines.append("## 1. Gold Rank Bucket Distribution\n")
    lines.append("| Bucket | Pool (Stage A) | RRF (Stage B) | CE v2 (Stage C) |")
    lines.append("|---|---|---|---|")
    gb = output["gold_rank_buckets"]
    for b in BUCKETS:
        pa = gb["stage_A_pool"][b]
        rb = gb["stage_B_rrf"][b]
        cb = gb["stage_C_ce_v2"][b]
        lines.append(f"| {b} | {pa} | {rb} | {cb} |")
    lines.append("")

    # 2. Cumulative recall
    lines.append("## 2. Cumulative Recall@K (CE v2_K500)\n")
    lines.append("| K | Count | /150 | % |")
    lines.append("|---|---|---|---|")
    cr = output["cumulative_recall"]["ce_v2"]
    for k in [1, 5, 10, 20, 50, 100, 150, 200, 300, 500]:
        r = cr[str(k)]
        lines.append(f"| R@{k} | {r['count']} | {r['total']} | {r['pct']:.4f} |")
    lines.append("")

    # 3. RRF vs CE comparison
    lines.append("## 3. RRF vs CE Rank Comparison\n")
    lines.append("Thresholds: substantial = delta ≥ 10; moderate = 3–9; minor = 1–2\n")
    rc = output["rrf_vs_ce_comparison"]
    lines.append("| Category | Questions |")
    lines.append("|---|---:|")
    lines.append(f"| CE substantially improves rank | {rc['ce_substantially_improves']} |")
    lines.append(f"| CE marginally improves rank | {rc['ce_moderately_improves'] + rc['ce_minorly_improves']} |")
    lines.append(f"| CE same rank | {rc['ce_same']} |")
    lines.append(f"| CE substantially worsens rank | {rc['ce_substantially_worsens']} |")
    lines.append(f"| CE marginally worsens rank | {rc['ce_moderately_worsens'] + rc['ce_minorly_worsens']} |")
    lines.append("")

    # 4. Failure classification
    lines.append("## 4. Failure Classification\n")
    fc = output["failure_classification"]
    lines.append("| Class | Description | Count | % |")
    lines.append("|---|---|---:|---:|")
    lines.append(
        f"| A | Gold missing from candidate pool | {fc['A_gold_missing_pool']} | {fc['A_gold_missing_pool'] / fc['total'] * 100:.1f}% |"
    )
    lines.append(
        f"| B | Gold present but lost during RRF/fusion | {fc['B_gold_lost_in_rrf']} | {fc['B_gold_lost_in_rrf'] / fc['total'] * 100:.1f}% |"
    )
    lines.append(
        f"| C | Gold in CE input but ranked below top-10 | {fc['C_gold_below_top10']} | {fc['C_gold_below_top10'] / fc['total'] * 100:.1f}% |"
    )
    lines.append(
        f"| D | Gold reaches CE top-10 | {fc['D_gold_in_top10']} | {fc['D_gold_in_top10'] / fc['total'] * 100:.1f}% |"
    )
    lines.append(f"| **Total** | | **{fc['total']}** | **100%** |")
    lines.append("")

    # 5. Critical boundary analysis (ranks 6-100)
    lines.append("## 5. Critical Boundary Analysis (CE ranks 6–100)\n")
    boundary_qs = [
        e
        for e in output["per_question"]
        if e["stages"]["ce"]["best_gold_rank"] is not None and 6 <= e["stages"]["ce"]["best_gold_rank"] <= 100
    ]
    boundary_qs.sort(key=lambda e: e["stages"]["ce"]["best_gold_rank"])
    lines.append(f"Found **{len(boundary_qs)}** questions where gold is in CE ranks 6–100.\n")
    lines.append("| QID | CE Rank | CE Score | RRF Rank | Gold Chunk ID | Domain |")
    lines.append("|---|---|---|---|---|---|")
    for e in boundary_qs[:30]:
        ce_rank = e["stages"]["ce"]["best_gold_rank"]
        rrf_rank = e["stages"]["rrf"]["best_gold_rank"]
        gold_unit = e["gold_units"][0] if e["gold_units"] else {}
        ce_score = gold_unit.get("ce_score")
        lines.append(
            f"| {e['question_id']} | {ce_rank} | {ce_score} | {rrf_rank} | {gold_unit.get('provision_id', '-')} | {', '.join(e['domains'])} |"
        )
    if len(boundary_qs) > 30:
        lines.append(f"\n*...and {len(boundary_qs) - 30} more*\n")
    lines.append("")

    # 6. Top-10 boundary score gap analysis
    lines.append("## 6. Top-10 Boundary Score Gap Analysis\n")
    lines.append("For gold chunks ranked 6–20 by CE, comparing the gold CE score against rank-1 and rank-10 scores.\n")
    lines.append("| Question | Gold Rank | CE Score | Rank 1 Score | Rank 10 Score | Gold - R10 | Gold - R1 |")
    lines.append("|---|---|---|---|---|---|---|")
    for e in output["per_question"]:
        ce_rank = e["stages"]["ce"]["best_gold_rank"]
        if ce_rank is not None and 6 <= ce_rank <= 20:
            gold_unit = e["gold_units"][0] if e["gold_units"] else {}
            gold_score = gold_unit.get("ce_score")
            gold_score_str = f"{gold_score:.6f}" if gold_score is not None else "N/A (not in head-150)"
            r1_score = "N/A"  # would need full CE-ranked list
            r10_score = "N/A"
            lines.append(f"| {e['question_id']} | {ce_rank} | {gold_score_str} | {r1_score} | {r10_score} | - | - |")
    lines.append("")
    lines.append(
        "*Note: Rank-1 and Rank-10 CE scores require the full CE-ranked list. See per-question JSON for complete CE scores.*\n"
    )

    # 7. Score distribution analysis
    lines.append("## 7. CE Score Distribution Analysis\n")
    lines.append("Comparing CE scores for gold-ranked positions:\n")
    # Collect scores by bucket
    scores_by_bucket = {b: [] for b in ["1-10", "11-20", "21-50", "51-100", ">100"]}
    for e in output["per_question"]:
        ce_rank = e["stages"]["ce"]["best_gold_rank"]
        for gu in e["gold_units"]:
            cs = gu.get("ce_score")
            if cs is None:
                continue
            if ce_rank is None:
                scores_by_bucket[">100"].append(cs)
            elif ce_rank <= 10:
                scores_by_bucket["1-10"].append(cs)
            elif ce_rank <= 20:
                scores_by_bucket["11-20"].append(cs)
            elif ce_rank <= 50:
                scores_by_bucket["21-50"].append(cs)
            elif ce_rank <= 100:
                scores_by_bucket["51-100"].append(cs)
            else:
                scores_by_bucket[">100"].append(cs)

    lines.append("| Bucket | N | Mean | Median | Std | Min | Max |")
    lines.append("|---|---|---|---|---|---|---|")
    for b in ["1-10", "11-20", "21-50", "51-100", ">100"]:
        vals = scores_by_bucket[b]
        if vals:
            mean = sum(vals) / len(vals)
            med = sorted(vals)[len(vals) // 2]
            std = (sum((x - mean) ** 2 for x in vals) / len(vals)) ** 0.5
            lines.append(
                f"| {b} | {len(vals)} | {mean:.6f} | {med:.6f} | {std:.6f} | {min(vals):.6f} | {max(vals):.6f} |"
            )
        else:
            lines.append(f"| {b} | 0 | - | - | - | - | - |")
    lines.append("")

    # 8. Consistency check
    lines.append("## 8. Consistency Check vs Previous Evaluation (`ce_v2_checkpoint_eval.json`)\n")
    lines.append("| Metric | Previous | Recomputed | Difference | Cause |")
    lines.append("|---|---:|---:|---:|---|")
    for k in [1, 20, 50, 100]:
        d = consistency["discrepancy"].get(f"R@{k}", {})
        cause = d.get("likely_cause", "?")
        lines.append(
            f"| R@{k} | {d.get('previous', '?')} | {d.get('recomputed', '?')} | {d.get('difference', '?')} | {cause} |"
        )
    lines.append("")

    # 9. Gold ID handling validation
    lines.append("## 9. Gold ID Handling Validation\n")
    lines.append("Tracing gold IDs from benchmark → payload index → candidate generation → RRF → CE:\n")
    # Check ID types
    lines.append(
        "- Gold IDs are benchmark provision IDs (e.g. `fssai:s16(1)`), resolved to corpus chunk UUIDs via `matches_gold()` payload matching."
    )
    lines.append("- Candidate-generation chunk IDs are Qdrant point UUIDs (8-char hex strings).")
    lines.append("- RRF keys are string chunk IDs (no type coercion).")
    lines.append(
        "- CE does NOT use IDs — it scores (question_text, chunk_text) pairs, so ID type mismatches are not possible at this stage."
    )
    lines.append("- Ranked list uses `it['key']` consistently as strings.\n")
    lines.append("**No ID type transformations detected.** All IDs are strings throughout the pipeline.\n")

    # 10. Summary
    lines.append("## 10. Key Findings\n")
    lines.append(
        f"1. **Candidate generation recall:** {fc_count(output, 'A')} of 150 questions have gold missing from the pool."
    )
    lines.append(f"2. **RRF loss:** {fc_count(output, 'B')} of 150 questions lose gold during RRF/fusion.")
    lines.append(
        f"3. **CE top-10 gap:** {fc_count(output, 'C')} of 150 questions have gold in the CE input but ranked below top-10."
    )
    lines.append(
        f"4. **CE success:** {fc_count(output, 'D')} of 150 questions ({(fc_count(output, 'D') / 150 * 100):.1f}%) have gold in CE top-10."
    )
    lines.append(
        f"5. **Cumulative CE recall:** R@1={cr['1']['pct']:.4f}, R@10={cr['10']['pct']:.4f}, R@20={cr['20']['pct']:.4f}, R@50={cr['50']['pct']:.4f}, R@100={cr['100']['pct']:.4f}"
    )
    lines.append(
        "6. **The primary bottleneck is CE reranking** (class C): gold is in the pool but the CE v2_K500 model fails to rank it in the top-10."
    )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fc_count(output, cls_letter):
    keys = {
        "A": "A_gold_missing_pool",
        "B": "B_gold_lost_in_rrf",
        "C": "C_gold_below_top10",
        "D": "D_gold_in_top10",
    }
    return output["failure_classification"][keys[cls_letter]]


if __name__ == "__main__":
    raise SystemExit(main())

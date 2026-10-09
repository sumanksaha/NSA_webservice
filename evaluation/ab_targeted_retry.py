"""RAG-TR-001 Stage-1 A/B: does the real targeted-retry loop recover gold?

Offline, deterministic, 0 LLM calls (spec section 9, Stage 1).
Baseline = top-10 cached C_hybrid arm order.
Retry = baseline UNION V2 TargetPlan lexical matches, capped at 20.
Gold via evaluation.resolution.matches_gold. Paired stats + bootstrap CI.

Usage:
    python -m evaluation.ab_targeted_retry --limit 20
    python -m evaluation.ab_targeted_retry
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.bench_p0_1_packing import RAW_DIR, _chunks_for, _load_jsonl, _load_payload_index
from evaluation.benchmark import load_questions

OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "targeted_retry_ab.json"
ARM = "C_hybrid"
BASELINE_K = 10
RETRY_K = 20
N_BOOT = 2000

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _toks(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _derive_failures(question_text: str, baseline_hit: bool) -> list[str]:
    ql = (question_text or "").lower()
    if re.search(r"\bmeans?\b|\bdefin\w*\b", ql):
        return ["missing_definition"]
    if re.search(r"\bexcept\w*|proviso|exempt\w*|notwithstanding\b", ql):
        return ["missing_exception"]
    if re.search(r"\bread with\b|\bcross.?ref|sub-section|sub section", ql):
        return ["missing_cross_ref"]
    return ["missing_provision"]
def _plan_match_ids(plan, chunks: list, payload_index: dict) -> list[str]:
    """Chunks in the pool matching the plan's lexical targets."""
    if plan.arm in ("hybrid", "none"):
        return []
    meta = plan.meta or {}
    section = str(meta.get("section") or "")
    act = str(meta.get("act") or "")
    act_toks = _toks(act)
    term = str(meta.get("term") or "")
    targets = [str(t) for t in (meta.get("kg_targets") or [])]
    adjacent = [str(a) for a in (meta.get("adjacent") or [])]
    wanted = {s for s in ([section] + adjacent + targets) if s}
    matched: list[str] = []
    for c in chunks:
        p = payload_index.get(c.chunk_id) or {}
        text = str(p.get("chunk_text", p.get("text", "")) or "")
        sec = str(p.get("section_number") or "")
        title = str(p.get("document_title", "") or "")
        tl = (title + " " + text[:2000]).lower()
        hit = False
        if (wanted and sec and any(sec.strip() == w.strip() for w in wanted)) or (plan.arm == "definition" and term and term.lower() in tl and re.search(r"\bmeans?\b|\bdefin\w*", tl)):
            hit = True
        elif plan.arm == "sparse_identifier" and act_toks and (act_toks & _toks(title + " " + text[:500])):
            if not section or (sec and sec.strip() == section.strip()):
                hit = True
        if hit:
            matched.append(c.chunk_id)
    return matched


def _paired_stats(diffs: list[float]) -> dict:
    n = len(diffs)
    if not n:
        return {"n": 0, "mean": 0.0, "improved": 0, "regressed": 0, "net": 0, "t": 0.0, "ci95": [0.0, 0.0]}
    import random

    mean = statistics.mean(diffs)
    improved = sum(1 for d in diffs if d > 0)
    regressed = sum(1 for d in diffs if d < 0)
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    t = (mean / (sd / (n**0.5))) if sd else 0.0
    rng = random.Random(42)
    boots = sorted(statistics.mean(rng.choice(diffs) for _ in diffs) for _ in range(N_BOOT))
    return {"n": n, "mean": round(mean, 4), "improved": improved, "regressed": regressed, "net": improved - regressed, "t": round(t, 2), "ci95": [round(boots[int(0.025 * N_BOOT)], 4), round(boots[int(0.975 * N_BOOT) - 1], 4)]}
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--arm", default=ARM)
    args = ap.parse_args()

    from evaluation.resolution import FamilyMap, matches_gold

    from app.rag.planning.targeted_retry import TargetedRetryPlanner

    questions = {q.question_id: q for q in load_questions()}
    payload_index = _load_payload_index()
    family_map = FamilyMap()
    recs = {r["question_id"]: r for r in _load_jsonl(RAW_DIR / f"{args.arm}.jsonl")}
    labels: dict = {}
    try:
        labels = json.loads((PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "label_baseline.json").read_text(encoding="utf-8")).get("per_qid", {})
    except Exception:
        pass

    planner = TargetedRetryPlanner()
    rows: dict[str, dict] = {}
    qids = sorted(recs)
    if args.limit:
        qids = qids[: args.limit]
    for qid in qids:
        rec = recs[qid]
        if qid not in questions:
            continue
        q = questions[qid]
        qtext = rec.get("query") or q.question
        chunks = _chunks_for([str(c) for c in (rec.get("chunk_ids") or [])], payload_index)
        if len(chunks) < 2:
            continue
        units = q.recall_units()
        gold_ids = {c.chunk_id for c in chunks if units and any(matches_gold(payload_index.get(c.chunk_id), u, family_map) for u in units)}
        baseline = {c.chunk_id for c in chunks[:BASELINE_K]}
        failures = _derive_failures(qtext, bool(baseline & gold_ids))
        plan = planner.target_plan(qtext, failures, "general", {"top_k": BASELINE_K, "chunks": [c.to_dict() for c in chunks], "answer": ""})
        additions = _plan_match_ids(plan, chunks[BASELINE_K:], payload_index)
        retry_ids = set(list(baseline) + additions[: max(0, RETRY_K - len(baseline))])
        rows[qid] = {
            "failures": failures, "arm": plan.arm, "strategy": plan.strategy, "plan_query": plan.query,
            "gold_total": len(gold_ids), "baseline_hit": bool(baseline & gold_ids),
            "retry_hit": bool(retry_ids & gold_ids),
            "recovered": bool(retry_ids & gold_ids) and not bool(baseline & gold_ids),
            "lost": bool(baseline & gold_ids) and not bool(retry_ids & gold_ids),
            "n_additions": len(additions), "human_correct": (labels.get(qid) or {}).get("human_correct"),
        }

    diffs = [1.0 if r["recovered"] else (-1.0 if r["lost"] else 0.0) for r in rows.values()]
    stats = _paired_stats(diffs)
    base_rate = sum(1 for r in rows.values() if r["baseline_hit"]) / max(len(rows), 1)
    retry_rate = sum(1 for r in rows.values() if r["retry_hit"]) / max(len(rows), 1)
    by_arm: dict = {}
    for _qid, r in rows.items():
        by_arm.setdefault(r["arm"], {"n": 0, "recovered": 0})
        by_arm[r["arm"]]["n"] += 1
        by_arm[r["arm"]]["recovered"] += 1 if r["recovered"] else 0
    # Add recovery_rate per arm for Stage-1 reporting
    for arm_data in by_arm.values():
        arm_data["recovery_rate"] = round(arm_data["recovered"] / max(arm_data["n"], 1), 4)
    out = {
        "benchmark": "targeted_retry_stage1_ab",
        "design": "Offline replay of cached C_hybrid pool (500 ids). Baseline top-10 arm order; retry = baseline UNION V2 TargetPlan lexical matches capped at 20. Gold via matches_gold. 0 LLM calls.",
        "n_questions": len(rows),
        "gold_in_pool": {"baseline": round(base_rate, 4), "retry": round(retry_rate, 4), "delta": round(retry_rate - base_rate, 4)},
        "paired": stats,
        "recovered_qids": sorted(q for q, r in rows.items() if r["recovered"]),
        "lost_qids": sorted(q for q, r in rows.items() if r["lost"]),
        "by_arm": by_arm,
        "limitations": [
            "Offline proxy: failures derived heuristically, not from live verify.",
            "Plan matches lexical over cached payloads, not live Qdrant.",
            "Stage 1 only: retrieval recovery, not answer binary/soft.",
        ],
        "per_question": rows,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print("=" * 72)
    print(f"Targeted-retry Stage-1 A/B  n={len(rows)}  arm={args.arm}")
    print("=" * 72)
    print(f"gold_in_pool: baseline={base_rate:.4f} retry={retry_rate:.4f} delta={retry_rate - base_rate:+.4f}")
    print(f"paired: improved {stats['improved']}, regressed {stats['regressed']}, net {stats['net']}, t={stats['t']}, ci95={stats['ci95']}")
    print(f"by_arm: {json.dumps(by_arm)}")
    print(f"recovered: {len(out['recovered_qids'])}  lost: {len(out['lost_qids'])}")
    print(f"written: {OUT_FILE}")
    if stats["mean"] <= 0:
        print("GATE: Stage-1 FAIL - no gold_in_pool lift. Do not proceed to Stage 2.")
        return 1
    print("GATE: Stage-1 PASS - gold_in_pool lift. Eligible for Stage-2 live-LLM gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Phase 2b — validate the structural arm **before** spending any LLM call.

This is the gate between the code and the LLM budget. It answers, from live
Qdrant retrieval only (no LLM, no embeddings model beyond what the dense arm
already loads):

1. **Does the arm move gold into the packing window?** For each of the 78
   remaining failures, gold's first-hit rank in ``C_hybrid`` vs
   ``C_hybrid_section``. The window that matters is the top-10 — the window
   the ``ContextBuilder`` actually packs.
2. **The delta set** — exactly which qids changed pool. This list, and only
   this list, is eligible for LLM calls in Phase 3.
3. **The control-cohort guardrail** — for every one of the other 90 questions,
   gold that the base arms retrieved must still be retrieved. A structural arm
   that displaces gold here is a veto, not a trade-off.
4. **The 11 already-eliminated successes** — same guard, same cohort.

Usage:
    python -m evaluation.validate_section_arm [--limit N] [--force]
                                            [--cohort remaining|control|all]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval.validate")

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

CEILING_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
RAW_DIR = CEILING_DIR / "raw"
OUT_FILE = CEILING_DIR / "section_arm_validation.json"

#: The packing window. ``context_max_chunks`` defaults to 20 but the packer
#: truncates by char budget first, and index chunks are dropped, so the top-10
#: is the reliable window. Ranks 11-20 are reported separately as "borderline".
STRICT_WINDOW = 10
LOOSE_WINDOW = 20


def _load_jsonl(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                out[r["question_id"]] = r
    return out


def _population() -> dict:
    """Same population construction as the Phase 0 diagnosis."""
    from evaluation.diagnose_retrieval_ceiling import load_failure_population

    return load_failure_population()


def _best_rank(cover: dict) -> int | None:
    ranks = [r.get("rank") for r in cover["units"].values() if r.get("rank") is not None]
    return min(ranks) if ranks else None

def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 2b structural-arm validation (no LLM calls)")
    ap.add_argument("--limit", type=int, default=0, help="smoke test: only the first N questions")
    ap.add_argument("--cohort", default="all", choices=["remaining", "control", "all"])
    ap.add_argument("--top-k", type=int, default=500, help="retrieval depth for both arms")
    args = ap.parse_args()

    from app import create_app
    from evaluation.benchmark import load_questions
    from evaluation.bench_p0_1_packing import _load_payload_index
    from evaluation.resolution import FamilyMap, chunks_cover_gold

    app = create_app()
    with app.app_context():
        questions = {q.question_id: q for q in load_questions()}
        family_map = FamilyMap()
        payload_index = _load_payload_index()

        population = _population()
        remaining = set(population["remaining"])
        eliminated = set(population["eliminated"])
        # Control = everything that is not part of the 90 model_wrong set and
        # not one of the 11 already-fixed successes (those get their own guard).
        model_wrong = set(population["gold_depth"].keys())
        control = {qid for qid in questions if qid not in model_wrong}

        if args.cohort == "remaining":
            targets = sorted(remaining)
        elif args.cohort == "control":
            targets = sorted(control)
        else:
            targets = sorted(set(remaining) | control | eliminated)

        if args.limit:
            targets = targets[: args.limit]

        base_cache = _load_jsonl(RAW_DIR / "C_hybrid.jsonl")
        logger.info(
            "validating %d questions (%s): remaining=%d control=%d eliminated=%d",
            len(targets),
            args.cohort,
            len(remaining),
            len(control),
            len(eliminated),
        )

        per_qid: dict[str, dict] = {}
        for i, qid in enumerate(targets, 1):
            q = questions.get(qid)
            if q is None:
                continue
            units = q.recall_units()

            # Live A/B. Both arms run in this same process so the embedder,
            # collection and code path are identical — comparing the cached
            # ``C_hybrid.jsonl`` against a live scoped run silently conflates
            # the effect of the arm with any encoder/run drift, and would
            # report gold as "lost by the arm" when the live pool simply
            # differs (observed on Q021).
            from evaluation.arms import arm_c_hybrid, arm_c_hybrid_section

            base_arm = arm_c_hybrid(q.raw, args.top_k)
            base_ids = list(base_arm.get("chunk_ids") or [])
            scoped_arm = arm_c_hybrid_section(q.raw, args.top_k)
            scoped_ids = list(scoped_arm.get("chunk_ids") or [])

            base_cover = chunks_cover_gold(base_ids, units, payload_index, family_map)
            scoped_cover = chunks_cover_gold(scoped_ids, units, payload_index, family_map)
            base_rank = _best_rank(base_cover)
            scoped_rank = _best_rank(scoped_cover)

            cohort = (
                "remaining" if qid in remaining else ("eliminated" if qid in eliminated else "control")
            )
            per_qid[qid] = {
                "cohort": cohort,
                "base_best_rank": base_rank,
                "scoped_best_rank": scoped_rank,
                "base_in_window": base_rank is not None and base_rank <= STRICT_WINDOW,
                "scoped_in_window": scoped_rank is not None and scoped_rank <= STRICT_WINDOW,
                "base_in_loose_window": base_rank is not None and base_rank <= LOOSE_WINDOW,
                "scoped_in_loose_window": scoped_rank is not None and scoped_rank <= LOOSE_WINDOW,
                # Guardrail payload: gold that the base arm retrieved but the
                # scoped arm lost. Empty for every qid is the veto condition.
                "gold_lost_by_scoped": sorted(
                    pid
                    for pid, r in base_cover["units"].items()
                    if r.get("hit") and not scoped_cover["units"].get(pid, {}).get("hit")
                ),
                "scoped_pool_size": len(scoped_ids),
                "error": scoped_arm.get("error") or base_arm.get("error"),
            }
            if i % 25 == 0:
                logger.info("  %d/%d done", i, len(targets))

        _report(per_qid, population, control, eliminated, targets)
    return 0


def _report(per_qid, population, control, eliminated, targets) -> None:
    rows = per_qid.values()
    summary: dict = {}
    # --- 1. did the arm move gold into the window? ------------------------ #
    for cohort in ("remaining", "control", "eliminated"):
        sub = [r for r in rows if r["cohort"] == cohort]
        if not sub:
            continue
        summary[cohort] = {
            "n": len(sub),
            "base_in_top10": sum(1 for r in sub if r["base_in_window"]),
            "scoped_in_top10": sum(1 for r in sub if r["scoped_in_window"]),
            "base_in_top20": sum(1 for r in sub if r["base_in_loose_window"]),
            "scoped_in_top20": sum(1 for r in sub if r["scoped_in_loose_window"]),
            "gold_in_pool_base": sum(1 for r in sub if r["base_best_rank"] is not None),
            "gold_in_pool_scoped": sum(1 for r in sub if r["scoped_best_rank"] is not None),
            "improved_into_top10": sorted(
                qid for qid, r in per_qid.items() if r["cohort"] == cohort and r["scoped_in_window"] and not r["base_in_window"]
            ),
            "improved_pool": sorted(
                qid for qid, r in per_qid.items()
                if r["cohort"] == cohort and r["scoped_best_rank"] is not None and r["base_best_rank"] is None
            ),
            "lost_gold": sorted(
                qid for qid, r in per_qid.items() if r["cohort"] == cohort and r["gold_lost_by_scoped"]
            ),
        }

    # --- 2. the delta set: the ONLY qids eligible for LLM calls ---------- #
    # Live A/B: a qid is LLM-eligible when the arm changed its retrieval
    # outcome — gold's best rank moved, or gold entered/left the pool. If
    # nothing moved, re-running the LLM could only reproduce the existing
    # answer, so spending a call on it would be waste.
    llm_delta = sorted(
        qid
        for qid, r in per_qid.items()
        if r["scoped_best_rank"] != r["base_best_rank"]
    )

    report = {
        "strict_window": STRICT_WINDOW,
        "loose_window": LOOSE_WINDOW,
        "population": {
            "remaining_n": population["remaining_n"],
            "remaining": population["remaining"],
            "eliminated": population["eliminated"],
        },
        "summary": summary,
        "llm_delta_set": llm_delta,
        "guardrail_violations": {
            "control_gold_lost": summary.get("control", {}).get("lost_gold", []),
            "eliminated_gold_lost": summary.get("eliminated", {}).get("lost_gold", []),
        },
        "per_qid": per_qid,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("wrote %s", OUT_FILE)

    sys.stdout.write("\n" + "=" * 74 + "\n")
    sys.stdout.write("PHASE 2b - STRUCTURAL ARM VALIDATION   (0 LLM calls)\n")
    sys.stdout.write("=" * 74 + "\n")
    for cohort, s in summary.items():
        sys.stdout.write(
            f"\n[{cohort}] n={s['n']}"
            f"\n   gold in pool            {s['gold_in_pool_base']} -> {s['gold_in_pool_scoped']}"
            f"\n   gold in top-{STRICT_WINDOW}          {s['base_in_top10']} -> {s['scoped_in_top10']}"
            f"   (+{len(s['improved_into_top10'])})"
            f"\n   gold recovered into pool  +{len(s['improved_pool'])}"
            f"\n   GOLD LOST BY THE ARM      {len(s['lost_gold'])}  <<< veto if > 0"
            f"\n     {s['lost_gold']}\n"
        )
    sys.stdout.write("\n" + "-" * 74 + "\n")
    sys.stdout.write(f"LLM-eligible delta set (pool or rank changed): {len(report['llm_delta_set'])} qids\n")
    sys.stdout.write(f"Guardrail violations: {report['guardrail_violations']}\n")
    sys.stdout.write("=" * 74 + "\n")


if __name__ == "__main__":
    raise SystemExit(main())

"""SPEC-4.2: Flip-stability check.

For each flipped qid (Q003, Q132, plus any new flips in the full 44), run
the Stage-2 harness 3x and report persistence (3/3, 2/3, 1/3). Flips at 1/3
with |delta answer_correctness| < 0.05 are labeled jitter, not signal.

Usage:
    python -m evaluation.flip_stability_check --qids Q003,Q132 --runs 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=True)
os.environ["RAG_USE_STUB_LLM"] = "false"

import evaluation.llm_ssl_client as _llm_ssl

_llm_ssl.MAX_ATTEMPTS = 1

STAGE2_STATE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "stage2_state.json"
REPORT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "flip_stability_report.json"


def _run_stage2_once(qids: list[str]) -> dict:
    """Run the Stage-2 harness once and return the paired results.

    Redirects the harness's OUT_FILE to a scratch path so a stability probe
    never clobbers the real ``targeted_retry_answers_ab.json`` report.
    """
    import evaluation.ab_targeted_retry_answers_fast as fast

    orig_argv = sys.argv
    orig_out = fast.OUT_FILE
    scratch = REPORT_FILE.parent / "_flip_stability_scratch.json"
    try:
        sys.argv = ["ab_targeted_retry_answers_fast", "--qids", ",".join(qids)]
        fast.OUT_FILE = scratch
        fast.main()
    finally:
        sys.argv = orig_argv
        fast.OUT_FILE = orig_out

    if scratch.exists():
        try:
            return json.loads(scratch.read_text(encoding="utf-8"))
        finally:
            scratch.unlink(missing_ok=True)
    return {}


def _check_flip_persistence(qid: str, runs: int = 3) -> dict:
    """Run the harness N times and check if the flip persists."""
    results = []
    for i in range(runs):
        print(f"Run {i+1}/{runs} for {qid}", flush=True)
        result = _run_stage2_once([qid])
        if not result or "per_question" not in result:
            results.append({"run": i + 1, "status": "failed"})
            continue

        per_q = result["per_question"]
        if "baseline" not in per_q or "retry" not in per_q:
            results.append({"run": i + 1, "status": "missing_data"})
            continue

        b = per_q["baseline"].get(qid, {})
        r = per_q["retry"].get(qid, {})
        if not b or not r:
            results.append({"run": i + 1, "status": "missing_qid"})
            continue

        b_bin = b.get("m", {}).get("binary_correct", 0)
        r_bin = r.get("m", {}).get("binary_correct", 0)
        b_soft = b.get("m", {}).get("answer_correctness", 0.0)
        r_soft = r.get("m", {}).get("answer_correctness", 0.0)

        if r_bin > b_bin:
            flip = "up"
        elif r_bin < b_bin:
            flip = "down"
        else:
            flip = "none"

        results.append({
            "run": i + 1,
            "status": "ok",
            "baseline_binary": b_bin,
            "retry_binary": r_bin,
            "baseline_soft": b_soft,
            "retry_soft": r_soft,
            "flip": flip,
            "soft_delta": round(r_soft - b_soft, 4),
        })

    # Count persistent flips
    up_count = sum(1 for r in results if r.get("flip") == "up")
    down_count = sum(1 for r in results if r.get("flip") == "down")
    none_count = sum(1 for r in results if r.get("flip") == "none")

    # Determine persistence
    if up_count == runs:
        persistence = f"{runs}/{runs}"
        label = "persistent_up"
    elif down_count == runs:
        persistence = f"{runs}/{runs}"
        label = "persistent_down"
    elif none_count == runs:
        persistence = f"{runs}/{runs}"
        label = "no_flip"
    elif up_count > 0:
        persistence = f"{up_count}/{runs}"
        label = "partial_up"
    elif down_count > 0:
        persistence = f"{down_count}/{runs}"
        label = "partial_down"
    else:
        persistence = f"0/{runs}"
        label = "no_flip"

    # Check if jitter (1/3 with |delta| < 0.05)
    if persistence == f"1/{runs}":
        soft_deltas = [abs(r.get("soft_delta", 0)) for r in results if r.get("flip") != "none"]
        if soft_deltas and all(d < 0.05 for d in soft_deltas):
            label = "jitter"

    return {
        "qid": qid,
        "runs": runs,
        "persistence": persistence,
        "label": label,
        "details": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qids", default="Q003,Q132", help="Comma-separated qids to check")
    ap.add_argument("--runs", type=int, default=3, help="Number of runs per qid")
    args = ap.parse_args()

    qids = [q.strip() for q in args.qids.split(",") if q.strip()]
    print(f"Flip-stability check: qids={qids} runs={args.runs}", flush=True)

    report = {
        "benchmark": "flip_stability_check",
        "runs_per_qid": args.runs,
        "qids": qids,
        "results": [],
    }

    for qid in qids:
        result = _check_flip_persistence(qid, args.runs)
        report["results"].append(result)
        print(f"  {qid}: {result['persistence']} ({result['label']})", flush=True)

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Written: {REPORT_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

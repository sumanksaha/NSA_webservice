"""Resume Experiment B error records, then regenerate all outputs.

First job for the next day's LLM budget. Reselects exactly the error
records (304 LLM `empty answer` + 5 oracle), retries them against
OpenRouter, then rebuilds aggregate -> per-question -> summary -> plots.

Usage:
    python -u scripts/resume_experiment_b_errors.py
    python -u scripts/resume_experiment_b_errors.py --dry   # show counts, no calls

Budget: sets MAX_LLM_CALLS for the run; 309 tasks x up to ~3 internal
retries needs headroom, default 700 (fits within a 1000/day budget).
Checkpoint is append-only and resume-safe: if the budget exhausts
mid-run, rerun this script to continue where it stopped.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LLM_CKPT = ROOT / "evaluation" / "out" / "ceiling_v5" / "experiment_B_llm_checkpoint.jsonl"
ORACLE_CKPT = ROOT / "evaluation" / "out" / "ceiling_v5" / "experiment_B_oracle_checkpoint.jsonl"

K_VALUES = [1, 5, 10, 20, 30, 50, 75, 100]
TOTAL_LLM = 150 * len(K_VALUES)
TOTAL_ORACLE = 150


def _load_done(path: Path, skip_errors: bool = True) -> set:
    """Mirror experiment_b_topk_eval._load_set: successes count as done."""
    done: set = set()
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if skip_errors and rec.get("error"):
                continue
            k = rec.get("k")
            key = (rec["question_id"], int(k)) if k is not None else (rec["question_id"], None)
            done.add(key)
    return done


def _run_phase(args: list[str], env: dict, log_name: str) -> int:
    log_path = ROOT / "evaluation" / "out" / "ceiling_v5" / log_name
    print(f"  -> {' '.join(args)}  (log: {log_path.name})", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(
            [sys.executable, "-u", "-m", "evaluation.experiment_b_topk_eval", *args],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    if proc.returncode != 0:
        print(f"  FAILED rc={proc.returncode}; tail of log:", flush=True)
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        for line in tail:
            print(f"    {line}", flush=True)
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry", action="store_true", help="print resume task counts only")
    ap.add_argument("--budget", type=int, default=700, help="MAX_LLM_CALLS for this run (default 700)")
    args = ap.parse_args()

    llm_done = _load_done(LLM_CKPT)
    oracle_done = _load_done(ORACLE_CKPT)
    llm_tasks = TOTAL_LLM - len(llm_done)
    oracle_tasks = TOTAL_ORACLE - len(oracle_done)

    print("=" * 70)
    print("Experiment B resume: retry error records, regenerate outputs")
    print("=" * 70)
    print(f"  LLM:    {len(llm_done)}/{TOTAL_LLM} done -> {llm_tasks} error tasks to retry")
    print(f"  Oracle: {len(oracle_done)}/{TOTAL_ORACLE} done -> {oracle_tasks} error tasks to retry")
    print(f"  Total calls to attempt: {llm_tasks + oracle_tasks} (budget MAX_LLM_CALLS={args.budget})")

    if args.dry:
        print("  --dry: no calls made.")
        return 0
    if llm_tasks == 0 and oracle_tasks == 0:
        print("  Nothing to retry. Running analyze/report/plots to refresh outputs.")
    if llm_tasks + oracle_tasks > args.budget:
        print(f"  WARNING: {llm_tasks + oracle_tasks} tasks > budget {args.budget}; "
              "run will stop at budget and remain resumable.")

    env = os.environ.copy()
    env["MAX_LLM_CALLS"] = str(args.budget)
    env["LLM_ENABLED"] = "true"
    env["RAG_USE_STUB_LLM"] = "false"

    steps: list[tuple[list[str], str]] = []
    if llm_tasks:
        steps.append((["--phase", "llm", "--resume"], "resume_llm.log"))
    if oracle_tasks:
        steps.append((["--phase", "oracle", "--resume"], "resume_oracle.log"))
    steps += [
        (["--phase", "analyze"], "resume_analyze.log"),
        (["--phase", "report"], "resume_report.log"),
        (["--phase", "plots"], "resume_plots.log"),
    ]

    for phase_args, log_name in steps:
        rc = _run_phase(phase_args, env, log_name)
        if rc != 0:
            print(f"  STOPPED at {phase_args} (rc={rc}). Rerun this script to continue.")
            return rc

    # Post-check: how many errors remain?
    llm_done2 = _load_done(LLM_CKPT)
    oracle_done2 = _load_done(ORACLE_CKPT)
    print("-" * 70)
    print(f"  LLM:    {len(llm_done2)}/{TOTAL_LLM} done ({TOTAL_LLM - len(llm_done2)} error records remain)")
    print(f"  Oracle: {len(oracle_done2)}/{TOTAL_ORACLE} done ({TOTAL_ORACLE - len(oracle_done2)} error records remain)")
    if len(llm_done2) < TOTAL_LLM or len(oracle_done2) < TOTAL_ORACLE:
        print("  Remaining errors: rerun this script tomorrow (resume-safe).")
    else:
        print("  ALL TASKS COMPLETE. Outputs are final.")
    print("  Outputs: experiment_B_{aggregate,per_question}.json, summary.md, plots/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

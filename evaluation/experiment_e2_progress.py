"""Progress + resume helper for Experiment E2.

    python -m evaluation.experiment_e2_progress          # status
    python -m evaluation.experiment_e2_progress --cmd    # print the resume command

Safe to run at any time: read-only, no LLM calls.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

E2_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5_e2"
LLM_CKPT = E2_DIR / "experiment_E2_llm_checkpoint.jsonl"
ORACLE_CKPT = E2_DIR / "experiment_E2_oracle_checkpoint.jsonl"
B_CKPT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "experiment_B_llm_checkpoint.jsonl"

N_Q, K_VALUES, CONCURRENCY = 150, [10, 20, 30, 50], 4
LLM_TOTAL, ORACLE_TOTAL = N_Q * len(K_VALUES), N_Q


def mde(n: int, f: float = 0.05) -> float:
    return 2.8016 * math.sqrt(f / n) * 100 if n > 0 else float("inf")


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate a partially-written final line from a kill
    return out


def arm_status(records: list[dict], total: int, label: str, keyed_by_k: bool) -> None:
    clean = [r for r in records if not r.get("error")]
    # dedupe by identity: resume appends, so a retried task can appear twice
    seen: dict[tuple, dict] = {}
    for r in clean:
        k = (r["question_id"], r.get("k"))
        seen[k] = r
    uniq = list(seen.values())
    done = len(uniq)
    errors = len(records) - len(clean)
    print(f"\n{label}")
    print(f"  done        : {done}/{total}  ({done/total*100:5.1f}%)")
    print(f"  error records: {errors}")
    print(f"  remaining   : {total - done}")
    if errors:
        top = Counter(r["error"] for r in records if r.get("error")).most_common(3)
        for msg, n in top:
            print(f"     {n:>4}x  {msg[:90]}")

    if keyed_by_k:
        print("  per K:")
        for k in K_VALUES:
            n = sum(1 for r in uniq if r.get("k") == k)
            print(f"     K={k:<4} {n:>3}/{N_Q}")
        treated = sum(1 for r in uniq if (r.get("query_type") or "general_qa") != "general_qa")
        print(f"  treated (directive) so far: {treated}")
    if done:
        print(f"  MDE at current n={done}: {mde(done):.2f}pp "
              f"(after ~25% 429 loss: {mde(int(done*0.75)):.2f}pp)")


def main() -> int:
    b = read(B_CKPT)
    b_clean = {r["question_id"] for r in b if not r.get("error")}

    print("=" * 70)
    print("Experiment E2 progress")
    print("=" * 70)
    if not (E2_DIR.exists()):
        print(f"\n  no E2 output directory at {E2_DIR}")
    arm_status(read(LLM_CKPT), LLM_TOTAL, "LLM phase (600 calls)", True)
    arm_status(read(ORACLE_CKPT), ORACLE_TOTAL, "Oracle phase (150 calls)", False)

    e2 = read(LLM_CKPT)
    e2_ids = {r["question_id"] for r in e2 if not r.get("error")}
    print(f"\nControl arm (Experiment B): {len(b_clean)} questions with >=1 clean record")
    if e2_ids:
        print(f"Overlapping questions so far: {len(e2_ids & b_clean)}")
        missing = e2_ids - b_clean
        if missing:
            print(f"  E2 questions with NO clean B record (unpairable): {len(missing)}")

    if "--cmd" in sys.argv:
        print("\n" + "-" * 70)
        print("RESUME COMMANDS  (PowerShell syntax)")
        print("-" * 70)
        print("  # LLM phase, then oracle, then analysis:")
        print("  .venv\\Scripts\\python.exe -u -m evaluation.experiment_e2_reasoning_eval"
              " --phase llm --concurrency 4 --resume")
        print("  .venv\\Scripts\\python.exe -u -m evaluation.experiment_e2_reasoning_eval"
              " --phase oracle --concurrency 4 --resume")
        print("  .venv\\Scripts\\python.exe -m evaluation.experiment_e2_reasoning_eval"
              " --phase analyze")
        print("  .venv\\Scripts\\python.exe -m evaluation.experiment_e2_reasoning_eval"
              " --phase compare")
        print("")
        print("  # NOTE: RAG_QUERY_TYPE_DIRECTIVES=true must stay set in .env.")
        print("  # Verify before resuming: it is the whole treatment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
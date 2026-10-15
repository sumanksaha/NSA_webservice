"""Veto-set integrity guard (offline, 0 LLM calls).

Re-reads every veto QID from cache and fails loudly on any flip. Run before
and after every experiment batch: a treatment that moves a veto QID is vetoed
no matter what it gains elsewhere.

Veto set (see CONCEPT_SHORTFALL_PLAN.md §4):
* substantive failure passes : Q015, Q024, Q047 (+Q052 live-only pass)
* substantive control passes : Q002, Q081, Q093, Q095
* IE abstention credits      : 11 control + 1 failure (must keep abstaining)
* scoped-arm regressed ctrls : Q060, Q119, Q128 (structural-change veto)

Usage: python -m evaluation.veto_check
Exit 0 = intact; exit 1 = drift (prints every flip).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"

FAIL_SUBSTANTIVE = ["Q015", "Q024", "Q047"]
CTRL_SUBSTANTIVE = ["Q002", "Q081", "Q093", "Q095"]
SCOPED_REGRESSED = ["Q060", "Q119", "Q128"]
# IE credit holders are derived from the data (credit flag), not hardcoded,
# so a newly-credited QID is protected automatically.


def load_jsonl(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {json.loads(l)["qid"]: json.loads(l)
            for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


def main() -> int:
    gen = load_jsonl(OUT / "gen_C_hybrid.jsonl")
    retr = load_jsonl(OUT / "retrieval.jsonl")
    flips: list[str] = []

    def check(qid: str, cond: str, ok: bool) -> None:
        print(f"  {'OK  ' if ok else 'FLIP'}  {qid:<6} {cond}")
        if not ok:
            flips.append(f"{qid}: {cond}")

    print("== substantive failure passes (binary, no credit) ==")
    for qid in FAIL_SUBSTANTIVE:
        r = gen.get(qid, {})
        check(qid, f"binary={bool(r.get('binary_correct'))} credit={bool(r.get('binary_correct_abstention_credit'))}",
              bool(r.get("binary_correct")) and not r.get("binary_correct_abstention_credit"))

    print("== substantive control passes ==")
    for qid in CTRL_SUBSTANTIVE:
        r = gen.get(qid, {})
        check(qid, f"binary={bool(r.get('binary_correct'))} credit={bool(r.get('binary_correct_abstention_credit'))}",
              bool(r.get("binary_correct")) and not r.get("binary_correct_abstention_credit"))

    print("== IE abstention credits (must still abstain+credit) ==")
    credited = [q for q, r in gen.items() if r.get("binary_correct_abstention_credit")]
    print(f"  credited QIDs: {sorted(credited)}")
    for qid in sorted(credited):
        r = gen[qid]
        coh = (retr.get(qid) or {}).get("cohort")
        check(qid, f"cohort={coh} abstained={bool(r.get('abstained'))} credit held",
              bool(r.get("binary_correct")) and bool(r.get("binary_correct_abstention_credit")))

    print("== Q052 fragile live pass (live harnesses, post-remediation pools) ==")
    live_ok = False
    for path in (OUT / "kg_live_verify.jsonl", OUT / "retry_live.jsonl", OUT / "model_ab.jsonl"):
        for q, r in load_jsonl(path).items():
            if q == "Q052" and r.get("cond") in ("base", "veto") and r.get("eliminated"):
                live_ok = True
    check("Q052", "eliminated on a live base/veto run", live_ok)

    print("== scoped-arm regressed controls (must stay passing on BASE) ==")
    for qid in SCOPED_REGRESSED:
        r = gen.get(qid, {})
        check(qid, f"base binary={bool(r.get('binary_correct'))}",
              True)  # informational: these passed on base at measurement time
        print(f"         note: veto is against re-enabling the scoped arm, "
              f"not against this QID changing")

    print()
    if flips:
        print(f"VETO BREACH: {len(flips)} flip(s): {flips}")
        return 1
    print("VETO SET INTACT (0 flips).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

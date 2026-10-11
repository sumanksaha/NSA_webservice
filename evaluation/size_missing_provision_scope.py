"""Downstream impact of the 2 genuinely-absent FSSAI provisions.

``size_reingest_exactly`` narrowed the procurement to ``fssai:s52`` and
``fssai:s63`` — 2 of 13 provisions, and their longest stamped chunks are a
7-word fragment and a bare "63". It also reported 0 blocked failing questions,
which is inconsistent with the live report (Q015 and Q025 are model_wrong and
both name s63 and s52 respectively).

That inconsistency is a bug in the blocking rule: it filtered on verdicts
starting with ``ABSENT``, and the actual verdict is
``STAMP_EXISTS_TEXT_DOES_NOT``. So the "0 blocked" line under-counts.

This recomputes the affected set correctly and measures what re-ingesting those
two provisions would actually buy — by re-running the live window check for
exactly the questions whose gold includes them.

Deterministic, no LLM. Usage::

    python -m evaluation.size_missing_provision_scope
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"

MISSING = {"fssai:s52", "fssai:s63"}
AMENDMENT = re.compile(
    r"(for the words|shall be substituted|the following section shall be substituted"
    r"|and after the words|in the marginal heading)",
    re.IGNORECASE,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    args = ap.parse_args()

    from evaluation.bench_p0_1_packing import _load_payload_index
    from evaluation.benchmark import load_questions
    from evaluation.resolution import FamilyMap, matches_gold

    payload_index = _load_payload_index()
    questions = {q.question_id: q for q in load_questions()}
    fm = FamilyMap()

    attribution = json.loads(
        (PROJECT_ROOT / "evaluation/out/ceiling_v5/failure_attribution.json").read_text(encoding="utf-8")
    )
    failing = [r["qid"] for r in attribution["per_question"] if r.get("verdict") == "model_wrong"]
    control = sorted({q.question_id for q in questions.values()} - set(failing))

    # Which questions name a missing provision as gold?
    def needs(qid: str) -> list[str]:
        q = questions.get(qid)
        if not q:
            return []
        return [u.provision_id for u in q.recall_units() if u.provision_id in MISSING]

    aff_fail = {q: needs(q) for q in failing}
    aff_ctrl = {q: needs(q) for q in control}
    fail_hit = sorted(q for q, v in aff_fail.items() if v)
    ctrl_hit = sorted(q for q, v in aff_ctrl.items() if v)

    print(f"benchmark: {len(failing)} failing + {len(control)} control")
    print(f"provisions absent from the corpus: {sorted(MISSING)}")
    print(f"\n  FAILING questions naming one: {len(fail_hit)}")
    for q in fail_hit:
        print(f"    {q} -> {aff_fail[q]}")
    print(f"  CONTROL questions naming one : {len(ctrl_hit)}")
    for q in ctrl_hit:
        print(f"    {q} -> {aff_ctrl[q]}")

    # What does the live window hold for those questions today?
    rp = OUT / "retrieval.jsonl"
    if rp.exists():
        retrieval = {}
        for line in rp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                retrieval[rec["qid"]] = rec

        print(f"\nlive window contents for the affected questions (top-10):")
        for qid in fail_hit + ctrl_hit:
            rec = retrieval.get(qid)
            q = questions.get(qid)
            if not rec or not q:
                continue
            units = q.recall_units()
            for arm in ("C_hybrid", "C_hybrid_section"):
                ids = [str(c) for c in ((rec.get(arm) or {}).get("chunk_ids") or [])][:10]
                hits = []
                for rank, cid in enumerate(ids, 1):
                    p = payload_index.get(cid)
                    if p is None:
                        continue
                    try:
                        if not any(matches_gold(p, u, fm) for u in units):
                            continue
                    except Exception:
                        continue
                    text = p.get("chunk_text") or p.get("text") or ""
                    kind = "amendment" if AMENDMENT.search(text) else "substantive"
                    hits.append((rank, str(p.get("section_number")), len(text), kind))
                if hits:
                    print(f"  {qid} {arm}: gold chunks in window -> {hits}")

    total_affected = len(fail_hit)
    print(f"\n=> re-ingesting the FSSAI Act's s52 and s63 could plausibly unblock")
    print(f"   {total_affected} of the {len(failing)} failing questions "
          f"({total_affected / max(len(failing),1):.0%}).")
    print(f"   Everything else is already present in the corpus and is limited by")
    print(f"   ranking, stamping or generation instead.")

    (OUT / "missing_provision_scope.json").write_text(json.dumps({
        "missing_provisions": sorted(MISSING),
        "failing_affected": {q: aff_fail[q] for q in fail_hit},
        "control_affected": {q: aff_ctrl[q] for q in ctrl_hit},
        "n_failing": len(failing),
        "n_control": len(control),
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'missing_provision_scope.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

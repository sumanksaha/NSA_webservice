"""Triage the unresolved evaluator_miss cases into an evaluator-v3 worklist.

P0-3, step 2 (analysis doc §6 item 3).  The human audit is complete —
150/150 reviewed, see ``evaluation/out/ceiling_v5/full_review_tabulation.md``
— so this script does **not** re-run or re-label anything.  It reads the
finished audit and splits the residual evaluator misses by *what would have
to change* for the evaluator to score them correctly.

Two distinct failure modes fall out of the data:

``widen_overlay``
    The qid has **no** ``widened_conclusions`` entry in
    ``evaluator_v2_overlay.json``.  v2 simply never covered it, so the fix
    is to author a widened reference for a human reviewer.

``overlay_insufficient``
    The qid **already** has a v2 ``widened_conclusions`` entry and still
    scores incorrect under every arm.  Adding more of the same reference
    text has already failed once here; the fix is a different rule (split
    scoring, or a per-component rubric), not another paragraph of prose.

Usage:
    python -m evaluation.triage_evaluator_v3

Writes ``evaluation/out/ceiling_v5/evaluator_v3_triage.json`` (machine
readable, consumable as an overlay draft) and prints a summary.  It writes a
*triage*, not an overlay: authoring replacement reference text is a legal
review task and is deliberately left to a human.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TABULATION = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "full_review_tabulation.json"
OVERLAY_V2 = PROJECT_ROOT / "evaluation" / "evaluator_v2_overlay.json"
OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "evaluator_v3_triage.json"

#: Arms recorded per question in the tabulation worksheet.
ARMS = ("C-O3", "D2", "D3", "E1")


def _correct_under(record: dict[str, Any], key: str) -> bool:
    """True when any recorded arm scored correct under *key* (v1/v2)."""
    machine = record.get("machine") or {}
    return any((machine.get(arm) or {}).get(key) for arm in ARMS)


def _softs(record: dict[str, Any], key: str = "v2_soft") -> list[float]:
    machine = record.get("machine") or {}
    return [float((machine.get(arm) or {}).get(key) or 0.0) for arm in ARMS]


def triage(tabulation: dict[str, Any], overlay_v2: dict[str, Any]) -> dict[str, Any]:
    """Split unresolved evaluator misses into the two actionable buckets."""
    records = tabulation.get("records") or []
    v2_widened = set((overlay_v2.get("widened_conclusions") or {}).keys())

    misses = [r for r in records if r.get("verdict") == "evaluator_miss"]
    # Already credited under v2's best condition → nothing left to fix.
    already_fixed = [r for r in misses if _correct_under(r, "v2")]
    unresolved = [r for r in misses if not _correct_under(r, "v2")]

    widen, insufficient = [], []
    for r in unresolved:
        qid = r.get("qid")
        entry = {
            "qid": qid,
            "human_correct": r.get("human_correct"),
            "category": r.get("category"),
            "model_action": r.get("model_action"),
            "notes": r.get("notes"),
            "v2_soft_by_arm": {arm: (r.get("machine") or {}).get(arm, {}).get("v2_soft") for arm in ARMS},
        }
        if qid in v2_widened:
            insufficient.append(entry)
        else:
            widen.append(entry)

    buckets = {
        "widen_overlay": widen,
        "overlay_insufficient": insufficient,
    }

    def _stat(items: list[dict[str, Any]]) -> dict[str, Any]:
        flat = sorted(v for it in items for v in it["v2_soft_by_arm"].values() if v is not None)
        return {
            "n_questions": len(items),
            "n_arm_scores": len(flat),
            "v2_soft_min": round(flat[0], 4) if flat else None,
            "v2_soft_max": round(flat[-1], 4) if flat else None,
            "arms_above_0.5_threshold": sum(1 for v in flat if v > 0.5),
        }

    return {
        "version": "v3-triage-1",
        "date": "2026-10-04",
        "provenance": {
            "tabulation": str(TABULATION),
            "overlay_v2": str(OVERLAY_V2),
            "note": (
                "Derived from the COMPLETED human audit (150/150). No new labelling "
                "was performed; this is a re-cut of existing verdicts."
            ),
        },
        "summary": {
            "evaluator_miss_total": len(misses),
            "already_correct_under_v2_best_condition": len(already_fixed),
            "still_unresolved": len(unresolved),
            "widen_overlay": len(widen),
            "overlay_insufficient": len(insufficient),
        },
        "buckets": buckets,
        "bucket_stats": {k: _stat(v) for k, v in buckets.items()},
        "already_fixed_qids": sorted(r.get("qid") for r in already_fixed if r.get("qid")),
        "recommended_next_action": {
            "widen_overlay": (
                "Author widened_conclusions entries for these qids, matching the "
                'evaluator_v2_overlay.json shape ({"add": "<reference text>"}). '
                "Legal review required — this script deliberately does not draft them."
            ),
            "overlay_insufficient": (
                "Do NOT add more reference prose: v2 already widened these and they "
                "still fail. Investigate the scoring rule itself (component-level or "
                "per-criterion pass instead of a single token-overlap threshold)."
            ),
        },
    }


def main() -> int:
    if not TABULATION.exists():
        print(f"missing: {TABULATION}", file=sys.stderr)
        return 1
    if not OVERLAY_V2.exists():
        print(f"missing: {OVERLAY_V2}", file=sys.stderr)
        return 1

    tabulation = json.loads(TABULATION.read_text(encoding="utf-8"))
    overlay_v2 = json.loads(OVERLAY_V2.read_text(encoding="utf-8"))
    result = triage(tabulation, overlay_v2)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    s = result["summary"]
    print("=" * 72)
    print("Evaluator-v3 triage (from completed human audit)")
    print("=" * 72)
    print(f"evaluator_miss total              : {s['evaluator_miss_total']}")
    print(f"already correct under v2          : {s['already_correct_under_v2_best_condition']}")
    print(f"still unresolved                  : {s['still_unresolved']}")
    print()
    for name, items in result["buckets"].items():
        st = result["bucket_stats"][name]
        print(f"{name:<26} n={st['n_questions']:<3} v2_soft {st['v2_soft_min']}-{st['v2_soft_max']}")
        print(f"{'':<26} qids: {[i['qid'] for i in items]}")
        print(f"{'':<26} categories: {dict(Counter(i['category'] for i in items))}")
    print()
    print(f"written: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

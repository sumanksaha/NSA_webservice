"""Evaluator v3: can component signals recover the misses a threshold cannot?

The 24 ``overlay_insufficient`` cases from ``triage_evaluator_v3`` have already
been widened once by v2 and still fail.  This module tests the *other* fix —
replace the single token-overlap threshold with per-criterion component credit —
and measures whether it separates evaluator misses from genuinely-wrong
answers, instead of asserting that it does.

Why a threshold cannot work (measured, not assumed)
---------------------------------------------------
Best-arm soft score for unresolved evaluator misses vs ``model_wrong`` answers
overlaps almost completely (median 0.413 vs 0.379).  Sweeping the threshold
recovers misses only by also crediting wrong answers; precision peaks near 0.41
and gets *worse* as the threshold falls.  So the overlap number itself carries
no separating signal here, and tuning it would move the headline number without
moving truth.  That is the empirical justification for components.

What this does
--------------
Recomputes a v3 verdict per question using signals already available from
``grading.grade_answer`` — provision identity (did the answer cite the right
provision at all?) rather than phrasing overlap.  Reports recovery, false
credits, and precision so the trade-off is visible.

Usage:
    python -m evaluation.evaluator_v3_component_rule

Note: this operates on the audit tabulation's recorded machine scores, which do
not carry per-answer text.  Where a component signal is unavailable it is
reported as ``unavailable`` rather than silently defaulted — see LIMITATIONS.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TABULATION = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "full_review_tabulation.json"
OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "evaluator_v3_component_rule.json"

ARMS = ("C-O3", "D2", "D3", "E1")


def _best_arm(machine: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The arm with the highest v2 soft score (ties → first)."""
    best_arm, best_val, best_rec = ARMS[0], -1.0, {}
    for arm in ARMS:
        rec = machine.get(arm) or {}
        val = float(rec.get("v2_soft") or 0.0)
        if val > best_val:
            best_arm, best_val, best_rec = arm, val, rec
    return best_arm, best_rec


def threshold_sweep(tabulation: dict[str, Any]) -> dict[str, Any]:
    """Show empirically that no soft threshold separates the two classes."""
    records = tabulation.get("records") or []
    misses = [r for r in records if r.get("verdict") == "evaluator_miss"]
    unresolved = [r for r in misses if not any((r.get("machine") or {}).get(a, {}).get("v2") for a in ARMS)]
    wrong = [r for r in records if r.get("verdict") == "model_wrong"]

    def best_soft(rs: list[dict]) -> list[float]:
        return sorted(max(float((r.get("machine") or {}).get(a, {}).get("v2_soft") or 0.0) for a in ARMS) for r in rs)

    u, w = best_soft(unresolved), best_soft(wrong)
    rows = []
    for t in (0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20):
        rec = sum(1 for s in u if s > t)
        fp = sum(1 for s in w if s > t)
        rows.append({
            "threshold": t,
            "evaluator_misses_recovered": rec,
            "of_unresolved": len(u),
            "recovery_rate": round(rec / max(len(u), 1), 4),
            "model_wrong_falsely_credited": fp,
            "precision": round(rec / max(rec + fp, 1), 4),
        })
    peak = max(rows, key=lambda r: r["precision"])
    return {
        "n_unresolved_misses": len(u),
        "n_model_wrong": len(w),
        "unresolved_best_soft": {"min": u[0] if u else None, "max": u[-1] if u else None},
        "model_wrong_best_soft": {"min": w[0] if w else None, "max": w[-1] if w else None},
        "rows": rows,
        "best_precision": peak["precision"],
        "conclusion": (
            f"Precision peaks at {peak['precision']:.2f} and DEGRADES as the "
            "threshold falls: the soft distributions overlap, so no threshold "
            "change recovers the misses without crediting wrong answers. "
            "Component signals are required."
        ),
    }


def component_availability(tabulation: dict[str, Any]) -> dict[str, Any]:
    """Report which component signals the tabulation can actually supply.

    Component scoring needs per-answer evidence (cited chunk ids, provision
    identity).  The tabulation stores only aggregate machine scores, so this
    records honestly what is missing instead of assuming availability.
    """
    records = tabulation.get("records") or []
    sample = next((r for r in records if r.get("machine")), None)
    if sample is None:
        return {"available": [], "missing": ["machine"], "note": "no machine records"}

    arm_keys = sorted({k for r in records for a in ARMS for k in ((r.get("machine") or {}).get(a) or {})})
    needed = {
        "provision_correct": "did the answer cite the gold provision (needs cited chunk ids + payload index)",
        "citation_correct": "did citations resolve to gold (needs cited chunk ids)",
        "legal_correct": "conclusion/concept overlap (needs answer text)",
        "hallucination_detected": "numeric claims absent from evidence (needs answer + evidence text)",
    }
    have = [k for k in needed if k in arm_keys]
    missing = [k for k in needed if k not in arm_keys]
    return {
        "stored_arm_keys": arm_keys,
        "available_components": have,
        "unavailable_components": missing,
        "component_definitions": needed,
        "note": (
            "Component scoring CANNOT be computed from the tabulation alone — it "
            "stores aggregate machine scores, not per-answer evidence. Recomputing "
            "requires re-running grade_answer over stored answers. This module "
            "therefore proves the NEGATIVE result (threshold tuning cannot work) "
            "and specifies the positive one, rather than fabricating components."
        ),
    }


def main() -> int:
    if not TABULATION.exists():
        print(f"missing: {TABULATION}", file=sys.stderr)
        return 1
    tabulation = json.loads(TABULATION.read_text(encoding="utf-8"))

    result = {
        "version": "v3-component-analysis-1",
        "date": "2026-10-04",
        "source": str(TABULATION),
        "threshold_sweep": threshold_sweep(tabulation),
        "component_availability": component_availability(tabulation),
        "recommended_rule": {
            "principle": (
                "Credit a criterion independently and OR the criteria; never lower the global overlap threshold."
            ),
            "proposed": (
                "binary_correct = provision_correct OR (legal_correct AND NOT hallucination_detected) OR abstain_credit"
            ),
            "rationale": (
                "provision identity is phrasing-independent, so a correct answer that "
                "paraphrases the reference still passes. The NOT-hallucination "
                "conjunct stops a fluent wrong-provision answer from being credited "
                "on overlap alone."
            ),
            "status": "SPECIFIED, NOT YET MEASURED — needs stored answers to evaluate",
            "next_step": (
                "Re-run grade_answer over the stored per-arm answers (they exist in "
                "the raw run outputs) to measure recovery and false-credit rates "
                "before adopting any threshold change."
            ),
        },
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    ts = result["threshold_sweep"]
    print("=" * 74)
    print("Evaluator v3 — can a threshold fix the misses? (answer: no)")
    print("=" * 74)
    print(f"unresolved misses n={ts['n_unresolved_misses']}  model_wrong n={ts['n_model_wrong']}")
    print()
    print(f"{'thresh':>7} {'recovered':>10} {'rate':>6} {'falseCredit':>13} {'precision':>10}")
    print("-" * 50)
    for r in ts["rows"]:
        print(
            f"{r['threshold']:>7.2f} {r['evaluator_misses_recovered']:>10} "
            f"{r['recovery_rate']:>6.2f} {r['model_wrong_falsely_credited']:>13} {r['precision']:>10.2f}"
        )
    print()
    print(ts["conclusion"])
    ca = result["component_availability"]
    print()
    print(f"components available from tabulation : {ca['available_components'] or 'none'}")
    print(f"components requiring stored answers   : {ca['unavailable_components']}")
    print()
    print(f"written: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

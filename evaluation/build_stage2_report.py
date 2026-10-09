"""Build the Stage-2 paired A/B report from accumulated slots (SPEC-4.4).

Reads ``evaluation/out/ceiling_v5/stage2_state.json`` (one flat
``"qid::arm"`` -> result slot per LLM call) and emits a paired aggregate with
flips, the SPEC-3 guarded selection, the missing-slot list, and the harness
limitations note.

Why a generator instead of the harness writing it directly: the accumulator
rebuilds the report on every invocation and overwrites it with whatever is
covered so far.  This script turns the accumulated state into a stable,
reproducible record of a specific slice, and writes it *outside*
``evaluation/out/`` (which is gitignored) so the evidence behind SPEC-1..3 is
actually committable.

Usage:
    python -m evaluation.build_stage2_report
    python -m evaluation.build_stage2_report --out evaluation/stage2_ab_n20_report.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

STATE_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "stage2_state.json"
STAGE1_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "targeted_retry_ab.json"
#: Deliberately outside evaluation/out/ so it survives .gitignore and can be
#: committed as the interim evidence record (SPEC-4.4).
DEFAULT_OUT = PROJECT_ROOT / "evaluation" / "stage2_ab_n20_report.json"

KEYS = (
    "binary_correct",
    "answer_correctness",
    "citation_recall",
    "citation_precision",
    "groundedness_score",
    "hallucination_detected",
    "latency_s",
)

LIMITATIONS = [
    "Live single-model run (poolside/laguna-s-2.1:free); soft deltas are noisy.",
    "Recovered-only population measures recovery, not regression.",
    "Mechanical binary here; adjudicated overlay in full gate.",
    "Free-tier 429 noise: partial slots are transient and merged only with real answers.",
]


def _load_slots() -> dict[tuple[str, str], dict[str, Any]]:
    if not STATE_FILE.exists():
        return {}
    raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    slots: dict[tuple[str, str], dict[str, Any]] = {}
    for key, value in raw.items():
        qid, _, arm = str(key).partition("::")
        if qid and arm:
            slots[(qid, arm)] = value
    return slots


def _is_good(slot: dict[str, Any] | None) -> bool:
    return bool(slot and slot.get("m") and (slot.get("answer") or "").strip())


def build() -> dict[str, Any]:
    slots = _load_slots()
    stage1 = json.loads(STAGE1_FILE.read_text(encoding="utf-8"))
    qids: list[str] = list(stage1.get("recovered_qids", []))

    baseline: dict[str, dict] = {}
    retry: dict[str, dict] = {}
    missing: list[str] = []
    for qid in qids:
        b = slots.get((qid, "base"))
        r = slots.get((qid, "retry"))
        if _is_good(b):
            baseline[qid] = b
        else:
            missing.append(f"{qid}::base")
        if _is_good(r):
            retry[qid] = r
        else:
            missing.append(f"{qid}::retry")

    paired = sorted(set(baseline) & set(retry))

    def agg(rows: dict[str, dict], qids_: list[str]) -> dict[str, float]:
        return {
            k: round(sum(rows[q]["m"].get(k, 0.0) for q in qids_) / max(len(qids_), 1), 4)
            for k in KEYS
        }

    ab = agg(baseline, paired)
    ar = agg(retry, paired)

    guarded: dict[str, Any] | None = None
    if paired:
        from evaluation.ab_targeted_retry_answers_fast import guarded_report

        guarded = guarded_report(baseline, retry, paired)

    report = {
        "benchmark": "targeted_retry_stage2_ab",
        "slice": "paired qids with both arms covered",
        "n_questions": len(paired),
        "n_recovered": len(qids),
        "qids": paired,
        "model": "poolside/laguna-s-2.1:free",
        "aggregate": {"baseline": ab, "retry": ar},
        "delta": {k: round(ar[k] - ab[k], 4) for k in KEYS},
        "flips_up": sorted(
            q for q in paired
            if retry[q]["m"].get("binary_correct") == 1 and baseline[q]["m"].get("binary_correct") == 0
        ),
        "flips_down": sorted(
            q for q in paired
            if retry[q]["m"].get("binary_correct") == 0 and baseline[q]["m"].get("binary_correct") == 1
        ),
        "guarded": guarded,
        "missing_slots": missing,
        "limitations": LIMITATIONS,
        # Per-question rows are part of the record, not an optional extra: a
        # previous regeneration attempt destroyed `stage2_state.json` while the
        # free tier was rate-limited, and the aggregates alone could not
        # reconstruct the run. Keep the full arms so a rebuild is always
        # possible from this file.
        "per_question": {
            "baseline": {q: baseline[q].get("m") for q in paired},
            "retry": {q: retry[q].get("m") for q in paired},
        },
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    out = Path(args.out)

    report = build()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 72)
    print(f"Stage-2 paired report  n={report['n_questions']}/{report['n_recovered']} recovered")
    print("=" * 72)
    ab, ar = report["aggregate"]["baseline"], report["aggregate"]["retry"]
    g = report["guarded"]["aggregate"] if report["guarded"] else {}
    for k in KEYS:
        guarded_col = f"{g[k]:>9.4f}" if k in g else " " * 9
        print(f"{k:<24} {ab[k]:>9.4f} {ar[k]:>9.4f} {ar[k] - ab[k]:+9.4f} | {guarded_col}")
    print(f"flips up   {report['flips_up']}")
    print(f"flips down {report['flips_down']}")
    if report["guarded"]:
        print(f"guarded flips up   {report['guarded']['flips_up']}")
        print(f"guarded flips down {report['guarded']['flips_down']}")
    print(f"missing slots {report['missing_slots']}")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Paired A/B comparison of two cached rerun tags — 0 LLM calls.

``rubric_top_lawyer`` scores one tag at a time, which makes reading a delta
between arms manual and error-prone.  This scores both arms of a paired run
(same qids, same pool, one flag different) and reports the *difference*, with
the discordant pairs called out: the repo measured 4/89 binary flips between
two runs of the *same* configuration (``control_pre`` vs ``control_post``,
2026-10-10), so a net movement smaller than that noise floor is not a result.

Reported per cohort (failure / control / each audit action):

* binary correctness — gained / lost / net (the discordant pairs);
* soft correctness — mean per arm and mean paired delta;
* the four top-lawyer rubric dimensions — pass rate per arm;
* citation gate — macro recall over the qids where gold reached the prompt,
  plus answers that cited nothing (needs ``citation`` records, i.e. tags run
  after 2026-10-14);
* abstention — false and missed abstentions, the standing harm guard.

Usage:
    python -m evaluation.compare_rerun_arms --base w3_ctrl_r1 --arms w3_struct_r1,w4_dir_r1
    python -m evaluation.compare_rerun_arms --base w3_ctrl_r1 --arms w3_struct_r1 --by-action
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.rubric_top_lawyer import (  # noqa: E402
    DIMENSIONS,
    load_audit_actions,
    load_cached_answers,
    score_dimensions,
)

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "rerun_gip"

#: Two runs of the same configuration flipped binary correctness on 4/89 qids,
#: so a net smaller than this is inside the noise floor.
NOISE_FLOOR_FLIPS = 4

SOFT_TIE = 0.02  # below this, a soft-correctness delta is a tie


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _metric_row(base_recs: list[dict], arm_recs: list[dict], questions: dict, actions: dict[str, str]) -> dict:
    """One paired row over matched records (last record per qid wins upstream)."""
    rows: list[dict[str, Any]] = []
    for rec_b, rec_a in zip(base_recs, arm_recs):
        qid = rec_b["qid"]
        question = questions.get(qid)
        if question is None:
            continue
        action = actions.get(qid, "unknown")
        dims_b = score_dimensions(rec_b.get("answer") or "", question, action)
        dims_a = score_dimensions(rec_a.get("answer") or "", question, action)

        cit_b = rec_b.get("citation") if isinstance(rec_b.get("citation"), dict) else None
        cit_a = rec_a.get("citation") if isinstance(rec_a.get("citation"), dict) else None
        rows.append({
            "qid": qid,
            "action": action,
            "binary_b": int(rec_b.get("binary_correct") or 0),
            "binary_a": int(rec_a.get("binary_correct") or 0),
            "soft_b": float(rec_b.get("answer_correctness") or 0.0),
            "soft_a": float(rec_a.get("answer_correctness") or 0.0),
            "dims_b": dims_b,
            "dims_a": dims_a,
            "cit_b": cit_b,
            "cit_a": cit_a,
            "error_a": bool(rec_a.get("error")),
        })

    if not rows:
        return {"n": 0}

    n = len(rows)
    gained = sum(1 for r in rows if not r["binary_b"] and r["binary_a"])
    lost = sum(1 for r in rows if r["binary_b"] and not r["binary_a"])
    soft_deltas = [r["soft_a"] - r["soft_b"] for r in rows]

    out: dict[str, Any] = {
        "n": n,
        "binary": {
            "base": sum(r["binary_b"] for r in rows),
            "arm": sum(r["binary_a"] for r in rows),
            "gained": gained,
            "lost": lost,
            "net": gained - lost,
            "inside_noise_floor": abs(gained - lost) <= NOISE_FLOOR_FLIPS,
        },
        "soft": {
            "base": _mean([r["soft_b"] for r in rows]),
            "arm": _mean([r["soft_a"] for r in rows]),
            "mean_paired_delta": _mean(soft_deltas),
            "improved": sum(1 for d in soft_deltas if d > SOFT_TIE),
            "worsened": sum(1 for d in soft_deltas if d < -SOFT_TIE),
        },
        "rubric": {},
        "abstention": {},
        "errors_arm": sum(1 for r in rows if r["error_a"]),
    }

    for dim in DIMENSIONS:
        b = [bool(r["dims_b"][dim]["pass"]) for r in rows]
        a = [bool(r["dims_a"][dim]["pass"]) for r in rows]
        deltas = [(1.0 if ya else 0.0) - (1.0 if yb else 0.0) for yb, ya in zip(b, a)]
        out["rubric"][dim] = {
            "base": _mean([1.0 if x else 0.0 for x in b]),
            "arm": _mean([1.0 if x else 0.0 for x in a]),
            "delta": _mean(deltas),
            "gained": sum(1 for yb, ya in zip(b, a) if not yb and ya),
            "lost": sum(1 for yb, ya in zip(b, a) if yb and not ya),
        }

    out["abstention"] = {
        "false_abstention_base": sum(1 for r in rows if r["dims_b"]["abstention_discipline"]["false_abstention"]),
        "false_abstention_arm": sum(1 for r in rows if r["dims_a"]["abstention_discipline"]["false_abstention"]),
        "missed_abstention_base": sum(1 for r in rows if r["dims_b"]["abstention_discipline"]["missed_abstention"]),
        "missed_abstention_arm": sum(1 for r in rows if r["dims_a"]["abstention_discipline"]["missed_abstention"]),
        "answered_base": sum(1 for r in rows if r["dims_b"]["abstention_discipline"]["abstained"]),
        "answered_arm": sum(1 for r in rows if r["dims_a"]["abstention_discipline"]["abstained"]),
    }

    # Citation gate: paired only where BOTH arms recorded gold-in-prompt,
    # so a missing instrumentation record drops the qid instead of scoring 0.
    cit_pairs = [
        (r["cit_b"], r["cit_a"])
        for r in rows
        if r["cit_b"] and r["cit_a"] and r["cit_b"].get("recall") is not None and r["cit_a"].get("recall") is not None
    ]
    if cit_pairs:
        out["citation"] = {
            "n": len(cit_pairs),
            "base": _mean([c_b["recall"] for c_b, _ in cit_pairs]),
            "arm": _mean([c_a["recall"] for _, c_a in cit_pairs]),
            "gained_any_gold": sum(1 for cb, ca in cit_pairs if ca["gold_cited"] > cb["gold_cited"]),
            "lost_gold": sum(1 for cb, ca in cit_pairs if ca["gold_cited"] < cb["gold_cited"]),
            "no_citation_base": sum(1 for cb, _ in cit_pairs if not cb.get("has_citation")),
            "no_citation_arm": sum(1 for _, ca in cit_pairs if not ca.get("has_citation")),
        }
    else:
        out["citation"] = None

    return out


def _print_row(label: str, row: dict) -> None:
    if not row.get("n"):
        print(f"{label:<28} no paired records")
        return
    b, s = row["binary"], row["soft"]
    flag = " ~noise" if b["inside_noise_floor"] else ""
    print(
        f"{label:<28} n={row['n']:<3} binary {b['base']:>2}->{b['arm']:<2} "
        f"(+{b['gained']}/-{b['lost']} net {b['net']:+d}){flag}  "
        f"soft {s['base']}->{s['arm']} ({(s['mean_paired_delta'] or 0):+.4f})"
    )
    for dim in DIMENSIONS:
        d = row["rubric"][dim]
        print(f"{'  ' + dim:<28}      {d['base']}->{d['arm']} (+{d['gained']}/-{d['lost']} {d['delta']:+.4f})")
    a = row["abstention"]
    print(
        f"{'  abstention':<28}      false {a['false_abstention_base']}->{a['false_abstention_arm']}  "
        f"missed {a['missed_abstention_base']}->{a['missed_abstention_arm']}"
    )
    if row.get("citation"):
        c = row["citation"]
        print(
            f"{'  citation':<28}      recall {c['base']}->{c['arm']} (n={c['n']})  "
            f"no-citation {c['no_citation_base']}->{c['no_citation_arm']}"
        )
    if row.get("errors_arm"):
        print(f"{'  arm errors':<28}      {row['errors_arm']} record(s) carry an LLM error")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="control tag under rerun_gip/")
    parser.add_argument("--arms", required=True, help="comma-separated treatment tags")
    parser.add_argument("--by-action", action="store_true", help="also print one row per audit action")
    parser.add_argument("--cohort", choices=["failures", "control", "all"], default="all")
    args = parser.parse_args()

    from evaluation.benchmark import load_questions

    questions = {q.question_id: q for q in load_questions()}
    actions = load_audit_actions()
    base = load_cached_answers(args.base)
    if not base:
        print(f"no cached answers for base tag {args.base!r}", file=sys.stderr)
        return 1

    report: dict[str, Any] = {
        "base": args.base,
        "arms": {},
        "cohort": args.cohort,
        "noise_floor_flips": NOISE_FLOOR_FLIPS,
    }
    arms = [t.strip() for t in args.arms.split(",") if t.strip()]

    for arm_tag in arms:
        arm = load_cached_answers(arm_tag)
        if not arm:
            print(f"no cached answers for arm tag {arm_tag!r}", file=sys.stderr)
            continue
        common = sorted(q for q in set(base) & set(arm) if q in questions)
        if args.cohort == "failures":
            common = [q for q in common if actions.get(q, "control") != "control"]
        elif args.cohort == "control":
            common = [q for q in common if actions.get(q, "control") == "control"]

        base_recs = [base[q] for q in common]
        arm_recs = [arm[q] for q in common]

        print("=" * 96)
        print(f"base={args.base}  arm={arm_tag}  paired={len(common)}  cohort={args.cohort}")
        print("=" * 96)
        overall = _metric_row(base_recs, arm_recs, questions, actions)
        _print_row("overall", overall)
        arm_report: dict[str, Any] = {"paired_qids": common, "overall": overall, "by_audit_action": {}}

        if args.by_action:
            for action in sorted({actions.get(q, "unknown") for q in common}):
                idx = [i for i, q in enumerate(common) if actions.get(q, "unknown") == action]
                row = _metric_row([base_recs[i] for i in idx], [arm_recs[i] for i in idx], questions, actions)
                _print_row(f"  {action}", row)
                arm_report["by_audit_action"][action] = row
        print()
        report["arms"][arm_tag] = arm_report

    out_path = OUT_DIR / f"compare_{args.base}__vs__{'__'.join(report['arms'])}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

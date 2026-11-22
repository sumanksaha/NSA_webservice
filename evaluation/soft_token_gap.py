"""Soft-token gap analysis: which reference tokens does the answer miss, and what is each family worth?

Phase 1 of the prompt-lift plan
(``docs/plans/PROMPT_SOFT_BINARY_LIFT.md``).  Purely offline: 0 LLM calls,
scorer untouched.  It answers one question -- *what is the answer missing, and
how much soft would closing each gap be worth?* -- so that the prompt wording
is chosen from measurement instead of guesswork.

The missing reference tokens are partitioned into five families, in priority
order, so the buckets never overlap:

``concept``
    a token belonging to a ``gold_concept`` the answer did not cover.  The
    benchmark's own statement of what the answer must contain.
``authority``
    a token belonging to a ``gold_authority`` the answer did not name.
``figure``
    a number, a money amount or a term of imprisonment.
``section_ref``
    a section or clause identifier -- ``s60``, ``s 37``, or a bare number
    adjacent to "section" in the reference text.
``other``
    the residual: ordinary operative prose.  This is the family that
    "name the exact statutory tokens" does not reach.

Each family's *headroom* is the soft score the tag would reach if that
family's missing tokens were present in the answer.  It is computed by
re-scoring the cached answer with the family's tokens appended, so the
headroom is the scorer's own arithmetic, not an estimate.

Measured on ``cond_v5`` (n=150): 1458 missing reference tokens, of which 1182
(81.1%) are ``other`` -- worth **+0.1602 soft / +105 binary** on their own,
against +0.0390 / +1 for the ``figure`` family.  A prompt arm aimed at
figures, section numbers and authorities reaches ~9% of the gap.

Usage:
    python -m evaluation.soft_token_gap --tags cond_v5,current_status_eval
    python -m evaluation.soft_token_gap --tags cond_v5 --cohort failures
    python -m evaluation.soft_token_gap --tags cond_v5 --per-qid
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from evaluation.answer_scoring import score_answer  # noqa: E402
from evaluation.benchmark import load_questions  # noqa: E402
from evaluation.rerun_gold_in_prompt import load_complement, load_targets  # noqa: E402
from evaluation.rubric_top_lawyer import load_audit_actions, load_cached_answers  # noqa: E402

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "soft_token_gap"

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Near-miss band: the answer carries real content but scores below the
#: binary threshold.  This is where soft lift is convertible.
NEAR_MISS_LO = 0.30
NEAR_MISS_HI = 0.50

#: Section-style references: "s60", "s37", "sec12".
_SECTION_TOKEN_RE = re.compile(r"^(?:s|sec|cl|clause)\d+[a-z]?$")

#: Money and term-of-imprisonment figures.
_FIGURE_TOKEN_RE = re.compile(r"^(?:\d[\d,.]*|rupees?|rs\.?|lakh|crore|months?|years?|days?)$")

FAMILY_ORDER = ("concept", "authority", "figure", "section_ref", "other")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _figure_tokens(text: str) -> set[str]:
    """Money amounts, numbers and terms of imprisonment in *text*."""
    return {t for t in _tokens(text) if _FIGURE_TOKEN_RE.match(t)}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _section_tokens(expected: str) -> set[str]:
    """Reference tokens that are section identifiers, including bare numbers
    that sit next to the word "section" in the reference text."""
    out: set[str] = set()
    toks = _TOKEN_RE.findall((expected or "").lower())
    for i, tok in enumerate(toks):
        if _SECTION_TOKEN_RE.match(tok):
            out.add(tok)
        elif tok.isdigit():
            window = toks[max(0, i - 3) : i + 4]
            if "section" in window or "clause" in window or "schedule" in window:
                out.add(tok)
    return out


def _families(answer: str, expected: str, question) -> dict[str, set[str]]:
    """Partition the missing reference tokens into the five families."""
    answer_tokens = _tokens(answer)
    expected_tokens = _tokens(expected)
    missing = expected_tokens - answer_tokens

    concept_tokens: set[str] = set()
    for concept in question.gold_concepts or []:
        need = _tokens(concept)
        if need and len(need & answer_tokens) / len(need) < 0.8:
            concept_tokens |= need

    authority_tokens: set[str] = set()
    for authority in question.gold_authorities or []:
        need = _tokens(authority)
        if need and len(need & answer_tokens) / len(need) < 0.5:
            authority_tokens |= need

    sections = _section_tokens(expected)
    families: dict[str, set[str]] = {f: set() for f in FAMILY_ORDER}
    for tok in missing:
        if tok in concept_tokens:
            families["concept"].add(tok)
        elif tok in authority_tokens:
            families["authority"].add(tok)
        elif _FIGURE_TOKEN_RE.match(tok):
            families["figure"].add(tok)
        elif tok in sections:
            families["section_ref"].add(tok)
        else:
            families["other"].add(tok)
    return families


def _headroom(rows: list[dict[str, Any]], family: str, questions: dict) -> dict[str, Any]:
    """Soft/binary the tag would reach if *family*'s missing tokens were present."""
    if not rows:
        return {"n": 0}
    base_soft: list[float] = []
    sim_soft: list[float] = []
    base_bin = 0
    sim_bin = 0
    for row in rows:
        q = questions[row["qid"]]
        toks = sorted(row["families"][family])
        if not toks:
            continue
        answer = row["answer"]
        a = score_answer(answer, q.acceptable_conclusion, q.insufficient_evidence, None)
        b = score_answer(answer + " " + " ".join(toks), q.acceptable_conclusion, q.insufficient_evidence, None)
        base_soft.append(a["answer_correctness"])
        sim_soft.append(b["answer_correctness"])
        base_bin += a["binary_correct"]
        sim_bin += b["binary_correct"]
    if not base_soft:
        return {"n": 0, "note": "no qid in this cohort is missing a token of this family"}
    return {
        "n": len(base_soft),
        "soft_base": _mean(base_soft),
        "soft_with_family": _mean(sim_soft),
        "soft_headroom": round(_mean(sim_soft) - _mean(base_soft), 4),
        "binary_base": base_bin,
        "binary_with_family": sim_bin,
        "binary_headroom": sim_bin - base_bin,
    }


def analyse(tag: str, questions: dict, actions: dict[str, str], cohort: str) -> dict[str, Any]:
    cached = load_cached_answers(tag)
    if not cached:
        return {"tag": tag, "error": f"no cached answers at rerun_gip/{tag}.jsonl"}
    rows: list[dict[str, Any]] = []
    for qid in sorted(set(cached) & set(questions)):
        if cohort == "failures" and actions.get(qid, "control") == "control":
            continue
        if cohort == "control" and actions.get(qid, "control") != "control":
            continue
        rec = cached[qid]
        if rec.get("error"):
            continue
        answer = rec.get("answer") or ""
        if not answer:
            continue
        q = questions[qid]
        rows.append({
            "qid": qid,
            "answer": answer,
            "audit_action": actions.get(qid, "control"),
            "soft": float(rec.get("answer_correctness") or 0.0),
            "coverage": float(rec.get("answer_coverage") or 0.0),
            "jaccard": 2 * float(rec.get("answer_correctness") or 0.0) - float(rec.get("answer_coverage") or 0.0),
            "families": _families(answer, q.acceptable_conclusion, q),
        })

    if not rows:
        return {"tag": tag, "error": "no scored rows in this cohort"}

    near = [r for r in rows if NEAR_MISS_LO < r["soft"] <= NEAR_MISS_HI]
    far = [r for r in rows if r["soft"] <= NEAR_MISS_LO]
    passed = [r for r in rows if r["soft"] > NEAR_MISS_HI]

    missing_counts = {f: 0 for f in FAMILY_ORDER}
    qid_counts = {f: 0 for f in FAMILY_ORDER}
    for r in rows:
        for f in FAMILY_ORDER:
            n = len(r["families"][f])
            missing_counts[f] += n
            if n:
                qid_counts[f] += 1
    total_missing = sum(missing_counts.values())

    return {
        "tag": tag,
        "cohort": cohort,
        "n": len(rows),
        "levels": {
            "soft": _mean([r["soft"] for r in rows]),
            "coverage": _mean([r["coverage"] for r in rows]),
            "jaccard": _mean([r["jaccard"] for r in rows]),
        },
        "bands": {
            "near_miss": {"n": len(near), "range": f"{NEAR_MISS_LO} < soft <= {NEAR_MISS_HI}"},
            "far_miss": {"n": len(far), "range": f"soft <= {NEAR_MISS_LO}"},
            "pass": {"n": len(passed), "range": f"soft > {NEAR_MISS_HI}"},
        },
        "missing_tokens": {
            "total": total_missing,
            "per_qid_mean": round(total_missing / len(rows), 2),
            "by_family": {
                f: {
                    "tokens": missing_counts[f],
                    "share_pct": round(100.0 * missing_counts[f] / max(1, total_missing), 1),
                    "qids": qid_counts[f],
                }
                for f in FAMILY_ORDER
            },
        },
        "headroom_by_family": {f: _headroom(rows, f, questions) for f in FAMILY_ORDER},
        "per_qid": [
            {
                "qid": r["qid"],
                "audit_action": r["audit_action"],
                "soft": r["soft"],
                "coverage": r["coverage"],
                "jaccard": r["jaccard"],
                "missing_total": sum(len(r["families"][f]) for f in FAMILY_ORDER),
                "missing_by_family": {f: len(r["families"][f]) for f in FAMILY_ORDER},
            }
            for r in sorted(rows, key=lambda x: x["soft"])
        ],
    }


def _print_report(result: dict[str, Any]) -> None:
    if result.get("error"):
        print(f"{result['tag']}: {result['error']}")
        return
    lv = result["levels"]
    print("=" * 100)
    print(f"tag={result['tag']}  cohort={result['cohort']}  n={result['n']}")
    print(f"  soft {lv['soft']}   coverage {lv['coverage']}   jaccard {lv['jaccard']}")
    b = result["bands"]
    print(
        f"  near-miss {b['near_miss']['n']} ({b['near_miss']['range']})   "
        f"far-miss {b['far_miss']['n']}   pass {b['pass']['n']}"
    )
    mt = result["missing_tokens"]
    print(f"  missing reference tokens: {mt['total']} total, {mt['per_qid_mean']} per qid")
    print(f"    {'family':<16} {'tokens':>8} {'share':>8} {'qids':>7}   {'soft headroom':>14} {'binary':>9}")
    for fam, blk in result["missing_tokens"]["by_family"].items():
        head = result["headroom_by_family"].get(fam) or {}
        hs = head.get("soft_headroom")
        hb = head.get("binary_headroom")
        print(
            f"    {fam:<16} {blk['tokens']:>8} {blk['share_pct']:>7}% {blk['qids']:>7}   "
            f"{(f'{hs:+.4f}' if hs is not None else '--'):>14} {(f'{hb:+d}' if hb is not None else '--'):>9}"
        )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tags", default="cond_v5,current_status_eval", help="comma-separated cached tags")
    parser.add_argument("--cohort", choices=["failures", "control", "all"], default="all")
    parser.add_argument("--per-qid", action="store_true", help="write the per-qid table to the artifact")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    questions = {q.question_id: q for q in load_questions()}
    actions = load_audit_actions()

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    summary: dict[str, Any] = {"cohort": args.cohort, "per_tag": {}}
    for tag in tags:
        result = analyse(tag, questions, actions, args.cohort)
        _print_report(result)
        summary["per_tag"][tag] = result
        (out_dir / f"soft_token_gap_{tag}_{args.cohort}.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    (out_dir / f"soft_token_gap_summary_{args.cohort}.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"written: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

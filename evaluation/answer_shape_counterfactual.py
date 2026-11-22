"""Answer-shape counterfactual: what does the scorer say if the quote block goes?

The shipped grounded-QA template answers in two steps -- ``Step 1 - Quotes``
(verbatim source passages) and ``Step 2`` (the answer).  Both land in the
scored text, and Step 1 is roughly half of it: median 259 tokens of quotes
against 244 tokens of prose, for a benchmark reference of 29 tokens
(``evaluation/out/ceiling_v5/rerun_gip/cond_v5.jsonl``, n=150).

``soft`` is ``(jaccard + coverage) / 2`` and jaccard's denominator is the union
of the answer's and the reference's token sets.  A quote block inflates that
denominator without adding reference tokens the prose did not already carry,
so it depresses jaccard -- and therefore binary correctness -- as a function
of length alone.

Measured on ``cond_v5`` (n=150): the Step 1 block is **114 unique tokens and
adds only 1.32 reference tokens** (coverage: Step 1 alone 0.4866, Step 2
alone 0.6212, union 0.6667).  The prose already carries 18.0 of the 19.3
reference tokens -- 93%.

This harness prices the shape change three ways.  0 LLM calls:

``full``
    the answer as generated.
``prose_only``
    the ``Step 2`` block alone -- the pure length effect of dropping the
    quotes.  Coverage *falls* here, because the quotes carried reference
    tokens the prose paraphrased away.
``prose_plus_ref``
    the ``Step 2`` block with the reference tokens the full answer carried but
    the prose dropped, re-appended.  This is a **proxy for the target answer
    shape**, not a prediction of what a model will emit: it approximates
    "the prose states the operative content in the source's own words".  Read
    it as an upper bound on the shape.

The exchange rate is lopsided and is why the shape arm has to be gated on
coverage, not on soft alone: ``d(coverage)/d(token) = 1/29`` against
``d(jaccard)/d(token) = -m/(N-m)^2``.  At N=176, m=19.3 that is 0.0345 against
0.00038 -- **one reference token is worth ~90 non-reference tokens**.  Content
must never be traded for length.

Usage:
    python -m evaluation.answer_shape_counterfactual
    python -m evaluation.answer_shape_counterfactual --tags cond_v5,cond_v7_w3w4 --by-action
    python -m evaluation.answer_shape_counterfactual --all --cohort failures
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from evaluation.answer_scoring import score_answer  # noqa: E402
from evaluation.benchmark import load_questions  # noqa: E402
from evaluation.rerun_gold_in_prompt import (  # noqa: E402
    OUT_DIR as RERUN_DIR,
    load_complement,
    load_targets,
)
from evaluation.rubric_top_lawyer import (  # noqa: E402
    load_audit_actions,
    load_cached_answers,
)

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "answer_shape"

#: Matches a step heading line -- ``Step 1 - Quotes``, ``Step 2 - Answer``.
STEP_SPLIT_RE = re.compile(r"(?im)^\s*[-*#]*\s*step\s*([12])\b[^\n]*$")

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CITATION_RE = re.compile(r"\[\d+\]")

#: Tags that exist on disk and are worth carrying by default -- the paired
#: arms the repo has already run, oldest first.
DEFAULT_TAGS = (
    "cond_v5_ctrl",
    "cond_v5",
    "cond_v6_ctrl",
    "cond_v6_contract",
    "cond_v7_w4v2",
    "cond_v7_w3w4",
    "current_status_eval",
)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _median(values: list[float]) -> float | None:
    return round(float(statistics.median(values)), 4) if values else None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def split_steps(answer: str) -> tuple[str, str] | None:
    """Split a two-step answer into ``(quote block, prose)``.

    Tolerates a missing ``Step 1`` heading: when only ``Step 2`` is present,
    everything before it is the quote block.  Returns ``None`` when the answer
    carries no step headings at all -- the structured-reasoner template renders
    a legal memo (Issue / Rule / Application / Conclusion), not two steps, so
    the counterfactual does not apply to it.
    """
    matches = list(STEP_SPLIT_RE.finditer(answer or ""))
    if not matches:
        return None
    if len(matches) >= 2:
        return (
            (answer or "")[matches[0].end() : matches[1].start()],
            (answer or "")[matches[1].end() :],
        )
    only = matches[0]
    if only.group(1) != "2":
        return None
    return (answer or "")[: only.start()], (answer or "")[only.end() :]


def _variants(answer: str, expected: str) -> dict[str, Any] | None:
    """The scored shapes for one answer, or ``None`` when it has no Step split."""
    split = split_steps(answer)
    if split is None:
        return None
    quote_block, prose = split
    full_tokens = _tokens(answer)
    prose_tokens = _tokens(prose)
    expected_tokens = _tokens(expected)
    # Reference tokens the full answer carried that the prose dropped: the
    # operative content that survived the quote block but not the prose.
    carried = sorted((expected_tokens & full_tokens) - prose_tokens)
    markers_step1 = set(_CITATION_RE.findall(quote_block))
    markers_step2 = set(_CITATION_RE.findall(prose))
    return {
        "full": answer or "",
        "quote_block": quote_block,
        "prose_only": prose,
        "prose_plus_ref": prose + " " + " ".join(carried),
        "n_carried": len(carried),
        "markers_only_in_step1": len(markers_step1 - markers_step2),
        "markers_step1": len(markers_step1),
        "markers_step2": len(markers_step2),
        "tokens_full": len(full_tokens),
        "tokens_prose": len(prose_tokens),
    }


def _row(rec: dict[str, Any], question) -> dict[str, Any] | None:
    """Score one cached answer under all three shapes."""
    variants = _variants(rec.get("answer") or "", question.acceptable_conclusion)
    if variants is None:
        return None
    scored: dict[str, dict[str, Any]] = {}
    for name in ("full", "prose_only", "prose_plus_ref"):
        scored[name] = score_answer(
            variants[name],
            question.acceptable_conclusion,
            question.insufficient_evidence,
            None,
        )
    return {
        "qid": rec.get("qid"),
        "audit_action": rec.get("audit_action") or "unknown",
        "scored": scored,
        "n_carried": variants["n_carried"],
        "markers_only_in_step1": variants["markers_only_in_step1"],
        "markers_step1": variants["markers_step1"],
        "markers_step2": variants["markers_step2"],
        "tokens_full": variants["tokens_full"],
        "tokens_prose": variants["tokens_prose"],
        "abstained": bool(rec.get("abstained")),
        "error": bool(rec.get("error")),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cohort roll-up of the three shapes."""
    if not rows:
        return {"n": 0}
    out: dict[str, Any] = {"n": len(rows)}
    for name in ("full", "prose_only", "prose_plus_ref"):
        soft = [r["scored"][name]["answer_correctness"] for r in rows]
        binary = sum(r["scored"][name]["binary_correct"] for r in rows)
        out[name] = {
            "soft": _mean(soft),
            "binary": binary,
            "coverage": _mean([r["scored"][name]["answer_coverage"] for r in rows]),
            "jaccard": _mean([r["scored"][name]["answer_jaccard"] for r in rows]),
        }
    out["delta_prose_only"] = {
        "soft": round(out["prose_only"]["soft"] - out["full"]["soft"], 4),
        "binary": out["prose_only"]["binary"] - out["full"]["binary"],
    }
    out["delta_prose_plus_ref"] = {
        "soft": round(out["prose_plus_ref"]["soft"] - out["full"]["soft"], 4),
        "binary": out["prose_plus_ref"]["binary"] - out["full"]["binary"],
    }
    out["length"] = {
        "full_median_unique_tokens": _median([r["tokens_full"] for r in rows]),
        "prose_median_unique_tokens": _median([r["tokens_prose"] for r in rows]),
    }
    out["citations"] = {
        "step1_median_markers": _median([r["markers_step1"] for r in rows]),
        "step2_median_markers": _median([r["markers_step2"] for r in rows]),
        "median_markers_only_in_step1": _median([r["markers_only_in_step1"] for r in rows]),
        "qids_with_step1_only_markers": sum(1 for r in rows if r["markers_only_in_step1"] > 0),
    }
    out["mean_carried_reference_tokens"] = round(sum(r["n_carried"] for r in rows) / len(rows), 3)
    out["recorded_abstained"] = sum(1 for r in rows if r["abstained"])
    return out


def score_tag(tag: str, questions: dict, actions: dict[str, str], cohort: str) -> dict[str, Any]:
    """Score one cached tag under all three shapes, per cohort and audit action."""
    cached = load_cached_answers(tag)
    if not cached:
        return {"tag": tag, "error": f"no cached answers at rerun_gip/{tag}.jsonl"}
    rows: list[dict[str, Any]] = []
    unsplit = 0
    for qid in sorted(set(cached) & set(questions)):
        if cohort == "failures" and actions.get(qid, "control") == "control":
            continue
        if cohort == "control" and actions.get(qid, "control") != "control":
            continue
        rec = cached[qid]
        if rec.get("error"):
            continue
        row = _row(rec, questions[qid])
        if row is None:
            unsplit += 1
            continue
        row["audit_action"] = actions.get(qid, "control")
        rows.append(row)
    by_action: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_action.setdefault(r["audit_action"], []).append(r)
    return {
        "tag": tag,
        "cohort": cohort,
        "n_scored": len(rows),
        "n_unsplit": unsplit,
        "overall": _aggregate(rows),
        "failure_cohort": _aggregate([r for r in rows if r["audit_action"] != "control"]),
        "control_cohort": _aggregate([r for r in rows if r["audit_action"] == "control"]),
        "by_audit_action": {a: _aggregate(rs) for a, rs in sorted(by_action.items())},
    }


def _fmt(row: dict[str, Any]) -> str:
    o = row.get("overall") or {}
    if not o.get("n"):
        return "no rows"
    f, p, r = o["full"], o["prose_only"], o["prose_plus_ref"]
    return (
        f"n={o['n']:<4} soft {f['soft']:.4f}->{r['soft']:.4f} ({o['delta_prose_plus_ref']['soft']:+.4f})  "
        f"binary {f['binary']}->{r['binary']} ({o['delta_prose_plus_ref']['binary']:+d})  "
        f"cov {f['coverage']:.4f}->{r['coverage']:.4f}  jac {f['jaccard']:.4f}->{r['jaccard']:.4f}  "
        f"[prose-only soft {o['delta_prose_only']['soft']:+.4f} bin {o['delta_prose_only']['binary']:+d}]"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tags", default=",".join(DEFAULT_TAGS), help="comma-separated cached tags")
    parser.add_argument("--all", action="store_true", help="score every tag under rerun_gip/")
    parser.add_argument("--cohort", choices=["failures", "control", "all"], default="all")
    parser.add_argument("--by-action", action="store_true", help="also print one row per audit action")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    questions = {q.question_id: q for q in load_questions()}
    actions = load_audit_actions()

    if args.all:
        tags = sorted(p.stem for p in RERUN_DIR.glob("*.jsonl"))
    else:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    print("=" * 118)
    print(f"answer-shape counterfactual  cohort={args.cohort}")
    print("=" * 118)
    summary: dict[str, Any] = {"cohort": args.cohort, "per_tag": {}}
    for tag in tags:
        result = score_tag(tag, questions, actions, args.cohort)
        if result.get("error"):
            print(f"{tag:<24} {result['error']}")
            continue
        print(f"{tag:<24} {_fmt(result)}")
        if result.get("n_unsplit"):
            print(
                f"{'':<24} ({result['n_unsplit']} answers with no Step 1 / Step 2 split "
                f"-- not the two-step template; counterfactual does not apply)"
            )
        if args.by_action:
            for action, agg in result["by_audit_action"].items():
                if action == "control" or not agg.get("n"):
                    continue
                print(f"    {action:<20} {_fmt({'overall': agg})}")
        summary["per_tag"][tag] = {k: v for k, v in result.items() if k != "rows"}
        (out_dir / f"answer_shape_{tag}.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    (out_dir / f"answer_shape_summary_{args.cohort}.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nwritten: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

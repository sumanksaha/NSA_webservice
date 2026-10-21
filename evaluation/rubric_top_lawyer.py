"""Top-lawyer rubric: score answers on the audit's own failure dimensions.

The pooled binary/soft gates cannot see *how* an answer is wrong — the
150-question human audit attributed each ``model_wrong`` failure to a
``model_action`` (``provision_check`` n=35, ``abstention_gate`` n=31,
``needs_deeper_reasoning`` n=23, from
``evaluation/out/ceiling_v5/full_review_tabulation.json``), and the repo
established that two identical runs flip the pooled gate on 8/89 questions
(``evaluation/FINDINGS_89_STATUS.md``). A lever that fixes one dimension
while breaking another is invisible in a pooled mean.

This harness scores each cached answer — no LLM calls — on four dimensions,
one per audited failure mode plus the presentation dimension:

``provision_check``
    Does the answer name a gold authority (Act + section)?  A wrong
    governing provision is the single largest audit bucket.
``reasoning_depth``
    Gold-concept coverage, plus penalty-structure completeness on
    ``penalty``-typed questions (base + continuing daily fine + escalation)
    — the audit's proviso-blindness / penalty-incompleteness modes.
``abstention_discipline``
    Does the answer abstain exactly when it should — on
    ``insufficient_evidence`` questions and on the audit's
    ``abstention_gate`` failures — and answer otherwise?  A top lawyer
    qualifies what the corpus does not establish; a false abstention on an
    answerable question is as wrong as an unqualified assertion.
``presentation``
    Memo structure (Issue / Rule / Application / Conclusion headings),
    and no completeness claim when gold concepts are still missing.

Scores are reported per dimension, per cohort (failure vs control) and per
audit action — never pooled — because that is the only way a lever aimed at
one slice is distinguishable from noise and from regression on another.

Answers are read from the cached runs produced by
``evaluation/rerun_gold_in_prompt.py`` (``--tag``), so the baseline scorecard
exists at zero cost and any later live run is scored by the same rubric.

Usage:
    python -m evaluation.rubric_top_lawyer --rescore baseline,cond_v3
    python -m evaluation.rubric_top_lawyer --rescore baseline --cohort failures
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "rubric"

DIMENSIONS = ("provision_check", "reasoning_depth", "abstention_discipline", "presentation")

#: A dimension pass requires at least this fraction of its components —
#: the frozen soft threshold the dual scorecard already uses
#: (``evaluation/answer_scoring.BINARY_CORRECTNESS_THRESHOLD``).
DIMENSION_PASS_THRESHOLD = 0.5

#: Memo-structure labels a lawyer-style answer opens sections with.  Counted
#: as distinct labels found at a line start (optionally numbered, markdown
#: bold, or colon-terminated), so an unstructured prose answer scores 0.
_STRUCTURE_LABELS = ("issue", "rule", "application", "conclusion", "holding", "analysis")
_STRUCTURE_LINE_RE = re.compile(
    r"^\s*(?:[-*\d.]+\s+)?\**\s*(" + "|".join(_STRUCTURE_LABELS) + r")\b\s*[:\-—]?\s*\**",
    re.IGNORECASE | re.MULTILINE,
)

#: Penalty-structure components (audit mode "penalty incompleteness").
_CONTINUING_RE = re.compile(r"\b(continuing|further fine|per day|daily fine|each day)\b", re.IGNORECASE)
_ESCALATION_RE = re.compile(
    r"\b(repeat|subsequent|second or\s+\w+\s+conviction|aggravat|higher fine|on a\s+\w+\s+conviction)\b", re.IGNORECASE
)
_BASE_PENALTY_RE = re.compile(
    r"\b(fine|imprisonment|term\s+(?:which\s+)?may\s+extend|penalty|rupees|₹)\b", re.IGNORECASE
)
_MONEY_RE = re.compile(r"(?:₹|rs\.?|inr)\s*[\d,]+|[\d,]+\s*rupees", re.IGNORECASE)

#: Completeness claims that are false while gold concepts are still missing
#: (audit mode "completeness claimed" on a partial answer).
_COMPLETENESS_CLAIM_RE = re.compile(
    r"\b(all parts|all of the (?:above|parts)|complete list|completely\s+(?:covered|addressed)|"
    r"every part (?:is|has been))\b",
    re.IGNORECASE,
)


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def load_cached_answers(tag: str) -> dict[str, dict[str, Any]]:
    """qid -> cached generation record for *tag* (``rerun_gip/<tag>.jsonl``)."""
    from evaluation.rerun_gold_in_prompt import OUT_DIR as RERUN_DIR

    path = RERUN_DIR / f"{tag}.jsonl"
    if not path.exists():
        return {}
    recs: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            recs[str(rec.get("qid"))] = rec
    return recs


def load_audit_actions() -> dict[str, str]:
    """qid -> audit model_action ("control" for the non-failure cohort)."""
    from evaluation.rerun_gold_in_prompt import load_complement, load_targets

    actions = {str(t["qid"]): str(t.get("audit_action") or "unknown") for t in load_targets()}
    actions.update({str(c["qid"]): "control" for c in load_complement()})
    return actions


def _mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _structure_labels(answer: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _STRUCTURE_LINE_RE.finditer(answer or "")})


def _penalty_structure(answer: str) -> float | None:
    """Fraction of penalty-structure components present (None when absent)."""
    components = [
        bool(_BASE_PENALTY_RE.search(answer) and _MONEY_RE.search(answer)),
        bool(_CONTINUING_RE.search(answer)),
        bool(_ESCALATION_RE.search(answer)),
    ]
    return sum(components) / len(components)


def score_dimensions(answer: str, question, audit_action: str) -> dict[str, dict[str, Any]]:
    """Score one answer on all four top-lawyer dimensions."""
    from evaluation.abstention_rule import abstain_match
    from evaluation.rerun_gold_in_prompt import authority_coverage, concept_coverage

    answer = answer or ""
    qtypes = [str(t).lower() for t in (question.question_types or [])]

    # -- provision_check: the governing authority is named --------------- #
    auth = authority_coverage(answer, question)
    provision_check = {"pass": bool(auth["hit"]), "authority": auth.get("authority")}

    # -- reasoning_depth: concepts + penalty structure -------------------- #
    cov = concept_coverage(answer, question)
    concepts_total = len(cov["covered"]) + len(cov["missing"])
    concept_fraction = (len(cov["covered"]) / concepts_total) if concepts_total else None
    depth_components = [concept_fraction] if concept_fraction is not None else []
    penalty_structure = None
    if any("penalty" in t for t in qtypes):
        penalty_structure = _penalty_structure(answer)
        if penalty_structure is not None:
            depth_components.append(penalty_structure)
    depth_score = _mean(depth_components)
    reasoning_depth = {
        "pass": bool(depth_score is not None and depth_score >= DIMENSION_PASS_THRESHOLD),
        "score": _round(depth_score),
        "concepts_covered": len(cov["covered"]),
        "concepts_missing": len(cov["missing"]),
        "penalty_structure": _round(penalty_structure),
    }

    # -- abstention_discipline: abstain exactly when the reference does --- #
    should_abstain = bool(question.insufficient_evidence) or audit_action == "abstention_gate"
    abstained = bool(abstain_match(answer))
    abstention_discipline = {
        "pass": abstained == should_abstain,
        "should_abstain": should_abstain,
        "abstained": abstained,
        "false_abstention": bool(abstained and not should_abstain),
        "missed_abstention": bool(should_abstain and not abstained),
    }

    # -- presentation: memo structure + no false completeness ------------- #
    labels = _structure_labels(answer)
    structure_score = min(len(labels), 4) / 4
    claims_complete = bool(_COMPLETENESS_CLAIM_RE.search(answer))
    false_completeness = bool(claims_complete and cov["missing"])
    presentation_components = [structure_score, 0.0 if false_completeness else 1.0]
    if audit_action == "abstention_gate":
        # A qualified refusal is the correct "presentation" on this slice:
        # structure labels are not expected of a refusal.
        presentation_components.append(1.0 if abstained else 0.0)
    presentation_score = _mean(presentation_components)
    presentation = {
        "pass": bool(presentation_score is not None and presentation_score >= DIMENSION_PASS_THRESHOLD),
        "score": _round(presentation_score),
        "structure_labels": labels,
        "false_completeness": false_completeness,
    }

    return {
        "provision_check": provision_check,
        "reasoning_depth": reasoning_depth,
        "abstention_discipline": abstention_discipline,
        "presentation": presentation,
    }


def _cohort_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-dimension pass rates for one cohort of scored rows."""
    out: dict[str, Any] = {"n": len(rows)}
    for dim in DIMENSIONS:
        key = f"{dim}.pass"
        passes = sum(1 for r in rows if r.get(key))
        scored = sum(1 for r in rows if r.get(key) is not None)
        out[dim] = {
            "pass": passes,
            "scored": scored,
            "rate": _round(passes / scored) if scored else None,
        }
    out["false_abstention_rate"] = (
        _round(sum(1 for r in rows if r.get("abstention_discipline.false_abstention")) / len(rows)) if rows else None
    )
    out["missed_abstention_rate"] = (
        _round(sum(1 for r in rows if r.get("abstention_discipline.missed_abstention")) / len(rows)) if rows else None
    )
    return out


def score_tag(tag: str, questions: dict[str, Any], audit_actions: dict[str, str], cohort: str) -> dict[str, Any]:
    """Score one cached tag through the rubric; per-dimension, per cohort."""
    cached = load_cached_answers(tag)
    if not cached:
        return {"tag": tag, "error": f"no cached answers at rerun_gip/{tag}.jsonl"}

    targets = sorted(set(audit_actions) & set(questions))
    rows: list[dict[str, Any]] = []
    for qid in targets:
        rec = cached.get(qid)
        answer = (rec or {}).get("answer") or ""
        if not rec:
            continue
        if cohort == "failures" and audit_actions.get(qid) == "control":
            continue
        if cohort == "control" and audit_actions.get(qid) != "control":
            continue
        dims = score_dimensions(answer, questions[qid], audit_actions.get(qid, "unknown"))
        flat: dict[str, Any] = {"qid": qid, "audit_action": audit_actions.get(qid, "unknown")}
        for dim, values in dims.items():
            for key, value in values.items():
                flat[f"{dim}.{key}"] = value
        flat["cached.binary_correct"] = rec.get("binary_correct")
        flat["cached.answer_correctness"] = rec.get("answer_correctness")
        rows.append(flat)

    failure_rows = [r for r in rows if r["audit_action"] != "control"]
    control_rows = [r for r in rows if r["audit_action"] == "control"]
    by_action: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_action.setdefault(r["audit_action"], []).append(r)

    return {
        "tag": tag,
        "n_scored": len(rows),
        "n_missing": len(targets) - len(rows),
        "failure_cohort": _cohort_rows(failure_rows),
        "control_cohort": _cohort_rows(control_rows),
        "by_audit_action": {a: _cohort_rows(rs) for a, rs in sorted(by_action.items())},
        "rows": rows,
    }


def _print_report(result: dict[str, Any]) -> None:
    if result.get("error"):
        print(f"{result['tag']:<16} {result['error']}")
        return
    print("=" * 86)
    print(f"tag={result['tag']}  scored={result['n_scored']}  missing={result['n_missing']}")
    print("=" * 86)
    header = f"{'cohort':<26} {'n':>4}" + "".join(f" {d[:12]:>13}" for d in DIMENSIONS)
    print(header)
    print("-" * len(header))

    def line(label: str, cohort: dict[str, Any]) -> None:
        if not cohort.get("n"):
            return
        cells = ""
        for dim in DIMENSIONS:
            rate = cohort[dim]["rate"]
            n_pass = cohort[dim]["pass"]
            rate_text = f"{rate:.2f}" if rate is not None else "--"
            cells += f" {rate_text} ({n_pass})".rjust(13)
        print(f"{label:<26} {cohort['n']:>4}{cells}")

    line("failure cohort", result["failure_cohort"])
    line("control cohort", result["control_cohort"])
    for action, cohort in result["by_audit_action"].items():
        if action != "control":
            line(f"  {action}", cohort)
    fc = result["failure_cohort"]
    print(f"\nfalse abstentions: {fc['false_abstention_rate']}   missed abstentions: {fc['missed_abstention_rate']}")


def main() -> int:
    from evaluation.benchmark import load_questions

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rescore", required=True, help="comma-separated cached tags under rerun_gip/")
    parser.add_argument("--cohort", choices=["failures", "control", "all"], default="all")
    parser.add_argument("--out-dir", default="", help="override output directory (default out/ceiling_v5/rubric/)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    questions = {q.question_id: q for q in load_questions()}
    audit_actions = load_audit_actions()

    tags = [t.strip() for t in args.rescore.split(",") if t.strip()]
    results = [score_tag(tag, questions, audit_actions, args.cohort) for tag in tags]

    summary = {
        "benchmark": "top_lawyer_rubric",
        "method": (
            "Deterministic per-dimension scoring of cached answers against the "
            "audit's model_action taxonomy. 0 LLM calls."
        ),
        "dimensions": {
            "provision_check": "answer names a gold authority (Act + section)",
            "reasoning_depth": "gold-concept coverage (+ penalty structure on penalty queries)",
            "abstention_discipline": "abstains exactly when the reference expects it",
            "presentation": "memo structure; no completeness claim while concepts are missing",
        },
        "cohort_filter": args.cohort,
        "tags": tags,
        "per_tag": {r["tag"]: {k: v for k, v in r.items() if k != "rows"} for r in results},
    }
    (out_dir / "rubric_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    for r in results:
        (out_dir / f"{r['tag']}_rubric.json").write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
        _print_report(r)
    print(f"\nwritten: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

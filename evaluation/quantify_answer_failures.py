"""S28 step 5 — quantify answer failures by §22 error category.

Reads per-question results (Experiment C ``experiment_C_per_question.json``
and/or Experiment B-style records), runs each failed question through
``evaluation.answer_error_taxonomy.classify_answer_failure``, and writes:

* ``*_distribution.json`` — per-category counts + rates + co-occurrence
* ``*_report.md`` — human-readable ranking of dominant error classes

Usage:
    python -m evaluation.quantify_answer_failures --input evaluation/out/ceiling_v5/experiment_C_per_question.json
    python -m evaluation.quantify_answer_failures --input results.json --condition O3_full_support --out-json dist.json --out-md report.md

Input shapes accepted:
* Exp C per-question: ``{qid: {"oracle_conditions": {cond: {...}}, ...}}``
* Flat records: ``{qid: {...record...}}`` or ``[{...record...}]``

A ``--condition`` selects which oracle condition to quantify (default
``O3_full_support``); flat records ignore it.  Records carry a
``question_id`` (and ``condition`` for Exp C rows) into the output so the
human audit (§28 step 3) can sample per category.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.answer_error_taxonomy import (
    CATEGORY_ORDER,
    classify_answer_failure,
    classify_with_reasons,
    is_failure_verdict,
)

DEFAULT_CONDITION = "O3_full_support"


def iter_records(payload: Any, condition: str = DEFAULT_CONDITION) -> list[dict[str, Any]]:
    """Normalize Exp C / flat payloads to ``[{question_id, condition, ...}]``."""
    rows: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for i, rec in enumerate(payload):
            if not isinstance(rec, dict):
                continue
            row = dict(rec)
            row.setdefault("question_id", str(i))
            rows.append(row)
        return rows
    if not isinstance(payload, dict):
        return rows
    for qid, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        conds = entry.get("oracle_conditions")
        if isinstance(conds, dict):
            rec = conds.get(condition, {})
            if not isinstance(rec, dict) or rec.get("status", "ok") != "ok":
                continue
            row = dict(rec)
            row["question_id"] = qid
            row["condition"] = condition
            if entry.get("n_gold_units") is not None:
                row.setdefault("n_gold_units", entry["n_gold_units"])
            rows.append(row)
        else:
            row = dict(entry)
            row.setdefault("question_id", qid)
            rows.append(row)
    return rows


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify failures and count §22 categories (multi-label aware)."""
    # Failure semantics shared with the taxonomy (fail-open on unknown
    # verdicts) — every counted failure gets ≥1 category, so rates reconcile.
    failures = [r for r in rows if is_failure_verdict(r.get("correct", r.get("answer_correctness")))]
    cat_counts: Counter = Counter()
    per_question: dict[str, list[str]] = {}
    for rec in failures:
        cats = classify_answer_failure(rec)
        qid = str(rec.get("question_id", "?"))
        per_question[qid] = cats
        for c in cats:
            cat_counts[c] += 1
    n_fail = len(failures)
    distribution = {
        c: {"count": cat_counts.get(c, 0), "rate": round(cat_counts.get(c, 0) / n_fail, 4) if n_fail else 0.0}
        for c in CATEGORY_ORDER
    }
    ranked = sorted(CATEGORY_ORDER, key=lambda c: (-cat_counts.get(c, 0), c))
    return {
        "n_questions": len(rows),
        "n_failures": n_fail,
        "n_correct": len(rows) - n_fail,
        "distribution": distribution,
        "ranked_categories": ranked,
        "per_question": per_question,
    }


def sample_worksheet(rows: list[dict[str, Any]], n_total: int = 40, seed: int = 1) -> list[dict[str, Any]]:
    """Stratified human-audit sample (§28 step 3): round-robin over ranked categories.

    Deterministic for a given seed.  Correct answers are excluded; each entry
    carries ``question_id``, ``condition``, ``categories``, ``reasons`` and a
    truncated ``answer`` so auditors can classify without re-running anything.
    """
    import random

    failures = [r for r in rows if is_failure_verdict(r.get("correct", r.get("answer_correctness")))]
    buckets: dict[str, list[dict[str, Any]]] = {}
    for rec in failures:
        for cat in classify_answer_failure(rec):
            buckets.setdefault(cat, []).append(rec)
    ranked = sorted(buckets, key=lambda c: (-len(buckets[c]), c))
    rng = random.Random(seed)
    for members in buckets.values():
        rng.shuffle(members)
    picked: dict[str, dict[str, Any]] = {}
    indices = {cat: 0 for cat in ranked}
    while len(picked) < n_total and any(indices[c] < len(buckets[c]) for c in ranked):
        for cat in ranked:
            if len(picked) >= n_total or indices[cat] >= len(buckets[cat]):
                continue
            rec = buckets[cat][indices[cat]]
            indices[cat] += 1
            qid = str(rec.get("question_id", "?"))
            if qid not in picked:
                picked[qid] = rec
    entries = []
    for qid, rec in picked.items():
        entries.append({
            "question_id": qid,
            "condition": rec.get("condition"),
            "categories": classify_answer_failure(rec),
            "reasons": classify_with_reasons(rec),
            "answer": str(rec.get("answer") or "")[:500],
        })
    return entries


def render_worksheet(entries: list[dict[str, Any]]) -> str:
    """Render the audit worksheet auditors fill in (§28 step 3)."""
    lines = [
        "# Answer-failure audit worksheet (S28 step 3)",
        "",
        "For each question, confirm or correct the pre-sort categories:",
        "clearly-wrong / partially-correct / lexically-different-but-acceptable /",
        "incomplete / wrong-citation / reasoning-error / evaluation-mismatch.",
        "",
        "| # | Question | Condition | Pre-sort categories | Auditor verdict |",
        "|---:|---|---|---|---|",
    ]
    for i, entry in enumerate(entries, 1):
        cats = ", ".join(entry["categories"])
        lines.append(f"| {i} | {entry['question_id']} | {entry.get('condition', '-')} | {cats} |  |")
    lines += [
        "",
        "## Per-question reasons (heuristic — verify, do not trust)",
        "",
    ]
    for entry in entries:
        lines.append(f"### {entry['question_id']}")
        for cat, reason in entry["reasons"].items():
            lines.append(f"- **{cat}**: {reason}")
        if entry.get("answer"):
            lines.append(f"- answer: {entry['answer']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_markdown(result: dict[str, Any], condition: str) -> str:
    """Render the §28 step-5 report (dominant classes first)."""
    lines = [
        "# Answer-failure quantification (S28 step 5)",
        "",
        f"Condition: `{condition}` — {result['n_failures']} failures / {result['n_questions']} questions.",
        "",
        "| Rank | Category | Count | Rate |",
        "|---:|---|---:|---:|",
    ]
    for i, cat in enumerate(result["ranked_categories"], 1):
        d = result["distribution"][cat]
        lines.append(f"| {i} | {cat} | {d['count']} | {d['rate']:.1%} |")
    lines += [
        "",
        "Multi-label: one question can carry several categories (e.g. "
        "`exception` + `citation`). Rates sum to more than 100% by design.",
        "Sample per category for the §28 step-3 human audit using `per_question` in the JSON.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (also importable for tests)."""
    parser = argparse.ArgumentParser(description="Quantify answer failures by §22 category (S28 step 5).")
    parser.add_argument("--input", required=True, help="Per-question JSON (Exp C or flat records).")
    parser.add_argument("--condition", default=DEFAULT_CONDITION, help="Exp C oracle condition to quantify.")
    parser.add_argument("--out-json", default=None, help="Distribution JSON path (default: <input>.distribution.json).")
    parser.add_argument("--out-md", default=None, help="Markdown report path (default: <input>.report.md).")
    parser.add_argument("--sample", type=int, default=0, help="Also emit a stratified audit worksheet of N failures.")
    parser.add_argument("--seed", type=int, default=1, help="Sampling seed (deterministic).")
    parser.add_argument("--worksheet", default=None, help="Worksheet path (default: <input>.audit_worksheet.md).")
    args = parser.parse_args(argv)

    src = Path(args.input)
    if not src.exists():
        print(f"input not found: {src} — run Experiment C/B first (S28 steps 1-2)", file=sys.stderr)
        return 2
    try:
        payload = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read input: {exc}", file=sys.stderr)
        return 2

    rows = iter_records(payload, condition=args.condition)
    if not rows:
        print("no quantifiable rows (missing condition or empty file)", file=sys.stderr)
        return 2
    result = aggregate(rows)
    result["condition"] = args.condition
    result["input"] = str(src)

    out_json = Path(args.out_json) if args.out_json else src.with_suffix(".distribution.json")
    out_md = Path(args.out_md) if args.out_md else src.with_suffix(".report.md")
    out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    out_md.write_text(render_markdown(result, args.condition), encoding="utf-8")
    top = result["ranked_categories"][0] if result["ranked_categories"] else "-"
    print(f"quantified {result['n_failures']}/{result['n_questions']} failures (condition={args.condition}) top={top}")
    print(f"wrote {out_json} + {out_md}")
    if args.sample > 0:
        entries = sample_worksheet(rows, n_total=args.sample, seed=args.seed)
        out_ws = Path(args.worksheet) if args.worksheet else src.with_suffix(".audit_worksheet.md")
        out_ws.write_text(render_worksheet(entries), encoding="utf-8")
        print(f"wrote {out_ws} ({len(entries)} audit entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

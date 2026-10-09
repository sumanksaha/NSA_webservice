"""Measure the evaluator-v3 component rule against the stored per-arm answers.

``evaluator_v3_component_rule.py`` proved a threshold cannot separate evaluator
misses from genuinely-wrong answers (precision peaks at 0.41 and degrades), and
specified a component rule without measuring it — because the tabulation stores
only aggregate machine scores.

The answer text *does* exist: ``full_review_worksheet_completed.md`` carries all
150 packets with all four arms' full answers, including their ``[n]`` citation
markers.  This module parses that, recomputes the rubric components, and
measures whether the component rule actually recovers the misses.

Ground truth for "did it work" is the human verdict in the tabulation, not the
machine score — that is the whole point of the exercise.

Usage:
    python -m evaluation.v3_component_rescore
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

WORKSHEET = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "full_review_worksheet_completed.md"
TABULATION = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "full_review_tabulation.json"
OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "v3_component_rescore.json"

ARMS = ("C-O3", "D2", "D3", "E1")

#: Packet headers are markdown h3 ("### Q004 - EASY | ..."), not h2.
_QID_RE = re.compile(r"^###\s+(Q\d{3})\b", re.MULTILINE)
_ARM_RE = re.compile(r"^\*\*[A-D]\.\s*([A-Za-z0-9-]+):\*\*", re.MULTILINE)


def parse_worksheet(path: Path) -> dict[str, dict[str, str]]:
    """Qid -> {arm: answer_text} from the completed worksheet.

    Answer bodies sit in fenced blocks directly under each arm header.  The
    split is header-driven (not fence-count-driven) so a packet with a missing
    or extra block cannot desynchronise every later question.
    """
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # Locate packet starts
    starts = [(i, m.group(1)) for i, line in enumerate(lines) if (m := _QID_RE.match(line))]
    out: dict[str, dict[str, str]] = {}
    for idx, (start, qid) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        block = lines[start:end]
        arms: dict[str, str] = {}
        cur_arm: str | None = None
        buf: list[str] = []
        in_fence = False
        for line in block:
            if not in_fence and (m := _ARM_RE.match(line)):
                if cur_arm and buf:
                    arms[cur_arm] = "\n".join(buf).strip()
                cur_arm = m.group(1)
                buf = []
                in_fence = False
                continue
            if line.strip().startswith("```"):
                if not in_fence:
                    in_fence = True
                else:
                    in_fence = False
                    if cur_arm is not None:
                        arms[cur_arm] = "\n".join(buf).strip()
                        buf = []
                continue
            if in_fence:
                buf.append(line)
        if cur_arm and buf and cur_arm not in arms:
            arms[cur_arm] = "\n".join(buf).strip()
        if arms:
            out[qid] = arms
    return out


def _components_for(answer: str, qid: str) -> dict[str, Any]:
    """Recompute rubric components for one answer using grading.grade_answer.

    Uses a thin shim for the evidence inputs the grader needs but the
    worksheet does not carry (evidence text / payload index), passing empty
    structures so the hallucination check degrades to "no evidence to
    contradict" rather than raising.  That limitation is reported, not hidden.
    """
    from evaluation.benchmark import load_questions
    from evaluation.grading import grade_answer
    from evaluation.resolution import FamilyMap

    questions = {q.question_id: q for q in load_questions()}
    q = questions.get(qid)
    if q is None:
        return {}
    try:
        return grade_answer(
            q,
            answer,
            cited_chunk_ids=[],  # worksheet has [n] markers, not chunk ids
            evidence_chunk_ids=[],
            evidence_texts={},
            payload_index={},
            family_map=FamilyMap(),
        )
    except Exception as exc:  # a grader crash must not abort the rescore
        return {"error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    if not WORKSHEET.exists() or not TABULATION.exists():
        print(f"missing input: {WORKSHEET if not WORKSHEET.exists() else TABULATION}", file=sys.stderr)
        return 1

    answers = parse_worksheet(WORKSHEET)
    tab = json.loads(TABULATION.read_text(encoding="utf-8"))
    records = {r["qid"]: r for r in tab.get("records") or []}

    # Only the questions the audit actually judged.
    judged = {q: r for q, r in records.items() if r.get("verdict")}
    per_q: list[dict[str, Any]] = []
    errors: list[str] = []

    for qid, rec in sorted(judged.items()):
        arms = answers.get(qid)
        if not arms:
            continue
        comp: dict[str, Any] = {}
        for arm in ARMS:
            text = arms.get(arm)
            if not text:
                continue
            c = _components_for(text, qid)
            if c.get("error"):
                errors.append(f"{qid}/{arm}: {c['error']}")
                continue
            comp[arm] = c
        if comp:
            per_q.append({"qid": qid, "verdict": rec["verdict"], "components": comp})

    if not per_q:
        print("no components computed — worksheet parse produced nothing", file=sys.stderr)
        return 1

    def evaluate(rule) -> dict[str, Any]:
        """Apply *rule* per question (any arm satisfying it -> credit)."""
        tp = fp = fn = tn = 0
        for row in per_q:
            human_right = row["verdict"] == "evaluator_miss"  # machine said wrong, human said right
            predicted = any(rule(c) for c in row["components"].values())
            if predicted and human_right:
                tp += 1
            elif predicted and not human_right:
                fp += 1
            elif not predicted and human_right:
                fn += 1
            else:
                tn += 1
        rec = tp / max(tp + fn, 1)
        prec = tp / max(tp + fp, 1)
        return {
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "true_negative": tn,
            "recovery_rate": round(rec, 4),
            "precision": round(prec, 4),
            "f1": round(2 * rec * prec / max(rec + prec, 1e-9), 4),
        }

    rules = {
        "soft_threshold_0.5(v1 baseline)": lambda c: bool(c.get("score", 0) >= 1 and not c.get("critical_error")),
        "proposed_v3": lambda c: bool(
            c.get("provision_correct") or (c.get("legal_correct") and not c.get("hallucination_detected")),
        ),
        "provision_only": lambda c: bool(c.get("provision_correct")),
        "legal_only": lambda c: bool(c.get("legal_correct")),
    }

    results = {name: evaluate(fn) for name, fn in rules.items()}
    out = {
        "version": "v3-component-rescore-1",
        "date": "2026-10-04",
        "sources": {"worksheet": str(WORKSHEET), "tabulation": str(TABULATION)},
        "n_questions_scored": len(per_q),
        "n_component_errors": len(errors),
        "ground_truth": "human verdict from full_review_tabulation (evaluator_miss = machine wrong, human right)",
        "limitations": [
            "Cited [n] markers in the worksheet are prompt positions, not chunk "
            "ids, so provision/citation identity is NOT recoverable here — those "
            "components fall back to evidence-free behaviour and "
            "'proposed_v3' is effectively legal_correct-based. Measuring the true "
            "provision-identity rule needs a re-run that persists chunk ids.",
            "evidence_texts is empty, so hallucination_detected cannot be "
            "validated against evidence and will be conservative.",
        ],
        "rules": results,
        "errors_sample": errors[:10],
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 78)
    print(f"v3 component rescore — {len(per_q)} questions ({len(errors)} component errors)")
    print("=" * 78)
    print(f"{'rule':<34} {'recov':>7} {'prec':>7} {'F1':>7} {'TP':>4} {'FP':>4} {'FN':>4}")
    print("-" * 72)
    for name, r in results.items():
        print(
            f"{name:<34} {r['recovery_rate']:>7.3f} {r['precision']:>7.3f} {r['f1']:>7.3f} "
            f"{r['true_positive']:>4} {r['false_positive']:>4} {r['false_negative']:>4}",
        )
    print()
    for lim in out["limitations"]:
        print(f"  ! {lim}")
    print()
    print(f"written: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

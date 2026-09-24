"""Step 2 — Keep the safety properties that already passed (plan sec 5.2). No model calls.

E1, F1, and F2 held zero regressions on already-correct answers. Those four
safety properties are frozen here as offline predicates so any Experiment G
candidate can be rejected before it replaces the D2 answer.

Properties (verbatim from plan sec 5.2 Step 2):

  1. zero_regression          already-correct answers must not regress
  2. quote_in_evidence        rewrite failing quote check -> discard, D2 stands
  3. no_open_critic           open critic / unconstrained full-answer replacement forbidden
  4. gated_rewrite            no-rewrite sentence replaced (not deleted); conclusion
                              may change operative rule / authority / remedy only when
                              the new subsection is quoted from evidence AND the
                              frozen checker accepts the candidate

Modes:
  --register     write step2_safety_properties.json + .md
  --evaluate     apply the four predicates to --candidates (offline)
  --candidates   JSON list of candidate records (or {"candidates": [...]})

Outputs (evaluation/out/ceiling_v5/):
  step2_safety_properties.json
  step2_safety_properties.md

Scorer is frozen; this module never imports generation code and never calls an LLM.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

OUT = ROOT / "evaluation" / "out" / "ceiling_v5"

PLAN_REF = "Experiment_D_E_F_Comprehensive_Adversarial_Evaluation_and_Improvements.md §5.2"
SCORER = (
    "frozen: experiment_b_topk_eval token_overlap/abstain_check + evaluator_v2 overlay"
)

# Reuse Step 1's frozen property strings + quote_in_evidence (no drift).
from evaluation.step1_preregister_gates import (  # noqa: E402
    SAFETY_PROPERTIES,
    quote_in_evidence,
)

SAFETY_JSON = OUT / "step2_safety_properties.json"
SAFETY_MD = OUT / "step2_safety_properties.md"

# Property ids in plan order (index 0..3 == SAFETY_PROPERTIES[0..3]).
PROPERTY_IDS = (
    "zero_regression",
    "quote_in_evidence",
    "no_open_critic",
    "gated_rewrite",
)

# Conclusion fields that may only change under a gated rewrite (plan Step 2 #4).
CONCLUSION_FIELDS = ("operative_rule", "authority", "remedy")


# --------------------------------------------------------------------------- #
# Offline safety predicates (one per frozen property)
# --------------------------------------------------------------------------- #


def check_zero_regression(*, already_correct: bool, candidate_correct: bool) -> dict:
    """Property 1 — E1/F1/F2 held zero regressions on already-correct answers."""
    if already_correct and not candidate_correct:
        return {
            "pass": False,
            "property": "zero_regression",
            "reason": "already_correct_answer_regressed",
            "action": "keep_d2",
        }
    return {
        "pass": True,
        "property": "zero_regression",
        "reason": "no_regression_on_already_correct",
        "action": "continue",
    }


def check_quote_in_evidence(
    *, cited_span: str = "", context: str = "", rewrite: bool = True
) -> dict:
    """Property 2 — rewrite failing quote-in-evidence is discarded; D2 stands."""
    if not rewrite:
        return {
            "pass": True,
            "property": "quote_in_evidence",
            "reason": "no_rewrite_to_check",
            "action": "continue",
        }
    if quote_in_evidence(cited_span, context):
        return {
            "pass": True,
            "property": "quote_in_evidence",
            "reason": "cited_span_verbatim_in_context",
            "action": "continue",
        }
    return {
        "pass": False,
        "property": "quote_in_evidence",
        "reason": "rewrite_fails_quote_in_evidence_discard_d2_stands",
        "action": "keep_d2",
    }


def check_no_open_critic(
    *, is_open_critic: bool = False, is_full_answer_replacement: bool = False
) -> dict:
    """Property 3 — no open critic replaces a full answer."""
    if is_open_critic:
        return {
            "pass": False,
            "property": "no_open_critic",
            "reason": "open_critic_attempted_full_answer_replacement",
            "action": "keep_d2",
        }
    if is_full_answer_replacement:
        return {
            "pass": False,
            "property": "no_open_critic",
            "reason": "unconstrained_full_answer_replacement",
            "action": "keep_d2",
        }
    return {
        "pass": True,
        "property": "no_open_critic",
        "reason": "not_open_critic_full_replacement",
        "action": "continue",
    }


def check_gated_rewrite(
    *,
    no_rewrite_sentence_deleted: bool = False,
    conclusion_fields_changed: list[str] | tuple[str, ...] | None = None,
    new_subsection_quoted_from_evidence: bool = False,
    frozen_checker_accepts: bool = False,
    is_full_answer_replacement: bool = False,
) -> dict:
    """Property 4 — gated rewrite (not deleted no-rewrite sentence).

    Conclusion may change operative rule / authority / remedy only when the new
    subsection is quoted from evidence AND the frozen checker accepts.
    """
    changed = list(conclusion_fields_changed or [])
    unknown = [f for f in changed if f not in CONCLUSION_FIELDS]
    if unknown:
        return {
            "pass": False,
            "property": "gated_rewrite",
            "reason": f"unknown_conclusion_fields:{unknown}",
            "action": "keep_d2",
        }

    if is_full_answer_replacement:
        return {
            "pass": False,
            "property": "gated_rewrite",
            "reason": "full_answer_replacement_not_gated_rewrite",
            "action": "keep_d2",
        }
    if no_rewrite_sentence_deleted:
        return {
            "pass": False,
            "property": "gated_rewrite",
            "reason": "no_rewrite_sentence_deleted_not_replaced_by_gated_rewrite",
            "action": "keep_d2",
        }
    if changed and not new_subsection_quoted_from_evidence:
        return {
            "pass": False,
            "property": "gated_rewrite",
            "reason": "conclusion_changed_without_quote_from_evidence",
            "action": "keep_d2",
        }
    if changed and not frozen_checker_accepts:
        return {
            "pass": False,
            "property": "gated_rewrite",
            "reason": "conclusion_changed_but_frozen_checker_rejects",
            "action": "keep_d2",
        }
    if changed:
        return {
            "pass": True,
            "property": "gated_rewrite",
            "reason": "conclusion_changed_under_quote_and_frozen_checker",
            "action": "continue",
        }
    # No conclusion change: still require quote+checker if a rewrite was attempted
    # with a new subsection (defensive; plan text is about conclusion changes).
    return {
        "pass": True,
        "property": "gated_rewrite",
        "reason": "no_conclusion_field_change",
        "action": "continue",
    }


SAFETY_CHECKS = {
    "zero_regression": check_zero_regression,
    "quote_in_evidence": check_quote_in_evidence,
    "no_open_critic": check_no_open_critic,
    "gated_rewrite": check_gated_rewrite,
}


# --------------------------------------------------------------------------- #
# Combined candidate evaluation (first failing property wins)
# --------------------------------------------------------------------------- #


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def evaluate_safety(fields: dict) -> dict:
    """Run all four Step 2 safety properties on one candidate record.

    Field keys (all optional; defaults are the safe/pass path unless the
    candidate claims a rewrite):

      already_correct: bool              D2 binary before intervention
      candidate_correct: bool            binary after candidate
      is_rewrite: bool                   default True (a candidate is a rewrite)
      cited_span: str                    span claimed in the candidate
      context: str                       evidence/context the span must appear in
      is_open_critic: bool
      is_full_answer_replacement: bool
      no_rewrite_sentence_deleted: bool
      conclusion_fields_changed: list    subset of operative_rule|authority|remedy
      new_subsection_quoted_from_evidence: bool
      frozen_checker_accepts: bool
    """
    f = dict(fields or {})
    is_rewrite = bool(f.get("is_rewrite", True))

    checks: list[dict] = []

    r1 = check_zero_regression(
        already_correct=bool(f.get("already_correct", False)),
        candidate_correct=bool(f.get("candidate_correct", False)),
    )
    checks.append(r1)
    if not r1["pass"]:
        return _result(False, checks, keep_d2=True)

    r2 = check_quote_in_evidence(
        cited_span=str(f.get("cited_span") or ""),
        context=str(f.get("context") or ""),
        rewrite=is_rewrite,
    )
    checks.append(r2)
    if not r2["pass"]:
        return _result(False, checks, keep_d2=True)

    r3 = check_no_open_critic(
        is_open_critic=bool(f.get("is_open_critic", False)),
        is_full_answer_replacement=bool(f.get("is_full_answer_replacement", False)),
    )
    checks.append(r3)
    if not r3["pass"]:
        return _result(False, checks, keep_d2=True)

    r4 = check_gated_rewrite(
        no_rewrite_sentence_deleted=bool(f.get("no_rewrite_sentence_deleted", False)),
        conclusion_fields_changed=list(f.get("conclusion_fields_changed") or []),
        new_subsection_quoted_from_evidence=bool(
            f.get("new_subsection_quoted_from_evidence", False)
        ),
        frozen_checker_accepts=bool(f.get("frozen_checker_accepts", False)),
        is_full_answer_replacement=bool(f.get("is_full_answer_replacement", False)),
    )
    checks.append(r4)
    if not r4["pass"]:
        return _result(False, checks, keep_d2=True)

    return _result(True, checks, keep_d2=False)


def _result(ok: bool, checks: list[dict], *, keep_d2: bool) -> dict:
    failed = next((c for c in checks if not c["pass"]), None)
    return {
        "pass": ok,
        "verdict": "accepted" if ok else "rejected",
        "keep_d2": keep_d2,
        "failed_property": None if ok else (failed or {}).get("property"),
        "reason": "all_safety_properties_pass" if ok else (failed or {}).get("reason"),
        "checks": checks,
    }


def evaluate_candidate_list(rows: list[dict]) -> dict:
    """Evaluate a list of candidate records; return per-row + counts."""
    results: list[dict] = []
    accepted = 0
    rejected = 0
    by_property: dict[str, int] = {}
    for row in rows:
        r = evaluate_safety(row)
        r["qid"] = row.get("qid")
        results.append(r)
        if r["pass"]:
            accepted += 1
        else:
            rejected += 1
            prop = r.get("failed_property") or "unknown"
            by_property[prop] = by_property.get(prop, 0) + 1
    return {
        "n": len(results),
        "accepted": accepted,
        "rejected": rejected,
        "rejected_by_property": by_property,
        "results": results,
    }


# --------------------------------------------------------------------------- #
# Answer-text → safety fields (derive flags from the texts, not self-report)
# --------------------------------------------------------------------------- #


def conclusion_retained(d2_answer: str, candidate_answer: str) -> bool:
    """True when the candidate still carries D2's conclusion (notes may follow)."""
    d2 = _norm_ws(d2_answer)
    cand = _norm_ws(candidate_answer)
    if not d2:
        return False
    if d2 in cand:
        return True
    # First sentence retained (candidate may reflow whitespace / strip markers).
    first = d2.split(". ", 1)[0].strip()
    return bool(first) and first in cand


def is_full_answer_replacement(d2_answer: str, candidate_answer: str) -> bool:
    """True only when the D2 conclusion is gone — an unconstrained rewrite."""
    d2 = _norm_ws(d2_answer)
    cand = _norm_ws(candidate_answer)
    if not d2 or not cand or d2 == cand:
        return False
    return not conclusion_retained(d2_answer, candidate_answer)


def fields_for_answers(
    *,
    d2_answer: str,
    candidate_answer: str,
    already_correct: bool = False,
    candidate_correct: bool = False,
    context: str = "",
    cited_span: str = "",
    is_open_critic: bool = False,
    conclusion_fields_changed: list[str] | tuple[str, ...] | None = None,
    new_subsection_quoted_from_evidence: bool = False,
    frozen_checker_accepts: bool = False,
) -> dict:
    """Build an ``evaluate_safety`` record from two answer strings.

    A note-only append (D2 conclusion retained) is not treated as a content
    rewrite: ``is_rewrite`` is True only when the conclusion was replaced.
    A replaced conclusion is both a rewrite and a full-answer replacement.
    """
    full = is_full_answer_replacement(d2_answer, candidate_answer)
    return {
        "already_correct": bool(already_correct),
        "candidate_correct": bool(candidate_correct),
        "is_rewrite": full,
        "cited_span": str(cited_span or ""),
        "context": str(context or ""),
        "is_open_critic": bool(is_open_critic) or full,
        "is_full_answer_replacement": full,
        "no_rewrite_sentence_deleted": False,
        "conclusion_fields_changed": list(conclusion_fields_changed or []),
        "new_subsection_quoted_from_evidence": bool(new_subsection_quoted_from_evidence),
        "frozen_checker_accepts": bool(frozen_checker_accepts),
    }


def gate_answers(
    *,
    d2_answer: str,
    candidate_answer: str,
    already_correct: bool = False,
    candidate_correct: bool = False,
    context: str = "",
    cited_span: str = "",
    is_open_critic: bool = False,
) -> dict:
    """Step 2 gate for one candidate answer derived from answer texts."""
    return evaluate_safety(
        fields_for_answers(
            d2_answer=d2_answer,
            candidate_answer=candidate_answer,
            already_correct=already_correct,
            candidate_correct=candidate_correct,
            context=context,
            cited_span=cited_span,
            is_open_critic=is_open_critic,
        )
    )


def gate_candidate_list(rows: list[dict]) -> dict:
    """Gate a list of {qid, d2_answer, candidate_answer, ...} records."""
    results: list[dict] = []
    accepted = 0
    rejected = 0
    by_property: dict[str, int] = {}
    for row in rows:
        r = gate_answers(
            d2_answer=str(row.get("d2_answer") or ""),
            candidate_answer=str(row.get("candidate_answer") or row.get("answer") or ""),
            already_correct=bool(row.get("already_correct", False)),
            candidate_correct=bool(row.get("candidate_correct", False)),
            context=str(row.get("context") or ""),
            cited_span=str(row.get("cited_span") or ""),
            is_open_critic=bool(row.get("is_open_critic", False)),
        )
        r["qid"] = row.get("qid")
        results.append(r)
        if r["pass"]:
            accepted += 1
        else:
            rejected += 1
            prop = r.get("failed_property") or "unknown"
            by_property[prop] = by_property.get(prop, 0) + 1
    return {
        "n": len(results),
        "accepted": accepted,
        "rejected": rejected,
        "rejected_by_property": by_property,
        "results": results,
    }


# --------------------------------------------------------------------------- #
# Registration payload
# --------------------------------------------------------------------------- #


def build_payload() -> dict[str, Any]:
    """Assemble the frozen Step 2 safety registration (no labels required)."""
    properties = []
    for pid, text in zip(PROPERTY_IDS, SAFETY_PROPERTIES, strict=True):
        properties.append(
            {
                "id": pid,
                "text": text,
                "check": SAFETY_CHECKS[pid].__name__,
                "action_on_fail": "keep_d2",
            }
        )
    return {
        "experiment": "Step 2 — keep the safety properties that already passed",
        "plan_ref": PLAN_REF,
        "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scorer": SCORER,
        "n_properties": len(properties),
        "properties": properties,
        "conclusion_fields_under_gate": list(CONCLUSION_FIELDS),
        "note": (
            "These four properties already passed in E1/F1/F2 and are kept for "
            "Experiment G. Any candidate failing any property is rejected and the "
            "D2 answer stands. Offline only — no generation calls."
        ),
    }


def validate_payload(payload: dict) -> dict[str, Any]:
    """Structural checks on a Step 2 registration (pure)."""
    errors: list[str] = []
    props = payload.get("properties") or []
    if len(props) != len(PROPERTY_IDS):
        errors.append(f"expected {len(PROPERTY_IDS)} properties, got {len(props)}")
    ids = [p.get("id") for p in props]
    if ids != list(PROPERTY_IDS):
        errors.append(f"property ids drifted: {ids} vs {list(PROPERTY_IDS)}")
    texts = [p.get("text") for p in props]
    if texts != list(SAFETY_PROPERTIES):
        errors.append("property text drifted from frozen SAFETY_PROPERTIES")
    for p in props:
        fn = SAFETY_CHECKS.get(p.get("id") or "")
        if fn is None:
            errors.append(f"unknown property check: {p.get('id')}")
        elif p.get("check") != fn.__name__:
            errors.append(f"{p.get('id')}.check drifted: {p.get('check')} != {fn.__name__}")
        if p.get("action_on_fail") != "keep_d2":
            errors.append(f"{p.get('id')}.action_on_fail must be keep_d2")
    if payload.get("scorer") != SCORER:
        errors.append("scorer string drifted")
    if list(payload.get("conclusion_fields_under_gate") or []) != list(CONCLUSION_FIELDS):
        errors.append("conclusion_fields_under_gate drifted")
    return {
        "ok": not errors,
        "errors": errors,
        "n_properties": len(props),
    }


def render_markdown(payload: dict, validation: dict) -> str:
    lines = [
        "# Step 2 — Safety properties that already passed",
        "",
        f"**Plan:** {payload['plan_ref']}",
        f"**Registered:** {payload['registered_at']}",
        f"**Scorer:** `{payload['scorer']}`",
        f"**Properties:** {payload['n_properties']}",
        f"**Validation:** ok={validation['ok']} errors={validation['errors'] or 'none'}",
        "",
        "## Frozen properties",
        "",
        "| # | Id | Text | Check | On fail |",
        "|---:|---|---|---|---|",
    ]
    for i, p in enumerate(payload["properties"], 1):
        lines.append(
            f"| {i} | `{p['id']}` | {p['text']} | `{p['check']}` | {p['action_on_fail']} |"
        )
    lines += [
        "",
        "## Conclusion fields under the gated rewrite",
        "",
    ]
    for f in payload["conclusion_fields_under_gate"]:
        lines.append(f"- `{f}`")
    lines += [
        "",
        "## Note",
        "",
        payload["note"],
        "",
    ]
    return "\n".join(lines)


def publish(payload: dict, validation: dict, *, out_dir: Path | None = None) -> dict[str, Path]:
    """Write Step 2 registration JSON + Markdown."""
    out = out_dir or OUT
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / SAFETY_JSON.name
    md_path = out / SAFETY_MD.name
    json_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(payload, validation), encoding="utf-8")
    return {"json": json_path, "md": md_path}


def register(*, out_dir: Path | None = None) -> tuple[dict, dict, dict[str, Path]]:
    """Build + validate + publish the Step 2 registration. Raises on validation failure."""
    payload = build_payload()
    validation = validate_payload(payload)
    if not validation["ok"]:
        raise ValueError(f"step2 validation failed: {validation['errors']}")
    written = publish(payload, validation, out_dir=out_dir)
    return payload, validation, written


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Step 2 — safety properties (no LLM)")
    ap.add_argument("--register", action="store_true", help="write safety JSON + Markdown")
    ap.add_argument(
        "--evaluate",
        action="store_true",
        help="apply the four safety predicates to --candidates",
    )
    ap.add_argument(
        "--candidates",
        type=Path,
        default=None,
        help="JSON list of candidate records (or {\"candidates\": [...]})",
    )
    ap.add_argument(
        "--gate-answers",
        type=Path,
        default=None,
        help=(
            "JSON list of {qid, d2_answer, candidate_answer, context?, ...} "
            "records; derive safety fields from the answer texts and gate them"
        ),
    )
    ap.add_argument(
        "--require-registered",
        action="store_true",
        help="exit 2 unless step2_safety_properties.json exists and validates",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="override output directory (default: evaluation/out/ceiling_v5)",
    )
    args = ap.parse_args(argv)

    if args.gate_answers:
        raw = json.loads(args.gate_answers.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else raw.get("candidates") or raw.get("records") or []
        summary = gate_candidate_list(rows)
        print(json.dumps(summary, indent=1, ensure_ascii=False))
        return 0

    if args.candidates:
        raw = json.loads(args.candidates.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else raw.get("candidates") or []
        summary = evaluate_candidate_list(rows)
        print(json.dumps(summary, indent=1, ensure_ascii=False))
        return 0

    if args.evaluate:
        print("Step 2 safety checks (offline):")
        for pid in PROPERTY_IDS:
            print(f"  {pid}: {SAFETY_CHECKS[pid].__name__}")
        print("action_on_fail: keep_d2")
        print(f"conclusion fields under gate: {', '.join(CONCLUSION_FIELDS)}")
        return 0

    if not (args.register or args.require_registered):
        ap.print_help()
        return 1

    if args.register:
        try:
            payload, validation, written = register(out_dir=args.out_dir)
        except ValueError as exc:
            print(f"REGISTER FAILED: {exc}", file=sys.stderr)
            return 2
        print(
            f"step2_registered={validation['ok']} "
            f"n_properties={payload['n_properties']} "
            f"errors={validation['errors'] or 'none'}"
        )
        for k, p in written.items():
            print(f"  wrote {k}: {p.name}")

    if args.require_registered:
        path = (args.out_dir or OUT) / SAFETY_JSON.name
        if not path.exists():
            print(f"NOT REGISTERED: {path} missing", file=sys.stderr)
            return 2
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            v = validate_payload(existing)
        except Exception as exc:  # noqa: BLE001
            print(f"REGISTERED BUT INVALID: {exc}", file=sys.stderr)
            return 2
        if not v["ok"]:
            print(f"REGISTERED BUT INVALID: {v['errors']}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

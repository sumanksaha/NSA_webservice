"""Step 1 — Pre-register one gate per label (plan sec 5.2). No model calls.

Freezes the three-label intervention table *before* any Experiment G run, so
keep/reject decisions cannot drift after results are seen.

Gates (verbatim from plan sec 5.2 Step 1):

  evidence_missing  corpus fill + re-retrieve qid only + 1 answer call
  model_wrong       one contrastive span-cut call behind quote-in-evidence
  reference_narrow  dual-score / fix reference; never spend a generation call

Also freezes Step 2 safety properties and Step 3 budget rules into the same
preregistration artifact so a later run can only claim what was registered.

Modes:
  --register     write step1_preregistered_gates.json + .md (gate table always;
                 qid assignment only if Step 0 labels are complete)
  --assign-qids  Step 1b: require complete Step 0 labels, partition residual
                 qids into the three gates, rewrite all gate target files
  --evaluate     apply registered gates to candidate records (offline predicates)
  --require-complete  exit 2 unless Step 0 is complete AND gates are registered

Outputs (evaluation/out/ceiling_v5/):
  step1_preregistered_gates.json
  step1_preregistered_gates.md
  step1_gate_targets.json
  step1_qid_assignment.json           (written by --assign-qids)

Scorer is frozen; this module never imports generation code and never calls an LLM.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

OUT = ROOT / "evaluation" / "out" / "ceiling_v5"

# Reuse Step 0 enum + label loaders (no generation imports).
from evaluation.step0_label_residual import (  # noqa: E402
    STEP0_ENUM,
    load_residual_qids,
    validate_labels,
)

STEP1_LABELS = STEP0_ENUM
SCORER = (
    "frozen: experiment_b_topk_eval token_overlap/abstain_check + evaluator_v2 overlay"
)
BUDGET_CAP = 150  # successful generations only; transport failures outside cap (plan 5.2 Step 3)
PLAN_REF = "Experiment_D_E_F_Comprehensive_Adversarial_Evaluation_and_Improvements.md §5.2"

PREREG_JSON = OUT / "step1_preregistered_gates.json"
PREREG_MD = OUT / "step1_preregistered_gates.md"
GATE_TARGETS = OUT / "step1_gate_targets.json"
ASSIGNMENT_JSON = OUT / "step1_qid_assignment.json"

# --------------------------------------------------------------------------- #
# Pre-registered gate table (plan sec 5.2 Step 1) — frozen strings
# --------------------------------------------------------------------------- #

GATE_TABLE: dict[str, dict[str, str]] = {
    "evidence_missing": {
        "intervention": (
            "Add the missing instrument text to the corpus (Water Act section bodies, "
            "WB Meat Order, KMC water rules, PCA Rules schedules). "
            "Re-retrieve those question ids only. One answer call on the new payload."
        ),
        "keep_if": (
            "The gold span is now in the payload, and binary correctness on that id rises."
        ),
        "reject_if": (
            "The section is not in the index. Stop. Do not loop retrieval."
        ),
        "target_file": "step0_corpus_fill_targets.json",
        "generations": 1,
        "re_retrieve": True,
    },
    "model_wrong": {
        "intervention": (
            "One contrastive call. Options are spans cut from retrieved text, each with "
            "section id, competent authority, and the operative sentence. "
            "The model must rewrite the remedy so it matches the chosen span."
        ),
        "keep_if": (
            "The cited span is a verbatim substring of the context, and the operative "
            "sentence changed to that span's rule."
        ),
        "reject_if": (
            "The model only swaps a section number, cites a span not in context, or "
            "abstains by naming an element the new payload does not contain."
        ),
        "target_file": "step0_contrastive_targets.json",
        "generations": 1,
        "re_retrieve": False,
    },
    "reference_narrow": {
        "intervention": (
            "Change the reference or report a second score. Do not change the model."
        ),
        "keep_if": (
            "Both the old and the new score are reported until the references are fixed."
        ),
        "reject_if": "A generation call was spent to chase the narrow reference.",
        "target_file": "step0_dual_score_targets.json",
        "generations": 0,
        "re_retrieve": False,
    },
}

# Step 2 — safety properties that already passed (plan sec 5.2 Step 2)
SAFETY_PROPERTIES: list[str] = [
    "E1, F1, and F2 held zero regressions on answers that were already correct.",
    "Any rewrite that fails the quote-in-evidence check is discarded and the D2 answer stands.",
    "No open critic replaces a full answer.",
    (
        "The no-rewrite sentence in E1/F2 is replaced by a gated rewrite, not deleted: "
        "the conclusion may change the operative rule, authority, and remedy only when "
        "the new subsection is quoted from evidence and the frozen checker accepts the candidate."
    ),
]

# Step 3 — budget + reporting rules (plan sec 5.2 Step 3)
BUDGET_RULES: dict[str, Any] = {
    "cap_successful_generations": BUDGET_CAP,
    "transport_failures_outside_cap": True,
    "spend_only_on": [
        "evidence_missing ids whose text was actually added",
        "model_wrong ids whose contrastive spans were actually cut from context",
    ],
    "publish_before_aggregate": (
        "Publish per-label recovered / rejected / unchanged counts before any "
        "aggregate soft-score claim."
    ),
}

# Decision rule for the run that follows (plan sec 5.3)
DECISION_RULES: list[str] = [
    (
        "If evidence_missing recovers and model_wrong does not, the bottleneck is corpus "
        "coverage. Stop generation work and continue instrument ingestion."
    ),
    (
        "If model_wrong recovers under the quote-and-reject gate and already-correct "
        "answers do not regress, integrate that single contrastive call behind the gate."
    ),
    (
        "If both stay at zero binary flips while reference_narrow is a large share of the "
        "sample, the ceiling is the scorer. Fix references before any further prompt."
    ),
    (
        "If a candidate is produced by unconstrained full-answer replacement, discard the "
        "run. That condition is already measured."
    ),
]


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #


def load_step0_labels() -> dict[str, Any]:
    path = OUT / "step0_residual_labels.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_step0_validation() -> dict[str, Any]:
    """Re-validate residual labels from the published Step 0 file (or empty)."""
    residual = load_residual_qids()
    published = load_step0_labels()
    labels = published.get("labels") or {}
    return validate_labels(labels, residual)


def assign_qids(
    labels: dict[str, str] | None = None,
    *,
    validation0: dict[str, Any] | None = None,
    source: str = "step0_residual_labels.json",
) -> tuple[dict, dict, dict[str, Path]]:
    """Step 1b — partition residual qids into the three registered gates.

    Requires complete Step 0 labels. Returns ``(payload, validation, written)``.
    Raises ``ValueError`` if labels are incomplete or the partition is invalid.
    """
    if validation0 is None:
        validation0 = load_step0_validation()
    if labels is None:
        labels = validation0.get("labels") or {}

    if not validation0.get("step0_complete"):
        raise ValueError(
            "Step 1b requires complete Step 0 labels: "
            f"missing={validation0.get('n_missing', '?')} "
            f"invalid={validation0.get('n_invalid', 0)} "
            f"labeled={validation0.get('n_labeled_valid', 0)}/"
            f"{validation0.get('n_residual_total', '?')}"
        )

    residual = load_residual_qids()
    v_lab = validate_labels(labels, residual)
    if not v_lab.get("step0_complete"):
        raise ValueError(
            f"label validation failed: missing={v_lab['n_missing']} "
            f"invalid={v_lab['invalid_labels']}"
        )

    payload = build_gates(labels, step0_complete=True, source=source)
    validation = validate_prereg(payload)
    if not validation["ok"]:
        raise ValueError(f"prereg validation failed: {validation['errors']}")

    written = publish(payload, validation)

    # Explicit Step 1b assignment report (per-label counts + residual check).
    assignment = {
        "experiment": "Step 1b — qid assignment to pre-registered gates",
        "plan_ref": PLAN_REF,
        "assigned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scorer": SCORER,
        "step0_complete": True,
        "qid_assignment": "assigned",
        "n_residual_total": len(residual),
        "label_counts": dict(Counter(v_lab["labels"].values())),
        "buckets": {
            label: {
                "n": payload["gates"][label]["n"],
                "qids": payload["gates"][label]["qids"],
                "target_file": GATE_TABLE[label]["target_file"],
                "generations_budgeted_per_qid": GATE_TABLE[label]["generations"],
                "re_retrieve": GATE_TABLE[label]["re_retrieve"],
            }
            for label in STEP1_LABELS
        },
        "budget_cap": BUDGET_CAP,
        "source": source,
        "note": (
            "Partition is residual == disjoint union of three buckets. "
            "Spend generations only on evidence_missing (text added) and "
            "model_wrong (spans cut from context); reference_narrow spends zero."
        ),
    }
    assign_path = written.get("json", PREREG_JSON).parent / ASSIGNMENT_JSON.name
    assign_path.write_text(json.dumps(assignment, indent=1, ensure_ascii=False), encoding="utf-8")
    written["assignment"] = assign_path
    return payload, validation, written


# --------------------------------------------------------------------------- #
# Build + validate preregistration
# --------------------------------------------------------------------------- #


def build_gates(
    labels: dict[str, str] | None = None,
    *,
    step0_complete: bool,
    source: str = "none",
) -> dict[str, Any]:
    """Assemble the frozen preregistration payload.

    Gate *definitions* always register. Qid buckets attach only when Step 0 is
    complete; otherwise buckets stay empty and ``qid_assignment`` is deferred.
    """
    if labels is None:
        labels = {}
    residual = load_residual_qids()
    validation = validate_labels(labels, residual) if labels else validate_labels({}, residual)

    gates: dict[str, dict] = {}
    for label in STEP1_LABELS:
        meta = GATE_TABLE[label]
        qids: list[str] = []
        if step0_complete and validation.get("step0_complete"):
            qids = sorted(q for q, v in validation["labels"].items() if v == label)
        gates[label] = {
            "label": label,
            "intervention": meta["intervention"],
            "keep_if": meta["keep_if"],
            "reject_if": meta["reject_if"],
            "target_file": meta["target_file"],
            "generations_budgeted_per_qid": meta["generations"],
            "re_retrieve": meta["re_retrieve"],
            "n": len(qids),
            "qids": qids,
        }

    # Partition check when assigned: residual == union of three buckets, disjoint.
    assigned = step0_complete and validation.get("step0_complete")
    if assigned:
        union = set()
        for g in gates.values():
            s = set(g["qids"])
            if union & s:
                raise ValueError(f"gate qid overlap: {sorted(union & s)}")
            union |= s
        if union != set(residual):
            raise ValueError(
                f"gate partition != residual: missing={sorted(set(residual)-union)} "
                f"extra={sorted(union-set(residual))}"
            )

    return {
        "experiment": "Step 1 — pre-registered per-label gates",
        "plan_ref": PLAN_REF,
        "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scorer": SCORER,
        "enum": list(STEP1_LABELS),
        "step0_complete": bool(assigned),
        "qid_assignment": "assigned" if assigned else "deferred_pending_step0_labels",
        "source": source,
        "n_residual_total": len(residual),
        "label_counts": validation.get("label_counts") or {},
        "gates": gates,
        "safety_properties_step2": list(SAFETY_PROPERTIES),
        "budget_step3": copy.deepcopy(BUDGET_RULES),
        "decision_rules_sec5_3": list(DECISION_RULES),
        "note": (
            "Gate definitions are frozen before any Experiment G generation. "
            "Qid buckets require complete Step 0 labels. "
            "Publish per-label counts before any aggregate soft-score claim."
        ),
    }


def validate_prereg(payload: dict) -> dict[str, Any]:
    """Structural checks on a preregistration payload (pure)."""
    errors: list[str] = []
    gates = payload.get("gates") or {}
    if set(gates) != set(STEP1_LABELS):
        errors.append(f"gate labels mismatch: {sorted(gates)} vs {list(STEP1_LABELS)}")
    for label in STEP1_LABELS:
        g = gates.get(label) or {}
        for key in ("intervention", "keep_if", "reject_if"):
            if not str(g.get(key) or "").strip():
                errors.append(f"{label}.{key} empty")
        meta = GATE_TABLE.get(label) or {}
        if g.get("intervention") != meta.get("intervention"):
            errors.append(f"{label}.intervention drifted from plan table")
        if g.get("keep_if") != meta.get("keep_if"):
            errors.append(f"{label}.keep_if drifted from plan table")
        if g.get("reject_if") != meta.get("reject_if"):
            errors.append(f"{label}.reject_if drifted from plan table")
    if payload.get("budget_step3", {}).get("cap_successful_generations") != BUDGET_CAP:
        errors.append("budget cap drifted")
    if payload.get("scorer") != SCORER:
        errors.append("scorer string drifted")
    assigned = payload.get("step0_complete")
    if assigned:
        residual = set(load_residual_qids())
        union: set[str] = set()
        for label, g in gates.items():
            s = set(g.get("qids") or [])
            if union & s:
                errors.append(f"overlap in {label}: {sorted(union & s)}")
            union |= s
        if union != residual:
            errors.append("assigned qids != residual set")
    return {
        "ok": not errors,
        "errors": errors,
        "step1_complete": not errors,
        "qid_assignment": payload.get("qid_assignment"),
        "step0_complete": bool(assigned),
        "label_counts": payload.get("label_counts") or {},
    }


# --------------------------------------------------------------------------- #
# Candidate evaluation predicates (offline; used by later runs under the gate)
# --------------------------------------------------------------------------- #


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def evaluate_evidence_missing(
    *,
    gold_span_in_payload: bool,
    section_in_index: bool,
    binary_before: bool,
    binary_after: bool,
) -> dict:
    """Apply the evidence_missing gate to one candidate (plan 5.2 Step 1)."""
    if not section_in_index:
        return {
            "verdict": "rejected",
            "reason": "section_not_in_index_stop_do_not_loop_retrieval",
            "keep": False,
        }
    if not gold_span_in_payload:
        return {
            "verdict": "rejected",
            "reason": "gold_span_still_absent_from_payload",
            "keep": False,
        }
    if binary_after and not binary_before:
        return {"verdict": "kept", "reason": "gold_span_in_payload_and_binary_rose", "keep": True}
    if binary_after and binary_before:
        return {
            "verdict": "rejected",
            "reason": "binary_already_correct_before_intervention",
            "keep": False,
        }
    return {
        "verdict": "rejected",
        "reason": "gold_span_present_but_binary_did_not_rise",
        "keep": False,
    }


def evaluate_model_wrong(
    *,
    cited_span_in_context: bool,
    section_number_only_swap: bool,
    operative_sentence_changed: bool,
    abstains_on_element_absent_from_payload: bool,
) -> dict:
    """Apply the model_wrong quote-and-reject gate (plan 5.2 Steps 1–2)."""
    if abstains_on_element_absent_from_payload:
        return {
            "verdict": "rejected",
            "reason": "abstains_on_element_absent_from_new_payload",
            "keep": False,
        }
    if not cited_span_in_context:
        return {
            "verdict": "rejected",
            "reason": "cited_span_not_verbatim_substring_of_context",
            "keep": False,
        }
    if section_number_only_swap:
        return {
            "verdict": "rejected",
            "reason": "section_number_only_swap",
            "keep": False,
        }
    if not operative_sentence_changed:
        return {
            "verdict": "rejected",
            "reason": "operative_sentence_unchanged",
            "keep": False,
        }
    return {
        "verdict": "kept",
        "reason": "span_verbatim_and_operative_sentence_changed",
        "keep": True,
    }


def evaluate_reference_narrow(
    *,
    old_score_reported: bool,
    new_score_reported: bool,
    generation_call_spent: bool,
) -> dict:
    """Apply the reference_narrow dual-score gate (plan 5.2 Step 1)."""
    if generation_call_spent:
        return {
            "verdict": "rejected",
            "reason": "generation_call_spent_chasing_narrow_reference",
            "keep": False,
        }
    if old_score_reported and new_score_reported:
        return {
            "verdict": "kept",
            "reason": "both_old_and_new_score_reported",
            "keep": True,
        }
    return {
        "verdict": "rejected",
        "reason": "missing_old_or_new_score_report",
        "keep": False,
    }


EVALUATORS = {
    "evidence_missing": evaluate_evidence_missing,
    "model_wrong": evaluate_model_wrong,
    "reference_narrow": evaluate_reference_narrow,
}


def evaluate_candidate(label: str, fields: dict) -> dict:
    """Dispatch a candidate record to its pre-registered gate evaluator."""
    fn = EVALUATORS.get(label)
    if fn is None:
        return {"verdict": "rejected", "reason": f"unknown_label:{label}", "keep": False}
    known = {
        "evidence_missing": (
            "gold_span_in_payload",
            "section_in_index",
            "binary_before",
            "binary_after",
        ),
        "model_wrong": (
            "cited_span_in_context",
            "section_number_only_swap",
            "operative_sentence_changed",
            "abstains_on_element_absent_from_payload",
        ),
        "reference_narrow": (
            "old_score_reported",
            "new_score_reported",
            "generation_call_spent",
        ),
    }[label]
    kwargs = {k: bool(fields.get(k, False)) for k in known}
    out = fn(**kwargs)
    out["label"] = label
    return out


def quote_in_evidence(cited_span: str, context: str) -> bool:
    """Verbatim (whitespace-normalized) substring check — Step 2 safety gate."""
    if not cited_span or not context:
        return False
    return _norm_ws(cited_span) in _norm_ws(context)


# --------------------------------------------------------------------------- #
# Publish
# --------------------------------------------------------------------------- #


def render_markdown(payload: dict, validation: dict) -> str:
    lines = [
        "# Step 1 — Pre-registered gates",
        "",
        f"**Plan:** {payload['plan_ref']}",
        f"**Registered:** {payload['registered_at']}",
        f"**Scorer:** `{payload['scorer']}`",
        f"**Step 0 complete:** {payload['step0_complete']} "
        f"({payload['qid_assignment']})",
        f"**Label counts:** `{payload['label_counts']}`",
        f"**Budget cap (successful generations):** {BUDGET_CAP}",
        "",
        "## Gates",
        "",
        "| Label | Intervention | Keep only if | Reject if | n |",
        "|---|---|---|---|---:|",
    ]
    for label in STEP1_LABELS:
        g = payload["gates"][label]
        lines.append(
            f"| `{label}` | {g['intervention']} | {g['keep_if']} | {g['reject_if']} | {g['n']} |"
        )
    lines += ["", "## Step 2 — Safety properties", ""]
    for s in SAFETY_PROPERTIES:
        lines.append(f"- {s}")
    lines += ["", "## Step 3 — Budget", ""]
    lines.append(f"- Cap successful generations: **{BUDGET_CAP}**")
    lines.append("- Transport failures stay outside the cap.")
    for s in BUDGET_RULES["spend_only_on"]:
        lines.append(f"- Spend only on: {s}")
    lines.append(f"- {BUDGET_RULES['publish_before_aggregate']}")
    lines += ["", "## Decision rules (§5.3)", ""]
    for r in DECISION_RULES:
        lines.append(f"- {r}")
    lines += [
        "",
        "## Validation",
        "",
        f"- ok: **{validation['ok']}**",
        f"- errors: {validation['errors'] or 'none'}",
        "",
    ]
    return "\n".join(lines)


def publish(
    payload: dict,
    validation: dict,
    *,
    out_dir: Path | None = None,
) -> dict[str, Path]:
    """Write prereg JSON + Markdown (+ gate targets when assigned)."""
    out = out_dir or OUT
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / PREREG_JSON.name
    md_path = out / PREREG_MD.name
    json_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(payload, validation), encoding="utf-8")
    written = {"json": json_path, "md": md_path}

    # Per-label target files (same schema as Step 0) when qids are assigned.
    targets_path = out / GATE_TARGETS.name
    buckets = {
        label: {"n": payload["gates"][label]["n"], "qids": payload["gates"][label]["qids"]}
        for label in STEP1_LABELS
    }
    targets_path.write_text(
        json.dumps(
            {
                "step0_complete": payload["step0_complete"],
                "qid_assignment": payload["qid_assignment"],
                "buckets": buckets,
                "budget_cap": BUDGET_CAP,
            },
            indent=1,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    written["targets"] = targets_path

    # Mirror the three step0_*_targets.json files so downstream readers stay in sync.
    if payload["step0_complete"]:
        for label in STEP1_LABELS:
            meta = GATE_TABLE[label]
            path = out / meta["target_file"]
            path.write_text(
                json.dumps(
                    {
                        "label": label,
                        "n": payload["gates"][label]["n"],
                        "qids": payload["gates"][label]["qids"],
                        "intervention": meta["intervention"],
                        "keep_if": meta["keep_if"],
                        "reject_if": meta["reject_if"],
                        "source": "step1_preregistered_gates",
                    },
                    indent=1,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            written[f"targets_{label}"] = path
    return written


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Step 1 — pre-register per-label gates (no LLM)")
    ap.add_argument("--register", action="store_true", help="write prereg JSON + Markdown")
    ap.add_argument(
        "--assign-qids",
        action="store_true",
        help=(
            "Step 1b: require complete Step 0 labels, partition residual qids "
            "into the three gates, rewrite all gate target files"
        ),
    )
    ap.add_argument(
        "--evaluate",
        action="store_true",
        help="print gate evaluators usage summary (offline predicates)",
    )
    ap.add_argument(
        "--candidates",
        type=Path,
        default=None,
        help="JSON list of {qid, label, ...fields} to evaluate under registered gates",
    )
    ap.add_argument(
        "--require-complete",
        action="store_true",
        help="exit 2 unless Step 0 labels are complete and gates register cleanly",
    )
    ap.add_argument(
        "--allow-incomplete-step0",
        action="store_true",
        help="register gate definitions even when Step 0 labels are incomplete",
    )
    ap.add_argument(
        "--labels",
        type=Path,
        default=None,
        help="optional labels JSON to use instead of step0_residual_labels.json (for assign)",
    )
    args = ap.parse_args(argv)

    validation0 = load_step0_validation()
    if args.labels:
        raw = json.loads(args.labels.read_text(encoding="utf-8"))
        from evaluation.step0_label_residual import normalize_labels

        residual = load_residual_qids()
        parsed = normalize_labels(raw, residual)
        validation0 = validate_labels(parsed, residual)
    step0_ok = bool(validation0.get("step0_complete"))
    labels = validation0.get("labels") or {}

    if args.candidates:
        raw = json.loads(args.candidates.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else raw.get("candidates") or []
        results = []
        counts: Counter = Counter()
        for row in rows:
            r = evaluate_candidate(str(row.get("label") or ""), dict(row))
            r["qid"] = row.get("qid")
            results.append(r)
            counts[r["verdict"]] += 1
        print(json.dumps({"n": len(results), "counts": dict(counts), "results": results}, indent=1))
        return 0

    if args.evaluate:
        print("Registered evaluators:")
        for label, fn in EVALUATORS.items():
            print(f"  {label}: {fn.__name__}")
        print("quote_in_evidence: verbatim whitespace-normalized substring")
        print(f"budget cap: {BUDGET_CAP} successful generations")
        return 0

    # Step 1b — assign qids (hard requirement: complete Step 0).
    if args.assign_qids:
        try:
            payload, v1, written = assign_qids(
                labels if step0_ok else {},
                validation0=validation0,
                source=str(args.labels) if args.labels else "step0_residual_labels.json",
            )
        except ValueError as exc:
            print(f"ASSIGN FAILED: {exc}", file=sys.stderr)
            return 2
        print(
            f"step1_assigned=True step0_complete=True "
            f"qid_assignment={payload['qid_assignment']} "
            f"label_counts={payload['label_counts']} "
            f"buckets={{ "
            + ", ".join(
                f"{lab}: {payload['gates'][lab]['n']}" for lab in STEP1_LABELS
            )
            + " }"
        )
        for k, p in written.items():
            print(f"  wrote {k}: {p.name}")
        if args.require_complete and not v1["ok"]:
            print(f"INCOMPLETE: prereg validation failed: {v1['errors']}", file=sys.stderr)
            return 2
        return 0

    if not (args.register or args.require_complete):
        ap.print_help()
        return 1

    # Register (always when --register; completeness gated separately).
    if not step0_ok and not args.allow_incomplete_step0 and args.require_complete:
        print(
            f"STEP0 INCOMPLETE: {validation0.get('n_missing', '?')} residual labels missing; "
            "refusing --require-complete without --allow-incomplete-step0",
            file=sys.stderr,
        )
        return 2

    payload = build_gates(
        labels if step0_ok else {},
        step0_complete=step0_ok,
        source="step0_residual_labels.json" if step0_ok else "none",
    )
    v1 = validate_prereg(payload)

    if args.register:
        written = publish(payload, v1)
        print(
            f"step1_registered={v1['ok']} step0_complete={payload['step0_complete']} "
            f"qid_assignment={payload['qid_assignment']} "
            f"label_counts={payload['label_counts']} errors={v1['errors'] or 'none'}"
        )
        for k, p in written.items():
            print(f"  wrote {k}: {p.name}")
        if step0_ok:
            # Labels already complete: register also performs assignment.
            print(
                "  note: Step 0 complete — run --assign-qids for the full Step 1b "
                "assignment report (step1_qid_assignment.json)"
            )

    if args.require_complete:
        if not v1["ok"] or not step0_ok:
            print(
                "INCOMPLETE: Step 0 labels must be complete and prereg must validate "
                f"(step0_complete={step0_ok}, step1_ok={v1['ok']})",
                file=sys.stderr,
            )
            return 2
    return 0 if v1["ok"] or args.allow_incomplete_step0 else 1


if __name__ == "__main__":
    sys.exit(main())

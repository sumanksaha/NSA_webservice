"""Build the full-150 human review worksheet (evaluator v2 context).

Every question gets a packet: question, v2 reference set (v1 conclusion + widened
alternatives where present), the four system answers (C-O3, D2, D3, E1), machine
scores under v1 and v2, and blank judgment fields. Prior section-20 judgments are
prefilled from `human_audit_tabulation.json` when present (marked
[PREFILLED - verify or override]).

Step 0 residual labeling (Experiment D/E/F comprehensive plan, sec 5.2):
packets in the residual set (D2 still incorrect on III, plus F1 recovered-but-
still-wrong) are marked [STEP0-RESIDUAL] and MUST be labeled with exactly one of:
  reference_narrow | evidence_missing | model_wrong
No new model calls; labels gate the three per-label interventions.

The completed worksheet is consumed by `evaluation/tabulate_full_review.py`, which
turns verdicts into concrete modification outputs:
  - evaluator v3 overlay candidates (unresolved evaluator misses)
  - step0 residual label buckets -> corpus-fill / contrastive / dual-score gates
  - F1 abstention-gate / F2 provision-flag question lists (model-side targets)
  - a human gold-label set for evaluator calibration experiments

Outputs (evaluation/out/ceiling_v5/):
  full_review_worksheet.md     - 150 review packets ([STEP0-RESIDUAL] on the residual set)
  full_review_status.json      - prefilled/blank + step0 residual progress tracker
  step0_residual_set.json      - residual qids + Step 0 verdict enum (plan sec 5.2)
"""

from __future__ import annotations

import json
import sys
import warnings
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

OUT = ROOT / "evaluation" / "out" / "ceiling_v5"

# --------------------------------------------------------------------------- #
# Load everything
# --------------------------------------------------------------------------- #
overlay = json.loads((ROOT / "evaluation" / "evaluator_v2_overlay.json").read_text(encoding="utf-8"))
widened = overlay["widened_conclusions"]

with redirect_stdout(__import__("io").StringIO()), redirect_stderr(__import__("io").StringIO()):
    from evaluation.benchmark import load_questions

    QUESTIONS = {q.raw["question_id"]: q for q in load_questions()}

C_PERQ = json.load(open(OUT / "experiment_C_per_question.json", encoding="utf-8"))
D_PERQ = {r["qid"]: r for r in map(json.loads, (OUT / "experiment_D_per_question.jsonl").open(encoding="utf-8"))}
E_PERQ = {r["qid"]: r for r in map(json.loads, (OUT / "experiment_E_per_question.jsonl").open(encoding="utf-8"))}
F_PERQ = {r["qid"]: r for r in map(json.loads, (OUT / "experiment_F_per_question.jsonl").open(encoding="utf-8"))}
V2_PERQ = {r["qid"]: r for r in map(json.loads, (OUT / "evaluator_v2_per_question.jsonl").open(encoding="utf-8"))}
TAB = json.load(open(OUT / "human_audit_tabulation.json", encoding="utf-8"))
PRIOR = {r["qid"]: r for r in TAB["records"]}

CONDITIONS = ("C-O3", "D2", "D3", "E1")


def residual_qids() -> set[str]:
    """Step 0 residual: D2 still incorrect on III, plus F1 recovered-but-still-wrong."""
    out: set[str] = set()
    for qid, r in D_PERQ.items():
        c = r.get("C-O3") or {}
        if (
            c.get("correct_o1") is False
            and c.get("correct_o2") is False
            and c.get("correct") is False
            and (r.get("D2") or {}).get("correct") is False
        ):
            out.add(qid)
    for qid, r in F_PERQ.items():
        if r.get("f1_status") == "recovered" and not (r.get("F1") or {}).get("correct"):
            out.add(qid)
    return out


RESIDUAL = residual_qids()


def answers_for(qid: str) -> dict[str, str | None]:
    d = D_PERQ.get(qid, {})
    e1 = E_PERQ.get(qid, {}).get("E1") or {}
    return {
        "C-O3": ((C_PERQ.get(qid, {}).get("oracle_conditions", {}).get("O3_full_support") or {}).get("answer")),
        "D2": (d.get("D2") or {}).get("answer"),
        "D3": (d.get("D3") or {}).get("answer"),
        "E1": e1.get("answer"),
    }


def fmt(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "correct" if v else "incorrect"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


lines: list[str] = [
    "# Full-150 Human Review Worksheet (evaluator v2 context)",
    "",
    "This worksheet covers **all 150 benchmark questions** across the four final systems.",
    f"Prior section-20 judgments prefilled where available; otherwise blank.",
    f"**Step 0 residual set: {len(RESIDUAL)} packets marked [STEP0-RESIDUAL]** "
    "(D2 still incorrect on III, plus F1 recovered-but-still-wrong).",
    "",
    "## Why: what this review feeds",
    "",
    "### Step 0 (primary) — freeze the scorer and label the residual (plan sec 5.2)",
    "",
    "No new model calls. Each [STEP0-RESIDUAL] packet gets **exactly one** of:",
    "",
    "1. `reference_narrow` — evidence supports a defensible reading the reference",
    "   conclusion does not accept (dual-score later; do not change the model).",
    "2. `evidence_missing` — the operative statutory text is absent from the O3 payload",
    "   (check the index if unsure). Intervention: add the instrument to the corpus,",
    "   re-retrieve that qid only, one answer call. Stop if the section is not in the index.",
    "3. `model_wrong` — the operative text is present and the model applied it wrongly.",
    "   Intervention: one contrastive span-cut call behind the quote-in-evidence gate.",
    "",
    "Non-residual packets may still use the full verdict vocabulary below to feed",
    "evaluator v3 and the F gate lists.",
    "",
    "### Secondary — evaluator alignment and model-side defects",
    "",
    "1. **Evaluator alignment** - v2 widened references; new `evaluator_miss` /",
    "   `reference_narrow` verdicts become the **evaluator v3 overlay**.",
    "2. **Model-side defects** - `model_action` still populates F1/F2 gate lists.",
    "",
    "## How to fill each packet",
    "",
    "- `human_correct`: `yes` / `no` / `partial` - judged on the **best** answer shown (say which letter).",
    "- `verdict` (per the best answer):",
    "  - **[STEP0-RESIDUAL] required:** `reference_narrow` / `evidence_missing` / `model_wrong`",
    "  - otherwise: `evaluator_miss` / `model_wrong` / `reference_narrow` / `evidence_missing` / `genuinely_wrong` / `ambiguous`.",
    "- `model_action` - what should change, one of:",
    "  - `none`                 - no change needed (answer adequate and, once v2/v3 scoring fixed, correctly scored)",
    "  - `fix_reference`        - the reference conclusion needs widening/rewriting (evaluator v3 candidate)",
    "  - `add_instrument_text`  - operative text missing from corpus/O3 payload (pairs with `evidence_missing`)",
    "  - `contrastive_repair`   - text present but misapplied (pairs with `model_wrong`; contrastive span-cut call)",
    "  - `abstention_gate`      - model should have answered from available evidence (F1 target)",
    "  - `provision_check`      - model cited/argued from the wrong provision (F2 target)",
    "  - `needs_deeper_reasoning` - neither quick fix applies; genuine interpretation-depth gap",
    "- `category`: A-R taxonomy letter (A retrieval/evidence framing, B wrong provision, C definition,",
    "  D condition omission, E exception omission, F cross-reference, G fact extraction, H fact-condition",
    "  mapping, I interpretation, J application, K conflict, L unsupported conclusion, M citation,",
    "  N incomplete, O abstention, P evaluation mismatch, Q other).",
    "- `notes`: one line. For `fix_reference`, state what the true conclusion should say.",
    "  For `evidence_missing`, name the absent instrument/section. For `model_wrong`, name the span the model should have applied.",
    "",
    "Machine scores show **v2** first (current evaluator) then v1 in parentheses.",
    "Scorer is frozen (`experiment_b_topk_eval` primitives + v2 overlay only).",
    "",
    "---",
    "",
]

status: dict[str, dict] = {}
n_prefilled = 0

for qid in sorted(QUESTIONS):
    q = QUESTIONS[qid]
    raw = q.raw
    refs = [q.acceptable_conclusion or ""]
    w = widened.get(qid)
    if w:
        refs.append(w["add"])

    prior = PRIOR.get(qid)
    prefilled = prior is not None and bool(prior.get("verdict"))
    is_residual = qid in RESIDUAL

    ans = answers_for(qid)
    v2row = V2_PERQ.get(qid, {})

    flags = []
    if is_residual:
        flags.append("[STEP0-RESIDUAL]")
    flags.append("[PREFILLED - verify/override]" if prefilled else "[BLANK]")
    lines += [
        f"### {qid} - {raw.get('difficulty', '?')} | {', '.join(raw.get('question_type', [])) or 'n/a'} | "
        + " | ".join(flags),
        "",
        f"**Question:** {D_PERQ.get(qid, {}).get('question') or raw.get('question')}",
        "",
    ]

    if w:
        lines += [
            f"**Reference (v2, widened):** {w['add']}",
            "",
            f"*(v1 reference: {refs[0] or '(none)'})*",
            "",
        ]
    else:
        lines += [f"**Reference:** {refs[0] or '(none recorded)'}", ""]

    if raw.get("common_traps"):
        lines += [f"**Known trap(s):** {'; '.join(raw['common_traps'])}", ""]
    if raw.get("insufficient_evidence"):
        lines += ["**Note: insufficient-evidence question - a correct abstention is the right answer.**", ""]

    labels = ["A. C-O3", "B. D2", "C. D3", "D. E1"]
    for label, cond in zip(labels, CONDITIONS):
        a = ans.get(cond)
        rec = v2row.get(cond) or {}
        if rec.get("status") != "ok":
            lines += [f"**{label}:** *(not run)*", ""]
            continue
        v2, v1 = rec.get("v2") or {}, rec.get("v1") or {}
        lines += [
            f"**{label}:** *(v2: {fmt(v2.get('correct'))}, soft {fmt(v2.get('soft'))} | v1: {fmt(v1.get('correct'))}, soft {fmt(v1.get('answer_correctness'))})*",
            "",
            "```",
            (a or "(empty)").strip(),
            "```",
            "",
        ]

    if prefilled:
        n_prefilled += 1
        lines += [
            "**Prior judgment (section-20 audit):**",
            "",
            f"- human_correct: {prior.get('human_correct')}",
            f"- verdict: {prior.get('verdict')}",
            f"- model_action: *(derive from prior: {'fix_reference' if prior.get('verdict') == 'evaluator_miss' else 'none'})*",
            f"- category: {prior.get('reasoning_error_category')}",
            f"- notes: {prior.get('notes')}",
            "",
        ]
    else:
        lines += ["**Your judgment:**", ""]
        if is_residual:
            lines += [
                "- human_correct: ",
                "- verdict:  # required: reference_narrow | evidence_missing | model_wrong",
                "- model_action:  # fix_reference | add_instrument_text | contrastive_repair",
                "- category: ",
                "- notes: ",
                "",
            ]
        else:
            lines += [
                "- human_correct: ",
                "- verdict: ",
                "- model_action: ",
                "- category: ",
                "- notes: ",
                "",
            ]

    lines += ["---", ""]
    status[qid] = {
        "prefilled": prefilled,
        "prior_verdict": (prior or {}).get("verdict"),
        "step0_residual": is_residual,
    }

(OUT / "full_review_worksheet.md").write_text("\n".join(lines), encoding="utf-8")
n_residual = len(RESIDUAL)
(OUT / "full_review_status.json").write_text(
    json.dumps(
        {
            "n_total": len(status),
            "n_prefilled": n_prefilled,
            "n_blank": len(status) - n_prefilled,
            "n_step0_residual": n_residual,
            "step0_residual_qids": sorted(RESIDUAL),
            "prefilled": sorted(k for k, v in status.items() if v["prefilled"]),
            "status": status,
        },
        indent=1,
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
(OUT / "step0_residual_set.json").write_text(
    json.dumps(
        {
            "definition": "D2 still incorrect on III (correct_o1&o2&O3 false) ∪ F1 recovered but binary-incorrect",
            "n_residual": n_residual,
            "qids": sorted(RESIDUAL),
            "verdict_enum_step0": ["reference_narrow", "evidence_missing", "model_wrong"],
            "source": "build_full_review_worksheet.residual_qids",
        },
        indent=1,
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
print(f"worksheet: 150 packets ({n_prefilled} prefilled, {150 - n_prefilled} blank, {n_residual} step0-residual)")
print("wrote full_review_worksheet.md + full_review_status.json + step0_residual_set.json")

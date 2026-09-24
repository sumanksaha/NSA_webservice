"""Build the full-150 human review worksheet (evaluator v2 context).

Every question gets a packet: question, v2 reference set (v1 conclusion + widened
alternatives where present), the four system answers (C-O3, D2, D3, E1), machine
scores under v1 and v2, and blank judgment fields. The 45 questions already judged
in the section-20 audit are prefilled from `human_audit_tabulation.json` (marked
[PREFILLED - verify or override]).

The completed worksheet is consumed by `evaluation/tabulate_full_review.py`, which
turns verdicts into concrete modification outputs:
  - evaluator v3 overlay candidates (unresolved evaluator misses)
  - F1 abstention-gate / F2 provision-flag question lists (model-side targets)
  - a human gold-label set for evaluator calibration experiments

Outputs (evaluation/out/ceiling_v5/):
  full_review_worksheet.md     - 150 review packets
  full_review_status.json      - which packets are prefilled vs blank (progress tracker)
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
V2_PERQ = {r["qid"]: r for r in map(json.loads, (OUT / "evaluator_v2_per_question.jsonl").open(encoding="utf-8"))}
TAB = json.load(open(OUT / "human_audit_tabulation.json", encoding="utf-8"))
PRIOR = {r["qid"]: r for r in TAB["records"]}

CONDITIONS = ("C-O3", "D2", "D3", "E1")


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
    "The 45 packets reviewed in the section-20 audit are **prefilled** - verify or override them;",
    "the remaining 105 are blank.",
    "",
    "## Why: what this review feeds",
    "",
    "The D/E cycle established that neither more evidence (C), more reasoning layers (D),",
    "nor citation checking (E) moves correctness much. Two bottlenecks remain, and both need",
    "**human labels at full scale** to fix:",
    "",
    "1. **Evaluator alignment** - v2 widened references fixed 13 of 22 audited misses, but that",
    "overlay was drafted from a 45-question sample. Your verdicts on the remaining 105 questions",
    "either validate v2 or surface new `evaluator_miss` cases -> become the **evaluator v3 overlay**.",
    "2. **Model-side defects** - wrong-provision selection (11) and over-abstention (7) are the two",
    "clusters Experiment F targets. Your `verdict` + `model_action` fields directly populate F's",
    "gate lists, and the F design's success gates are counted against these labels.",
    "",
    "## How to fill each packet",
    "",
    "- `human_correct`: `yes` / `no` / `partial` - judged on the **best** answer shown (say which letter).",
    "- `verdict` (per the best answer): `evaluator_miss` / `model_wrong` / `reference_narrow` / `genuinely_wrong` / `ambiguous`.",
    "- `model_action` - what should change, one of:",
    "  - `none`                 - no change needed (answer adequate and, once v2/v3 scoring fixed, correctly scored)",
    "  - `fix_reference`        - the reference conclusion needs widening/rewriting (evaluator v3 candidate)",
    "  - `abstention_gate`      - model should have answered from available evidence (F1 target)",
    "  - `provision_check`      - model cited/argued from the wrong provision (F2 target)",
    "  - `needs_deeper_reasoning` - neither quick fix applies; genuine interpretation-depth gap",
    "- `category`: A-R taxonomy letter (A retrieval/evidence framing, B wrong provision, C definition,",
    "  D condition omission, E exception omission, F cross-reference, G fact extraction, H fact-condition",
    "  mapping, I interpretation, J application, K conflict, L unsupported conclusion, M citation,",
    "  N incomplete, O abstention, P evaluation mismatch, Q other).",
    "- `notes`: one line. For `fix_reference`, state what the true conclusion should say.",
    "",
    "Machine scores show **v2** first (current evaluator) then v1 in parentheses.",
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

    ans = answers_for(qid)
    v2row = V2_PERQ.get(qid, {})

    lines += [
        f"### {qid} - {raw.get('difficulty', '?')} | {', '.join(raw.get('question_type', [])) or 'n/a'} | "
        f"{'[PREFILLED - verify/override]' if prefilled else '[BLANK]'}",
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
        lines += [
            "**Your judgment:**",
            "",
            "- human_correct: ",
            "- verdict: ",
            "- model_action: ",
            "- category: ",
            "- notes: ",
            "",
        ]

    lines += ["---", ""]
    status[qid] = {"prefilled": prefilled, "prior_verdict": (prior or {}).get("verdict")}

(OUT / "full_review_worksheet.md").write_text("\n".join(lines), encoding="utf-8")
(OUT / "full_review_status.json").write_text(
    json.dumps(
        {
            "n_total": len(status),
            "n_prefilled": n_prefilled,
            "n_blank": len(status) - n_prefilled,
            "prefilled": sorted(k for k, v in status.items() if v["prefilled"]),
            "status": status,
        },
        indent=1,
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
print(f"worksheet: 150 packets ({n_prefilled} prefilled, {150 - n_prefilled} blank)")
print("wrote full_review_worksheet.md + full_review_status.json")

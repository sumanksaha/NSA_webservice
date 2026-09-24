"""Tabulate the completed full-150 review worksheet into component-modification outputs.

Run after reviewing ``full_review_worksheet.md`` (save the reviewed copy as
``full_review_worksheet_reviewed.md``).  Incremental: packets with a filled
``verdict`` line are parsed; blank packets are skipped and reported.

Step 0 residual labels (plan sec 5.2): [STEP0-RESIDUAL] packets must use
``reference_narrow`` / ``evidence_missing`` / ``model_wrong``. Those verdicts are
bucketed into per-label intervention gates before any aggregate claim.

Outputs (evaluation/out/ceiling_v5/):
  full_review_tabulation.json          - per-qid judgments joined with v1/v2 machine scores + aggregates
  evaluator_v3_candidates.json         - qids needing reference widening (fix_reference), with notes
  step0_residual_labels.json           - residual qid -> label + per-label counts
  step0_corpus_fill_targets.json       - evidence_missing qids (corpus completion + re-retrieve)
  step0_contrastive_targets.json       - model_wrong qids (contrastive span-cut call behind quote gate)
  step0_dual_score_targets.json        - reference_narrow qids (dual-score, no model change)
  experiment_F_gate_lists.json         - F1 abstention-gate + F2 provision-flag qids, with F-design counts
  human_gold_labels.jsonl              - one gold record per reviewed qid (for evaluator calibration)
  full_review_tabulation.md            - human-readable summary
"""

from __future__ import annotations

import collections
import json
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "evaluation" / "out" / "ceiling_v5"
# Default input; optionally override: python tabulate_full_review.py [reviewed-file]
REVIEWED = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "full_review_worksheet_reviewed.md"

if not REVIEWED.exists():
    raise SystemExit(
        f"not found: {REVIEWED.name}\n"
        "Review full_review_worksheet.md and save the reviewed copy as "
        "full_review_worksheet_reviewed.md (partial reviews are fine - rerun any time)."
    )

# --------------------------------------------------------------------------- #
# Parse reviewed worksheet
# --------------------------------------------------------------------------- #
text = REVIEWED.read_text(encoding="utf-8")
blocks = re.split(r"\n### (Q\d{3}) ", text)
records: list[dict] = []
for i in range(1, len(blocks), 2):
    qid, body = blocks[i], blocks[i + 1]
    jz = re.search(r"\*\*(?:Your judgment|Prior judgment.*?):\*\*(.*?)(?=\n---|\Z)", body, re.S)
    j = jz.group(1) if jz else ""

    def grab(label: str) -> str:
        # [ \t]* (not \s*) so the pattern cannot cross into the next judgment line
        m = re.search(rf"^[ \t]*-\s*{label}\s*(?:\([^)]*\))?\s*:[ \t]*(.*)$", j, re.I | re.M)
        if not m:
            return ""
        # strip inline guidance comments (e.g. "# required: reference_narrow | ...")
        return m.group(1).split("#", 1)[0].strip()

    verdict = grab("verdict")
    if not verdict:
        records.append({"qid": qid, "reviewed": False, "step0_residual": "[STEP0-RESIDUAL]" in body})
        continue
    # Track which condition the reviewer judged best (from human_correct note or explicit letter)
    best_m = re.search(r"best answer[^a-zA-Z]*([A-D])\b", j, re.I)
    records.append({
        "qid": qid,
        "reviewed": True,
        "human_correct": grab("human_correct"),
        "verdict": verdict,
        "model_action": grab("model_action"),
        "category": grab("category"),
        "notes": grab("notes"),
        "best_answer_letter": (best_m.group(1).upper() if best_m else None),
        "prefilled": "Prior judgment" in (jz.group(0)[:60]),
        "step0_residual": "[STEP0-RESIDUAL]" in body,
    })

reviewed = [r for r in records if r.get("reviewed")]
print(f"reviewed: {len(reviewed)}/150  (blank: {150 - len(reviewed)})")

# --------------------------------------------------------------------------- #
# Join machine scores (v1 + v2)
# --------------------------------------------------------------------------- #
V2 = {r["qid"]: r for r in map(json.loads, (OUT / "evaluator_v2_per_question.jsonl").open(encoding="utf-8"))}
TAB = json.load(open(OUT / "human_audit_tabulation.json", encoding="utf-8"))
PRIOR = {r["qid"]: r for r in TAB["records"]}
CONDITIONS = ("C-O3", "D2", "D3", "E1")

for r in reviewed:
    qid = r["qid"]
    row = V2.get(qid, {})
    r["machine"] = {
        c: {
            "v1": ((row.get(c) or {}).get("v1") or {}).get("correct"),
            "v1_soft": ((row.get(c) or {}).get("v1") or {}).get("answer_correctness"),
            "v2": ((row.get(c) or {}).get("v2") or {}).get("correct"),
            "v2_soft": ((row.get(c) or {}).get("v2") or {}).get("soft"),
        }
        for c in CONDITIONS
    }
    prior = PRIOR.get(qid)
    if prior:
        r.setdefault("category", prior.get("reasoning_error_category"))
        r.setdefault("notes", prior.get("notes"))
        r.setdefault("human_correct", prior.get("human_correct"))

# --------------------------------------------------------------------------- #
# Aggregates
# --------------------------------------------------------------------------- #
verdicts = collections.Counter(r["verdict"] for r in reviewed)
actions = collections.Counter(r.get("model_action", "?") for r in reviewed)
categories = collections.Counter((r.get("category") or "?").split()[0] for r in reviewed)

# Agreement between human verdict and v2 machine correctness (best condition)
agree_correct = sum(
    1 for r in reviewed if r["verdict"] == "evaluator_miss" and any(r["machine"][c]["v2"] for c in CONDITIONS)
)
evaluator_miss_total = verdicts.get("evaluator_miss", 0)

tabulation = {
    "source": REVIEWED.name,
    "n_reviewed": len(reviewed),
    "n_blank": 150 - len(reviewed),
    "verdict_distribution": dict(verdicts),
    "model_action_distribution": dict(actions),
    "category_distribution": dict(categories),
    "evaluator_v2_agreement": {
        "evaluator_miss_total": evaluator_miss_total,
        "already_correct_under_v2_best_condition": agree_correct,
        "still_unresolved": evaluator_miss_total - agree_correct,
    },
    "records": reviewed,
}
(OUT / "full_review_tabulation.json").write_text(json.dumps(tabulation, indent=1, ensure_ascii=False), encoding="utf-8")

# --------------------------------------------------------------------------- #
# Step 0 residual labels (plan sec 5.2) — publish before any aggregate claim
# --------------------------------------------------------------------------- #
STEP0_ENUM = ("reference_narrow", "evidence_missing", "model_wrong")
# Prefer the residual set file (authoritative) over worksheet markers alone.
_residual_set_path = OUT / "step0_residual_set.json"
if _residual_set_path.exists():
    _residual_set = set(json.loads(_residual_set_path.read_text(encoding="utf-8"))["qids"])
else:
    _residual_set = {r["qid"] for r in records if r.get("step0_residual")}

step0_residual = [r for r in reviewed if r["qid"] in _residual_set]
step0_blank = sorted(_residual_set - {r["qid"] for r in step0_residual})
step0_labels = {r["qid"]: r["verdict"] for r in step0_residual}
bad_step0 = {qid: v for qid, v in step0_labels.items() if v not in STEP0_ENUM}
# Valid labels only for completeness accounting
valid_step0 = {qid: v for qid, v in step0_labels.items() if v in STEP0_ENUM}
step0_complete = not step0_blank and not bad_step0 and len(valid_step0) == len(_residual_set)

step0 = {
    "enum": list(STEP0_ENUM),
    "n_residual_total": len(_residual_set),
    "n_reviewed": len(step0_residual),
    "n_blank": len(step0_blank),
    "blank_qids": step0_blank,
    "labels": dict(sorted(valid_step0.items())),
    "label_counts": dict(collections.Counter(valid_step0.values())),
    "invalid_labels": bad_step0,
    "n_labeled_valid": len(valid_step0),
    "n_invalid": len(bad_step0),
    "step0_complete": step0_complete,
    "source": REVIEWED.name,
    "scorer": "frozen: experiment_b_topk_eval token_overlap/abstain_check + evaluator_v2 overlay",
    "note": "publish per-label counts before any aggregate soft-score claim (plan sec 5.2/Step 3)",
}
(OUT / "step0_residual_labels.json").write_text(
    json.dumps(step0, indent=1, ensure_ascii=False), encoding="utf-8"
)

corpus_fill = sorted(qid for qid, v in valid_step0.items() if v == "evidence_missing")
contrastive = sorted(qid for qid, v in valid_step0.items() if v == "model_wrong")
dual_score = sorted(qid for qid, v in valid_step0.items() if v == "reference_narrow")

(OUT / "step0_corpus_fill_targets.json").write_text(
    json.dumps(
        {
            "label": "evidence_missing",
            "n": len(corpus_fill),
            "qids": corpus_fill,
            "intervention": "add missing instrument text to corpus; re-retrieve qid only; one answer call",
            "keep_if": "gold span now in payload AND binary correctness rises on that id",
            "reject_if": "section not in index — stop, do not loop retrieval",
        },
        indent=1,
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
(OUT / "step0_contrastive_targets.json").write_text(
    json.dumps(
        {
            "label": "model_wrong",
            "n": len(contrastive),
            "qids": contrastive,
            "intervention": "one contrastive span-cut call; options cut from retrieved text with section id/authority/operative sentence",
            "keep_if": "cited span is verbatim substring of context AND operative sentence changed",
            "reject_if": "section-number-only swap, span not in context, or abstains on element absent from new payload — keep D2",
        },
        indent=1,
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
(OUT / "step0_dual_score_targets.json").write_text(
    json.dumps(
        {
            "label": "reference_narrow",
            "n": len(dual_score),
            "qids": dual_score,
            "intervention": "change the reference or report a second score; do not change the model",
            "keep_if": "both old and new score reported until references fixed",
            "reject_if": "a generation call was spent chasing the narrow reference",
        },
        indent=1,
        ensure_ascii=False,
    ),
    encoding="utf-8",
)

# --------------------------------------------------------------------------- #
# Evaluator v3 candidates
# --------------------------------------------------------------------------- #
v3 = {
    r["qid"]: {
        "verdict": r["verdict"],
        "notes": r.get("notes"),
        "already_fixed_by_v2": any(r["machine"][c]["v2"] for c in CONDITIONS),
    }
    for r in reviewed
    if r["verdict"] in ("evaluator_miss", "reference_narrow")
}
(OUT / "evaluator_v3_candidates.json").write_text(json.dumps(v3, indent=1, ensure_ascii=False), encoding="utf-8")

# --------------------------------------------------------------------------- #
# Experiment F gate lists
# --------------------------------------------------------------------------- #
f1 = [r["qid"] for r in reviewed if r.get("model_action") == "abstention_gate"]
f2 = [r["qid"] for r in reviewed if r.get("model_action") == "provision_check"]
deep = [r["qid"] for r in reviewed if r.get("model_action") == "needs_deeper_reasoning"]
(OUT / "experiment_F_gate_lists.json").write_text(
    json.dumps(
        {
            "f1_abstention_gate": sorted(f1),
            "f2_provision_check": sorted(f2),
            "needs_deeper_reasoning": sorted(deep),
            "n_f1": len(f1),
            "n_f2": len(f2),
            "note": "human-labeled targets; F's deterministic gates must recover these (plus look-alikes) to pass",
        },
        indent=1,
        ensure_ascii=False,
    ),
    encoding="utf-8",
)

# --------------------------------------------------------------------------- #
# Human gold labels
# --------------------------------------------------------------------------- #
with (OUT / "human_gold_labels.jsonl").open("w", encoding="utf-8") as f:
    for r in reviewed:
        f.write(
            json.dumps(
                {
                    "qid": r["qid"],
                    "human_correct": r.get("human_correct"),
                    "verdict": r["verdict"],
                    "model_action": r.get("model_action"),
                    "category": r.get("category"),
                    "best_answer_letter": r.get("best_answer_letter"),
                    "step0_residual": bool(r.get("step0_residual")),
                    "machine_v2_best": max((r["machine"][c]["v2"] or False) for c in CONDITIONS),
                    "machine_v2_best_soft": max((r["machine"][c]["v2_soft"] or 0.0) for c in CONDITIONS),
                    "notes": r.get("notes"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


# --------------------------------------------------------------------------- #
# Markdown summary
# --------------------------------------------------------------------------- #
def pct(x: int) -> str:
    return f"{100 * x / max(len(reviewed), 1):.1f}%"


md = [
    "# Full-150 Review Tabulation",
    "",
    f"Reviewed: **{len(reviewed)}/150** ({150 - len(reviewed)} blank)",
    "",
    "## Step 0 residual labels (publish first)",
    "",
    f"- residual packets: {step0['n_residual_total']} | reviewed: {step0['n_reviewed']} | blank: {step0['n_blank']}",
    f"- label counts: {step0['label_counts']}",
    f"- invalid labels (not in enum): {step0['invalid_labels'] or 'none'}",
    f"- **step0_complete: {step0['step0_complete']}**",
    f"- corpus-fill targets (`evidence_missing`): {len(corpus_fill)}",
    f"- contrastive targets (`model_wrong`): {len(contrastive)}",
    f"- dual-score targets (`reference_narrow`): {len(dual_score)}",
    "",
    "## Verdicts",
    "",
    "| Verdict | n | share |",
    "|---|---:|---:|",
    *[f"| {v} | {n} | {pct(n)} |" for v, n in verdicts.most_common()],
    "",
    "## Model actions (component targets)",
    "",
    "| Action | n | Feeds |",
    "|---|---:|---|",
    f"| fix_reference | {actions.get('fix_reference', 0)} | evaluator v3 overlay |",
    f"| add_instrument_text | {actions.get('add_instrument_text', 0)} | corpus fill (`evidence_missing`) |",
    f"| contrastive_repair | {actions.get('contrastive_repair', 0)} | contrastive call (`model_wrong`) |",
    f"| abstention_gate | {actions.get('abstention_gate', 0)} | F1 gate list |",
    f"| provision_check | {actions.get('provision_check', 0)} | F2 gate list |",
    f"| needs_deeper_reasoning | {actions.get('needs_deeper_reasoning', 0)} | future interpretation-depth work |",
    f"| none | {actions.get('none', 0)} | - |",
    "",
    "## Category distribution",
    "",
    ", ".join(f"{c}:{n}" for c, n in categories.most_common()),
    "",
    "## Evaluator v2 agreement",
    "",
    f"- evaluator_miss cases: {evaluator_miss_total}",
    f"- already fixed by v2 (best condition correct): {agree_correct}",
    f"- still unresolved (v3 candidates): {evaluator_miss_total - agree_correct}",
    "",
]
(OUT / "full_review_tabulation.md").write_text("\n".join(md), encoding="utf-8")

print("step0 labels:", step0["label_counts"], "blank residual:", step0["n_blank"])
print("step0_complete:", step0["step0_complete"])
if bad_step0:
    print("WARNING invalid step0 labels:", bad_step0)
if not step0["step0_complete"]:
    print(
        "NOTE: Step 0 incomplete — label every residual qid "
        f"({step0['n_blank']} blank, {len(bad_step0)} invalid) before Experiment G."
    )
print("verdicts:", dict(verdicts))
print("actions:", dict(actions))
print("wrote full_review_tabulation.json/.md, evaluator_v3_candidates.json,")
print("        step0_residual_labels.json, step0_corpus_fill_targets.json,")
print("        step0_contrastive_targets.json, step0_dual_score_targets.json,")
print("        experiment_F_gate_lists.json, human_gold_labels.jsonl")

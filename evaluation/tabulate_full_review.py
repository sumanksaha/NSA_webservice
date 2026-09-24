"""Tabulate the completed full-150 review worksheet into component-modification outputs.

Run after reviewing ``full_review_worksheet.md`` (save the reviewed copy as
``full_review_worksheet_reviewed.md``).  Incremental: packets with a filled
``verdict`` line are parsed; blank packets are skipped and reported.

Outputs (evaluation/out/ceiling_v5/):
  full_review_tabulation.json          - per-qid judgments joined with v1/v2 machine scores + aggregates
  evaluator_v3_candidates.json         - qids needing reference widening (fix_reference), with notes
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
        m = re.search(rf"-\s*{label}\s*\([^)]*\)\s*:\s*(.*)", j, re.I)
        if m:
            return m.group(1).strip()
        m = re.search(rf"-\s*{label}\s*:\s*(.*)", j, re.I)
        return m.group(1).strip() if m else ""

    verdict = grab("verdict")
    if not verdict:
        records.append({"qid": qid, "reviewed": False})
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

print("verdicts:", dict(verdicts))
print("actions:", dict(actions))
print("wrote full_review_tabulation.json/.md, evaluator_v3_candidates.json,")
print("        experiment_F_gate_lists.json, human_gold_labels.jsonl")

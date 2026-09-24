"""Select the Section-20 human audit sample (30-50 questions) and build review packets.

Priorities (Experiment D spec, section 20):
  1. III failures            - incorrect under O1, O2 and O3 in Experiment C
  2. D2 improvements         - C-O3 incorrect -> D2 correct
  3. D3-only improvements    - D2 incorrect -> D3 correct (but not C-O3)
  4. D2 regressions          - C-O3 correct -> D2 incorrect
  5. D3 regressions          - D2 correct -> D3 incorrect
  6. Ceiling stratum         - binary-incorrect under C-O3 AND D2 but top soft scores
                               (the evaluator-alignment population this audit exists to test)

All 150 questions are III-eligible, so the III stratum is partitioned into disjoint
sub-strata by priority: III∩transitions, III∩E-misattributed, III∩ceiling, III-rest.
C-O3-correct questions are mostly outside III, so transitions/ceiling strata are
non-overlapping by construction; the remaining slots are filled from both pools.

Reproducibility: fixed seed, deterministic sort order.  Rerun overwrites the packet
files with identical content.

Outputs (evaluation/out/ceiling_v5/):
  human_audit_sample.json       - machine-readable selection record with strata provenance
  human_audit_worksheet.md      - human-readable review packets, one per question
  human_audit_answers.jsonl     - answer template (one record per question, blanks to fill)
"""

from __future__ import annotations

import json
import random
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

OUT = ROOT / "evaluation" / "out" / "ceiling_v5"
SEED = 20260923
TARGET = 45  # mid of the spec's 30-50 range

# --------------------------------------------------------------------------- #
# Load artifacts
# --------------------------------------------------------------------------- #
D = {r["qid"]: r for r in map(json.loads, (OUT / "experiment_D_per_question.jsonl").open(encoding="utf-8"))}
E = {r["qid"]: r for r in map(json.loads, (OUT / "experiment_E_per_question.jsonl").open(encoding="utf-8"))}
V: dict[str, dict] = {}
for line in (OUT / "experiment_E_verification.jsonl").open(encoding="utf-8"):
    r = json.loads(line)
    V[r["qid"]] = r  # dedupe retries: later records win

import contextlib
import io

with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    from evaluation.benchmark import load_questions

    QUESTIONS = {q.raw["question_id"]: q for q in load_questions()}


# --------------------------------------------------------------------------- #
# Strata computation
# --------------------------------------------------------------------------- #
def ok(v) -> bool:
    return v is True


iii: set[str] = set()
d2_imp: set[str] = set()
d2_reg: set[str] = set()
d3_only: set[str] = set()
d3_reg: set[str] = set()
ceiling: set[str] = set()  # binary-incorrect C-O3 AND D2, non-abstaining

for qid, r in sorted(D.items()):
    c = r["C-O3"]
    d2 = r.get("D2") or {}
    d3 = r.get("D3") or {}
    if c.get("correct_o1") is False and c.get("correct_o2") is False and c.get("correct") is False:
        iii.add(qid)
    if not ok(c.get("correct")) and ok(d2.get("correct")):
        d2_imp.add(qid)
    if ok(c.get("correct")) and not ok(d2.get("correct")):
        d2_reg.add(qid)
    if not ok(d2.get("correct")) and ok(d3.get("correct")):
        d3_only.add(qid)
    if ok(d2.get("correct")) and d3.get("correct") is False:
        d3_reg.add(qid)
    if not ok(c.get("correct")) and not ok(d2.get("correct")) and c.get("abstained") is False:
        ceiling.add(qid)

misattr: set[str] = {
    qid for qid, r in V.items() if any(f.get("label") == "misattributed" for f in r.get("results", []))
}

# Ceiling ranking: by best soft score across C-O3 and D2 (both incorrect but
# semantically close to the reference).
ceil_ranked = sorted(
    ceiling,
    key=lambda qid: (
        -max(
            (D[qid]["C-O3"].get("answer_correctness") or 0.0),
            ((D[qid].get("D2") or {}).get("answer_correctness") or 0.0),
        )
    ),
)

# --------------------------------------------------------------------------- #
# Selection: disjoint buckets, then fill
# --------------------------------------------------------------------------- #
selection: dict[str, list[str]] = {}
chosen: set[str] = set()


def take(pool: set[str], n: int, label: str) -> None:
    got = sorted(pool - chosen)[:n]
    selection[label] = got
    chosen.update(got)


# Priority order per spec section 20; III-anchored strata first (they are disjoint
# among themselves), then non-III transition strata, then fill.
take(iii & (d2_imp | d3_only | d2_reg | d3_reg), 6, "iii_transitions")
take(iii & misattr, 5, "iii_e_misattributed")
take(set(ceil_ranked[:12]), 12, "iii_ceiling_top_soft")
remaining_iii = iii - chosen
rng = random.Random(SEED)
rest = rng.sample(sorted(remaining_iii), min(10, len(remaining_iii)))
take(set(rest), 10, "iii_random_rest")
take(d2_imp - iii, 4, "d2_improvements")
take(d3_only - iii, 1, "d3_only_improvements")
take(d2_reg | d3_reg, 5, "regressions")
take(d2_imp | d3_only, 2, "d2_improvements_overflow")

total = len(chosen)
shortfall = TARGET - total
if shortfall > 0:
    pool = sorted((iii | ceiling | d2_imp | d2_reg | d3_reg) - chosen)
    extra = rng.sample(pool, min(shortfall, len(pool)))
    selection["fill_random"] = extra
    chosen.update(extra)

SAMPLE = sorted(chosen)


# --------------------------------------------------------------------------- #
# Enrichment + metadata coverage stats
# --------------------------------------------------------------------------- #
def best_d3_status(r: dict) -> str:
    d3 = r.get("D3") or {}
    if d3.get("correct") is True:
        return "correct"
    if d3.get("abstained"):
        return "abstained"
    if d3:
        return "incorrect"
    return "not_run"


meta_counts = {
    "difficulty": {},
    "domain": {},
    "question_type": {},
}
rows: list[dict] = []
for qid in SAMPLE:
    r = D[qid]
    c, d2, d3 = r["C-O3"], r.get("D2") or {}, r.get("D3") or {}
    e1 = (E.get(qid) or {}).get("E1") or {}
    bq = QUESTIONS[qid]
    raw = bq.raw
    strata_of = [name for name, qids in selection.items() if qid in qids]
    rows.append({
        "qid": qid,
        "strata": strata_of,
        "question": r["question"],
        "difficulty": raw.get("difficulty"),
        "domains": raw.get("domains", []),
        "question_type": raw.get("question_type", []),
        "primary_provisions": raw.get("primary_provisions", []),
        "acceptable_conclusion": bq.acceptable_conclusion,
        "common_traps": raw.get("common_traps", []),
        "answers": {
            "C-O3": c.get("answer"),
            "D2": d2.get("answer"),
            "D3": d3.get("answer"),
            "E1": e1.get("answer"),
        },
        "machine_scores": {
            "C-O3": {
                "correct": c.get("correct"),
                "soft": c.get("answer_correctness"),
                "abstained": c.get("abstained"),
                "citation_recall": c.get("citation_recall"),
                "citation_precision": c.get("citation_precision"),
                "groundedness": c.get("groundedness"),
            },
            "D2": {
                "correct": d2.get("correct"),
                "soft": d2.get("answer_correctness"),
                "abstained": d2.get("abstained"),
            },
            "D3": {
                "correct": d3.get("correct"),
                "soft": d3.get("answer_correctness"),
                "abstained": d3.get("abstained"),
                "audit_status": d3.get("audit_status"),
            },
            "E1": {
                "correct": e1.get("correct"),
                "soft": e1.get("answer_correctness"),
            },
        },
        "e_misattributed_claims": [
            f for f in (V.get(qid) or {}).get("results", []) if f.get("label") == "misattributed"
        ],
    })
    meta_counts["difficulty"][raw.get("difficulty")] = meta_counts["difficulty"].get(raw.get("difficulty"), 0) + 1
    for dom in raw.get("domains", []):
        meta_counts["domain"][dom] = meta_counts["domain"].get(dom, 0) + 1
    for qt in raw.get("question_type", []):
        meta_counts["question_type"][qt] = meta_counts["question_type"].get(qt, 0) + 1

record = {
    "purpose": (
        "Experiment D spec section 20 human audit sample: distinguish 'model legally "
        "wrong' from 'reference too narrow / evaluator-alignment ceiling'."
    ),
    "seed": SEED,
    "target": TARGET,
    "n_selected": len(SAMPLE),
    "strata_sizes": {
        "III total": len(iii),
        "ceiling (C-O3 & D2 binary-incorrect, non-abstain)": len(ceiling),
        "D2 improvements": len(d2_imp),
        "D3-only improvements": len(d3_only),
        "D2 regressions": len(d2_reg),
        "D3 regressions": len(d3_reg),
        "E misattributed-flagged": len(misattr),
    },
    "selection": selection,
    "metadata_coverage": meta_counts,
    "questions": rows,
}

(OUT / "human_audit_sample.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

# --------------------------------------------------------------------------- #
# Markdown worksheet
# --------------------------------------------------------------------------- #
lines: list[str] = [
    "# Human Audit Worksheet - Experiment D/E section-20 sample",
    "",
    f"**n = {len(SAMPLE)}** questions | seed {SEED} | generated from the final D + E artifacts.",
    "",
    "**Purpose.** Binary correct-rate is ~9.5-10.3% while soft correctness is ~0.38 and citations are sound.",
    "That signature means the binding constraint may be the **reference conclusions / evaluator** rather than",
    "the model. This audit decides, per question: was the machine answer legally right but scored wrong",
    "(evaluator/ceiling), legally wrong (model), or legitimately incomplete (reference too narrow)?",
    "",
    "**How to judge each answer** (independent of the machine score):",
    "",
    "- `human_correct`: `yes` / `no` / `partial` - does the answer state a legally correct, complete,",
    "  appropriately qualified conclusion for the question as asked?",
    "- `verdict` (the key field):",
    "  - `evaluator_miss`  - answer legally correct/adequate; binary + soft score are unfair",
    "    (reference too narrow, wrong provision emphasized, phrasing mismatch)",
    "  - `model_wrong`     - answer asserts an incorrect legal position",
    "  - `reference_narrow` - answer partially right; the reference conclusion is unreasonably narrow",
    "  - `genuinely_wrong` - answer wrong AND score roughly fair",
    "  - `ambiguous`       - cannot decide without legal expertise beyond the packet",
    "- `reasoning_error_category`: A-R from the Experiment D taxonomy (A retrieval, B wrong provision,",
    "  C definition, D condition omission, E exception omission, F cross-reference, G fact extraction,",
    "  H fact-condition mapping, I legal interpretation, J application, K conflict/hierarchy, L unsupported",
    "  conclusion, M citation, N incomplete, O abstention, P evaluation mismatch, Q other).",
    "- `notes`: one line on what the reference conclusion lacks or what the answer got wrong.",
    "",
    "Review order: packets are grouped by stratum; judge each answer **before** looking at the machine",
    "scores shown at the end of each packet.",
    "",
    "---",
    "",
]

STRATUM_TITLES = {
    "iii_transitions": "Stratum 1 - III questions with D2/D3 transitions (or regressions)",
    "iii_e_misattributed": "Stratum 2 - III questions flagged by Experiment E misattribution",
    "iii_ceiling_top_soft": "Stratum 3 - ceiling population: binary-incorrect, highest soft scores",
    "iii_random_rest": "Stratum 4 - random sample of remaining III failures",
    "d2_improvements": "Stratum 5 - D2 improvements outside III",
    "d3_only_improvements": "Stratum 6 - D3-only improvement",
    "regressions": "Stratum 7 - D2/D3 regressions",
    "fill_random": "Stratum 8 - fill",
}
STRATUM_ORDER = [
    "iii_transitions",
    "iii_e_misattributed",
    "iii_ceiling_top_soft",
    "iii_random_rest",
    "d2_improvements",
    "d3_only_improvements",
    "regressions",
    "fill_random",
]


def _fmt_score(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "correct" if v else "incorrect"
    return f"{v:.3f}"


for sname in STRATUM_ORDER:
    qids = selection.get(sname, [])
    if not qids:
        continue
    lines += [f"## {STRATUM_TITLES[sname]}", ""]
    for qid in sorted(qids):
        row = next(r for r in rows if r["qid"] == qid)
        lines += [
            f"### {qid} - {row['difficulty']} | {', '.join(row['question_type']) or 'n/a'} | strata: {', '.join(row['strata'])}",
            "",
            f"**Question:** {row['question']}",
            "",
            f"**Reference conclusion:** {row['acceptable_conclusion'] or '(none recorded)'}",
            "",
        ]
        if row["common_traps"]:
            lines += [f"**Known trap(s):** {'; '.join(row['common_traps'])}", ""]
        for label, key in [
            ("A. C-O3 (direct baseline)", "C-O3"),
            ("B. D2 (structured reasoning)", "D2"),
            ("C. D3 (reasoning+auditor)", "D3"),
            ("D. E1 (verify+repair)", "E1"),
        ]:
            ans = row["answers"].get(key)
            lines += [
                f"**{label}:**",
                "",
                "```",
                (ans or "(not run)").strip(),
                "```",
                "",
            ]
        ms = row["machine_scores"]
        lines += [
            "**Machine scores (consult after judging):** "
            f"C-O3: {_fmt_score(ms['C-O3']['correct'])}, soft {_fmt_score(ms['C-O3']['soft'])}"
            f" | D2: {_fmt_score(ms['D2']['correct'])}, soft {_fmt_score(ms['D2']['soft'])}"
            f" | D3: {_fmt_score(ms['D3']['correct'])}, soft {_fmt_score(ms['D3']['soft'])}"
            f" | E1: {_fmt_score((ms['E1'] or {}).get('correct'))}, soft {_fmt_score((ms['E1'] or {}).get('soft'))}",
            "",
            "**Your judgment:**",
            "",
            "- human_correct: ",
            "- verdict: ",
            "- reasoning_error_category: ",
            "- citation_correct (yes/no/partial): ",
            "- auditor_effective (for packets where D3 audited): ",
            "- notes: ",
            "",
            "---",
            "",
        ]

(OUT / "human_audit_worksheet.md").write_text("\n".join(lines), encoding="utf-8")

# --------------------------------------------------------------------------- #
# Machine-readable answer template
# --------------------------------------------------------------------------- #
with (OUT / "human_audit_answers.jsonl").open("w", encoding="utf-8") as f:
    for row in rows:
        f.write(
            json.dumps(
                {
                    "qid": row["qid"],
                    "strata": row["strata"],
                    "human_correct": None,
                    "verdict": None,
                    "reasoning_error_category": None,
                    "citation_correct": None,
                    "auditor_effective": None,
                    "notes": None,
                },
                ensure_ascii=False,
            )
            + "\n"
        )

print(f"selected {len(SAMPLE)} questions (target {TARGET})")
print("strata:", {k: len(v) for k, v in selection.items()})
print("difficulty coverage:", meta_counts["difficulty"])
print("domain coverage:", meta_counts["domain"])
print("question_type coverage:", meta_counts["question_type"])
print("outputs: human_audit_sample.json, human_audit_worksheet.md, human_audit_answers.jsonl")

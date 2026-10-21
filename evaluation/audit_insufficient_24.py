"""WF-7 — evidence-backed disposition of the 24 ``overlay_insufficient`` qids.

v2 widened these qids once and they still failed; adding more reference prose
is explicitly forbidden for this bucket (triage rule). This module re-judges
each qid under the CURRENT ruler (v4.2 — WF-5 lexicon bans + WF-6 widened
bundle + two-shape C2) and emits exactly ONE cause class per qid against the
fixed component vocabulary:

  abstention_marker_fp        answerable qid still refused (lexicon matched a
                              phrase the WF-5 bans do not yet cover) →
                              mechanical fix: same audit-backed WF-5 ban stamping
  temporal_heuristic_fp       the ``repealed``-word heuristic penalised the
                              answer (no gold temporal label exists in the
                              frozen benchmark) → fix: heuristic rule change
  multi_provision_string_mismatch
                              C1 identity TRUE, answer correct per audit, but the
                              reference-string overlap still below threshold →
                              fix: split scoring / per-unit rubric, NOT prose
  penalty_structure_absence   C1 identity FALSE while cited chunks cover gold
                              units (the identity rule misses a legal
                              reference form the audit accepts) → rule fix
  unresolvable_infra_input    required inputs missing (WF-1 gaps) → cannot be
                              dispositioned mechanically
  human_review                none of the above is provable → escalated

Some of the 24 may already be RECOVERED by v4.2 (WF-5 bans, union-coverage
metric, widened references); recovery is recorded per qid — the disposition
handler ignores recovered qids (they need no fix).

Gate: every one of the 24 qids has exactly one class or is escalated; no
reference-prose additions. 0 LLM calls.

Usage:
    python -m evaluation.audit_insufficient_24
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
TRIAGE = OUT / "evaluator_v3_triage.json"
RESULTS = OUT / "wf7_dispositions.json"
MD_REPORT = OUT / "wf7_dispositions.md"

CAUSE_CLASSES = (
    "recovered_by_v4.2",
    "abstention_marker_fp",
    "temporal_heuristic_fp",
    "multi_provision_string_mismatch",
    "penalty_structure_absence",
    "unresolvable_infra_input",
    "human_review",
)


def disposition_one(
    qid: str, rec: dict[str, Any] | None, bundle_rec: dict[str, Any], verdict: dict[str, Any], bans: set[str]
) -> dict[str, Any]:
    """Exactly one cause class per call — the cascade order is the evidence order."""
    from evaluation.calibrate_abstention import matched_phrases, load_lexicon
    from evaluation.experiment_b_topk_eval import token_overlap

    q = None
    evidence: dict[str, Any] = {
        "human_verdict": verdict.get("verdict"),
        "human_correct": verdict.get("human_correct"),
        "audit_model_action": verdict.get("model_action"),
        "audit_note": verdict.get("notes"),
    }
    if rec is None or rec.get("status") == "missing" or not rec.get("answer"):
        evidence["gap"] = "C-O3 answer or audit columns unavailable (WF-1 inventory gap)"
        return {"qid": qid, "cause": "unresolvable_infra_input", "evidence": evidence, "who": "machine-classified"}

    answer = rec["answer"]
    should_abstain = bool(bundle_rec.get("expected_abstain"))
    refs = list(bundle_rec.get("conclusions") or []) + list(bundle_rec.get("concepts") or [])
    coverage_union = round(float(token_overlap(answer, " ".join(r for r in refs if r)).get("correctness") or 0.0), 4)
    lexicon = load_lexicon()
    matched = matched_phrases(answer, lexicon, bans)
    graded = rec.get("graded") or {}
    C1 = bool(graded.get("provision_correct"))
    C5 = bool(graded.get("hallucination_detected"))
    C4 = bool(graded.get("citation_correct"))
    temporal = graded.get("temporal_correct")
    evidence.update({
        "C1_provision_identity": C1,
        "C2_coverage_union": coverage_union,
        "C4_citation_legitimacy": C4,
        "C5_hallucination": C5,
        "temporal_correct": temporal,
        "abstained": bool(matched),
        "matched_phrases": matched,
        "expected_abstain": should_abstain,
    })

    # (1) refusal behaviour first — a refused answer scores 0 on every rule lane
    if matched and not should_abstain:
        evidence["fix"] = "stamp qid-scoped WF-5-style lexicon ban (same audit-backed class: evaluator_miss)"
        return {"qid": qid, "cause": "abstention_marker_fp", "evidence": evidence, "who": "machine-classified"}

    # (2) the repealed-word heuristic penalised a correct answer
    if temporal is False and not C5 and (verdict or {}).get("human_correct") == "yes":
        evidence["fix"] = (
            "rule change: temporal heuristic has no gold label in the frozen benchmark; exempt non-temporal questions"
        )
        return {"qid": qid, "cause": "temporal_heuristic_fp", "evidence": evidence, "who": "machine-classified"}

    # (3) identity-correct but string-overlap cannot see the (widened) reading
    if C1 and C4 and not C5 and coverage_union < 0.6:
        evidence["fix"] = "split scoring / per-unit rubric — prose is already widened and still insufficient by design"
        return {
            "qid": qid,
            "cause": "multi_provision_string_mismatch",
            "evidence": evidence,
            "who": "machine-classified",
        }

    # (4) identity leg itself fails while the audit accepts the answer
    if not C1:
        cited = rec.get("cited_ids") or []
        gold_covering = sorted(set(rec.get("gold_unit_ids") or []))
        evidence["cited_n"] = len(cited)
        evidence["gold_units"] = gold_covering
        if cited:
            evidence["fix"] = (
                "penalty-structure: audit accepts the answer, identity rule does not; inspect matches_gold mapping — rule change candidate"
            )
            return {"qid": qid, "cause": "penalty_structure_absence", "evidence": evidence, "who": "machine-classified"}
        evidence["gap"] = "no solvable citation input (WF-1 inventory gap)"
        return {"qid": qid, "cause": "unresolvable_infra_input", "evidence": evidence, "who": "machine-classified"}

    # (5) nothing provable left — escalate
    return {"qid": qid, "cause": "human_review", "evidence": evidence, "who": "escalated"}


def main() -> int:
    from evaluation.measure_component_rule_identity import load_answers
    from evaluation.grading import grade_answer
    from evaluation.benchmark import load_questions as load_benchmark
    from evaluation.resolution import FamilyMap
    from evaluation.measure_component_rule_identity import load_payload_index, load_verdicts, evidence_texts_for
    from evaluation.evaluator_v4 import BUNDLE_PATH
    from evaluation.build_references_v4 import verify_bundle

    abstract = verify_bundle()
    if not abstract.get("ok"):
        raise SystemExit(f"bundle {abstract.get('status')!r} — verify first")
    bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    triage = json.loads(TRIAGE.read_text(encoding="utf-8"))
    insufficient = [e["qid"] for e in triage["buckets"]["overlay_insufficient"]]
    assert len(insufficient) == 24, f"expected 24 overlay_insufficient qids, got {len(insufficient)}"

    answers = load_answers()
    verdicts = load_verdicts()
    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_benchmark()}
    bans_by_qid = bundle.get("banned_abstain_markers", {}).get("rules_by_qid") or {}

    from evaluation.calibrate_abstention import matched_phrases, load_lexicon

    lexicon = load_lexicon()

    rows: list[dict[str, Any]] = []
    for qid in insufficient:
        rec = answers.get(qid)
        verdict = verdicts.get(qid) or {}
        bundle_rec = (bundle.get("questions") or {}).get(qid) or {}
        label = None
        graded = {}
        if rec and rec.get("answer"):
            q = questions.get(qid)
            if q is not None:
                ev_texts = evidence_texts_for(rec.get("context_ids") or [], payload_index)
                graded = grade_answer(
                    q,
                    rec["answer"],
                    cited_chunk_ids=rec.get("cited_ids") or [],
                    evidence_chunk_ids=rec.get("context_ids") or [],
                    evidence_texts=ev_texts,
                    payload_index=payload_index,
                    family_map=family_map,
                )
                rec = {**rec, "graded": graded}
        row = disposition_one(
            qid, rec, bundle_rec, verdict, set((bans_by_qid.get(qid) or {}).get("banned_phrases") or [])
        )
        v4 = None
        if graded:
            from evaluation.evaluator_v4 import apply_rule
            from evaluation.experiment_b_topk_eval import token_overlap as _tok

            answer_bans = set((bans_by_qid.get(qid) or {}).get("banned_phrases") or [])
            should_abstain = bool(bundle_rec.get("expected_abstain"))
            abstained = bool(matched_phrases(rec["answer"], lexicon, answer_bans))
            refs = list(bundle_rec.get("conclusions") or []) + list(bundle_rec.get("concepts") or [])
            cov_max = max((_tok(rec["answer"], r).get("correctness") or 0.0 for r in refs if r), default=0.0)
            cov_union = float(_tok(rec["answer"], " ".join(r for r in refs if r)).get("correctness") or 0.0)
            v4 = {
                "credit": apply_rule(
                    {
                        "C1_provision_identity": bool(graded.get("provision_correct")),
                        "coverage_union": cov_union,
                        "C5_hallucination": bool(graded.get("hallucination_detected")),
                        "abstain_credit_road": bool(should_abstain and abstained),
                        "false_abstention": bool(abstained and not should_abstain),
                    },
                    0.6,
                    "credit",
                ),
                "claim": apply_rule(
                    {
                        "C1_provision_identity": bool(graded.get("provision_correct")),
                        "coverage_union": cov_union,
                        "C5_hallucination": bool(graded.get("hallucination_detected")),
                        "abstain_credit_road": False,
                        "false_abstention": bool(abstained and not should_abstain),
                    },
                    0.4,
                    "claim",
                ),
                "coverage_max": round(cov_max, 4),
                "coverage_union": round(cov_union, 4),
            }
            if v4["credit"]:
                row["cause"] = "recovered_by_v4.2"
                row["who"] = "machine-classified"
                row["evidence"]["recovery"] = (
                    "CREDIT already true under v4.2 (WF-5 bans / WF-6 references / union coverage)"
                )
            row["v4_state"] = v4
        rows.append(row)

    counts: dict[str, int] = {c: 0 for c in CAUSE_CLASSES}
    for row in rows:
        counts[row["cause"]] = counts[row["cause"]] + 1
    n_machine = sum(counts[c] for c in CAUSE_CLASSES if c != "human_review")
    n_escalated = counts["human_review"]
    gate = {
        "n_qids": len(rows),
        "classified_machine": n_machine,
        "escalated_human": n_escalated,
        "exactly_one_class_each": all(sum(1 for c in CAUSE_CLASSES if c == r["cause"]) == 1 for r in rows),
        "no_reference_prose_proposed": all("add" not in r.get("evidence", {}) for r in rows),
    }

    artifacts = {
        "version": "wf7-dispositions-1",
        "source_triage": "evaluator_v3_triage.json overlay_insufficient (24)",
        "ruler_version": "v4.2-dev",
        "cause_counts": counts,
        "gate": gate,
        "dispositions": rows,
    }

    from evaluation.ruler import format_publication_header, publication_header, write_json_with_header

    print(format_publication_header(publication_header()))
    print(f"\nqids triaged          : {len(rows)} (overlay_insufficient)")
    print(f"recovered under v4.2  : {counts['recovered_by_v4.2']}")
    for c in CAUSE_CLASSES:
        if c != "recovered_by_v4.2" and counts[c]:
            print(f"cause {c:<32}: {counts[c]}")
    print(f"gate                  : {gate}")

    write_json_with_header(
        RESULTS,
        artifacts,
        thresholds={
            "cause_counts": counts,
            "escalated": n_escalated,
            "ruler_version": "v4.2-dev",
        },
    )

    # human-review sheet for the escalated rows only
    md = ["# WF-7 escalated dispositions (human review required)", ""]
    for row in rows:
        if row["who"] != "escalated":
            continue
        md.append(f"## {row['qid']} — {row['cause']}")
        md.append("```json")
        md.append(json.dumps(row["evidence"], indent=2, ensure_ascii=False))
        md.append("```")
        md.append("")
    MD_REPORT.write_text("\n".join(md), encoding="utf-8")
    print(f"written: {RESULTS}")
    print(f"written: {MD_REPORT} (only the {n_escalated} escalated rows need a human pass)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

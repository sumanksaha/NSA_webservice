"""WF-3 — evaluator v4: components, calibrated binaries, scorecard (0 LLM calls).

Builds the ruler that the v1→v3 lineage demanded (design review §5 / workflow
WF-3). Per-answer, deterministic, thresholds calibrated **only** against the
completed human tabulation (rule R3), published both in-sample and on a
deterministic held-out qid split so every operating point carries its basis.

Components (all deterministic, from the ARMED grader of WF-2):
  C1 provision_identity    cited chunks cover a gold unit (payload-key matches_gold)
  C2 conclusion_coverage   TWO published shapes (boot rule R2: control side by side):
                             coverage       = max correctness over individual refs
                                              (frozen v2-shape control — provably
                                              inert to additive widening: 38/53 v2
                                              widenings moved zero, WF-6 14/14 inert)
                             coverage_union = correctness vs the merged multi-ref
                                              text (what additive widening widens)
                           PRIMARY selected on holdout; threshold calibrated t2
  C3 abstention_discipline audit-calibrated lexicon (WF-5) minus the bundle's
                           qid-scoped banned markers (false rate 0.1472→0.0792)
  C4 citation_legitimacy   graded citation_correct (armed)
  C5 hallucination_guard   armed evidence-blob check

Derived (per-answer — F3; no cross-arm OR):
  CREDIT(t2, metric) = C1 OR (row[metric] >= t2 AND NOT C5) OR abstain_lane
  CLAIM(t2, metric)  = C1 AND row[metric] >= t2 AND NOT C5 AND NOT false_abstention
  soft_v4(t2, metric): expected-abstain → abstained ? 1.0 : 0.0;
                       else 1.0 if C1∧cov≥t2∧¬C5, 0.5 if C1 or (cov≥t2∧¬C5), else 0.0
  metric ∈ {"coverage", "coverage_union"}; publish both; primary = holdout-selected

Selector objective per operating point (published, not hidden):
  CREDIT  — recall-first; select by F-beta (beta=2) on the calibration split
  CLAIM   — precision-first; select by F-beta (beta=0.5), report whether the
            staged precision bound is reached (A2/A3 honesty)
  soft    — minimize MAE vs the human partial-credit scale (acceptance A7)

Usage:
    python -m evaluation.evaluator_v4
"""

from __future__ import annotations

import json
import sys
import zlib
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
RESULTS = OUT / "evaluator_v4_scorecard.json"
BUNDLE_PATH = PROJECT_ROOT / "evaluation" / "evaluator_v4_references.json"


def load_bundle() -> dict[str, Any]:
    """Single reference source (WF-4): the frozen bundle with WF-5 lexicon bans."""
    from evaluation.build_references_v4 import verify_bundle

    verdict = verify_bundle()
    if not verdict.get("ok"):
        raise SystemExit(
            f"reference bundle is {verdict.get('status')!r} — a ruler report must not calibrate on a corrupt/absent bundle"
        )
    return json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))


#: deterministic qid split (fahraced hasher — builtin hash() is session-random)
_CALIB_FRACTION = 0.7

T2_GRID = [round(0.05 * i, 2) for i in range(2, 16)]  # 0.10 … 0.75


def split_for(qid: str) -> str:
    return "calibration" if zlib.crc32(qid.encode()) % 100000 < (_CALIB_FRACTION * 100000) else "holdout"


def build_rows() -> list[dict[str, Any]]:
    """Armed component vectors + human verdict for every audited C-O3 answer."""
    from evaluation.grading import grade_answer
    from evaluation.measure_component_rule_identity import (
        load_answers,
        load_payload_index,
        load_verdicts,
    )
    from evaluation.benchmark import load_questions
    from evaluation.resolution import FamilyMap
    from evaluation.calibrate_abstention import load_lexicon, matched_phrases
    from evaluation.experiment_b_topk_eval import token_overlap

    bundle = load_bundle()
    bundle_questions = bundle["questions"]
    bans = bundle.get("banned_abstain_markers") or {}
    rules_by_qid = {
        qid: set(rule.get("banned_phrases") or []) for qid, rule in (bans.get("rules_by_qid") or {}).items()
    }
    carried = bans.get("carried_from") or {}
    for qid in carried.get("v2_overlay_applies_to_qids", []):
        rules_by_qid.setdefault(qid, set()).update(carried.get("v2_overlay_markers") or [])
    lexicon = load_lexicon()

    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}
    answers = load_answers()
    verdicts = load_verdicts()
    ie_mismatch: list[str] = []

    rows: list[dict[str, Any]] = []
    for qid, rec in sorted(answers.items()):
        if rec.get("status") == "missing" or qid not in questions or qid not in verdicts:
            continue
        verdict = verdicts[qid]
        if verdict.get("verdict") == "reference_narrow":
            continue  # reference-set defect; not an answer diagnosis
        q = questions[qid]
        bref = bundle_questions.get(qid) or {}
        if bool(q.insufficient_evidence) != bool(bref.get("expected_abstain")):
            ie_mismatch.append(qid)
        refs = list(bref.get("conclusions") or []) + list(bref.get("concepts") or [])
        texts = evidence_from_none = None  # no shimming: real evidence stays contextual per WF-2
        from evaluation.measure_component_rule_identity import evidence_texts_for

        evidence_texts = evidence_texts_for(rec.get("context_ids") or [], payload_index)
        graded = grade_answer(
            q,
            rec["answer"],
            cited_chunk_ids=rec["cited_ids"],
            evidence_chunk_ids=rec["context_ids"],
            evidence_texts=evidence_texts,
            payload_index=payload_index,
            family_map=family_map,
        )
        # C2 coverage — two published shapes (boot rule R2: v1/v2-frozen control
        # side by side with the amendment):
        #   coverage       : max correctness over individual refs   (frozen v2-shape control)
        #   coverage_union : correctness against the merged multi-ref text — the
        #                    reference SET as one gold statement (what additive
        #                    widening actually widens; max-per-ref is provably
        #                    inert to added refs — 38/53 v2 widenings scored zero delta)
        coverage = 0.0
        for ref in refs:
            if ref:
                coverage = max(coverage, float(token_overlap(rec["answer"], ref).get("correctness") or 0.0))
        coverage_union = float(token_overlap(rec["answer"], " ".join(r for r in refs if r)).get("correctness") or 0.0)
        should_abstain = bool(bref.get("expected_abstain"))
        abstained = bool(matched_phrases(rec["answer"], lexicon, rules_by_qid.get(qid, set())))
        false_abstention = abstained and not should_abstain

        human_partial = {"yes": 1.0, "partial": 0.5, "no": 0.0}.get(str(verdict.get("human_correct") or "no")[:7], 0.0)

        rows.append({
            "qid": qid,
            "verdict": verdict.get("verdict"),
            "human_partial": human_partial,
            "split": split_for(qid),
            "insufficient_evidence": should_abstain,
            "abstained": abstained,
            "abstain_credit_road": bool(should_abstain and abstained),
            "false_abstention": false_abstention,
            "C1_provision_identity": bool(graded.get("provision_correct")),
            "C4_citation_legitimacy": bool(graded.get("citation_correct")),
            "C5_hallucination": bool(graded.get("hallucination_detected")),
            "coverage": round(coverage, 4),
            "coverage_union": round(coverage_union, 4),
            "v1_soft": float((verdicts[qid].get("machine") or {}).get("C-O3", {}).get("v2_soft") or 0.0),
        })
    if ie_mismatch:
        print(f"IE-flag mismatch bundle vs benchmark (published, not silent): {ie_mismatch}")
    return rows


def apply_rule(row: dict[str, Any], t2: float, shape: str, metric: str = "coverage_union") -> bool:
    C1 = row["C1_provision_identity"]
    C2 = row[metric] >= t2
    C5 = row["C5_hallucination"]
    if shape == "credit":
        return bool(C1 or (C2 and not C5) or row["abstain_credit_road"])
    if shape == "claim":
        return bool(C1 and C2 and not C5 and not row["false_abstention"])
    raise ValueError(shape)


def soft_v4(row: dict[str, Any], t2: float, metric: str = "coverage_union") -> float:
    C1 = row["C1_provision_identity"]
    C2 = row[metric] >= t2
    C5 = row["C5_hallucination"]
    if row["insufficient_evidence"]:
        return 1.0 if row["abstained"] else 0.0
    if C1 and C2 and not C5:
        return 1.0
    if C1 or (C2 and not C5):
        return 0.5
    return 0.0


def _fbeta(confusion: dict[str, Any], beta: float) -> float:
    prec, rec = confusion["precision"], confusion["recovery_rate"]
    if prec + rec <= 0:
        return 0.0
    b2 = beta * beta
    return (1 + b2) * prec * rec / (b2 * prec + rec)


def confusion_for(rows: list[dict[str, Any]], shape: str, t2: float, metric: str = "coverage_union") -> dict[str, Any]:
    tp = fp = fn = tn = 0
    for row in rows:
        predicted = apply_rule(row, t2, shape, metric)
        human_right = row["verdict"] == "evaluator_miss"
        if predicted and human_right:
            tp += 1
        elif predicted:
            fp += 1
        elif human_right:
            fn += 1
        else:
            tn += 1
    recovery = tp / max(tp + fn, 1)
    precision = tp / max(tp + fp, 1)
    return {
        "t2": t2,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "recovery_rate": round(recovery, 4),
        "precision": round(precision, 4),
        "f1": round(2 * recovery * precision / max(recovery + precision, 1e-9), 4),
    }


def select_shape(rows: list[dict[str, Any]], shape: str, beta: float, metric: str = "coverage_union") -> dict[str, Any]:
    """Sweep t2 on the calibration split; publish grid + chosen point."""
    calib = [r for r in rows if r["split"] == "calibration"]
    grid = [confusion_for(calib, shape, t2, metric) for t2 in T2_GRID]
    chosen = max(grid, key=lambda row: _fbeta(row, beta))
    return {
        "objective": f"F-beta(beta={beta}) on calibration split (n={len(calib)})",
        "t2_grid": grid,
        "chosen": chosen,
    }


def soft_mae(rows: list[dict[str, Any]], t2: float, metric: str = "coverage_union") -> dict[str, Any]:
    per = [abs(soft_v4(r, t2, metric) - r["human_partial"]) for r in rows]
    return {"t2": t2, "mae": round(sum(per) / max(len(per), 1), 4), "n": len(per)}


def bands(values: list[float]) -> dict[str, int]:
    edges = ((0.45, 0.50), (0.40, 0.45), (0.35, 0.40))
    out = {f"[{a:.2f},{b:.2f})": 0 for a, b in edges}
    out["<0.35"] = 0
    for v in values:
        for (a, b), key in zip(edges, out):
            if a <= v < b:
                out[key] += 1
                break
        else:
            if v < 0.35:
                out["<0.35"] += 1
    return out


def main() -> int:
    rows = build_rows()
    metrics = ("coverage", "coverage_union")
    bundle_v = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))["meta"]["bundle_version"]
    holdout = [r for r in rows if r["split"] == "holdout"]

    def per_metric(metric: str) -> dict[str, Any]:
        credit = select_shape(rows, "credit", beta=2.0, metric=metric)
        claim = select_shape(rows, "claim", beta=0.5, metric=metric)
        soft_rows = [soft_mae(rows, t2, metric) for t2 in T2_GRID]
        soft_best = min(soft_rows, key=lambda row_: row_["mae"])
        return {
            "metric": metric,
            "credit_binary": credit,
            "claim_binary": claim,
            "soft_v4": {"t2_grid": soft_rows, "chosen": soft_best},
            "credit_holdout": confusion_for(holdout, "credit", credit["chosen"]["t2"], metric),
            "claim_holdout": confusion_for(holdout, "claim", claim["chosen"]["t2"], metric),
            "soft_holdout_mae": soft_mae(holdout, soft_best["t2"], metric)["mae"],
            "soft_full_mae": soft_mae(rows, soft_best["t2"], metric)["mae"],
        }

    per = {m: per_metric(m) for m in metrics}
    # PRIMARY metric selected ON THE HOLDOUT (never on the calibration split,
    # whose budget is already spent choosing t2 — double-dipping forbidden).
    # Tie-break pre-declared: equal credit-holdout F-beta → the metric with the
    # lower full-set soft MAE wins (A7 progress without touching binaries).
    primary = min(metrics, key=lambda m: (-_fbeta(per[m]["credit_holdout"], 2.0), per[m]["soft_full_mae"]))
    sel = per[primary]
    credit, claim, soft_best = sel["credit_binary"], sel["claim_binary"], sel["soft_v4"]["chosen"]
    soft_full_mae = per[primary]["soft_full_mae"]

    soft_full_mae = per[primary]["soft_full_mae"]
    # means per metric readers: publish both shapes next to each other
    means = {}
    for verdict in ("evaluator_miss", "model_wrong"):
        sub = [r for r in rows if r["verdict"] == verdict]
        means[verdict] = {"n": len(sub)}
        for m in metrics:
            vals = [soft_v4(r, soft_best["t2"], m) for r in sub]
            means[verdict][f"mean_soft[{m}]"] = round(sum(vals) / max(len(vals), 1), 4)
            covs = [r[m] for r in sub]
            means[verdict][f"mean_coverage[{m}]"] = round(sum(covs) / max(len(covs), 1), 4)

    scorecard = {
        "version": "evaluator-v4-scorecard-2",
        "stage": (
            f"v4.2 (bundle {bundle_v} + WF-6 widening landed, n=14 additive merges with provenance; "
            "two-shape C2 published side by side: frozen max-per-ref control 'coverage' vs reference-SET "
            "'coverage_union'; PRIMARY metric selected on holdout: " + primary + "; overrides v4.1)"
        ),
        "primary_metric": primary,
        "metric_comparison": {
            m: {
                "credit_holdout_fbeta": _fbeta(per[m]["credit_holdout"], 2.0),
                "claim_holdout_fbeta": _fbeta(per[m]["claim_holdout"], 0.5),
                "soft_holdout_mae": per[m]["soft_holdout_mae"],
                "soft_full_mae": per[m]["soft_full_mae"],
            }
            for m in metrics
        },
        "n_rows": len(rows),
        "splits": {"calibration": len(rows) - len(holdout), "holdout": len(holdout)},
        "credit_binary": credit,
        "claim_binary": claim,
        "soft_v4": {"t2_grid": sel["soft_v4"]["t2_grid"], "chosen": soft_best, "mae_full_set": soft_full_mae},
        "holdout_outcomes": {
            "credit_holdout": sel["credit_holdout"],
            "claim_holdout": sel["claim_holdout"],
            "soft_holdout_mae": sel["soft_holdout_mae"],
            "n_holdout": len(holdout),
        },
        "per_verdict_class": means,
        "acceptance": {
            "A7_soft_MAE_le_0.25": (soft_full_mae <= 0.25),
            "A2_claim_precision_note": (
                f"chosen CLAIM point precision={claim['chosen']['precision']}; staged bound is "
                ">=0.60 after WF-6 reference widening — publish honestly whatever this is."
            ),
            "crediting_per_answer": True,
            "no_inherited_shared_threshold": True,
        },
        "inputs_frozen": {"rows_source": "experiment_C_per_question.json/O3_full_support (armed)", "t2_grid": T2_GRID},
    }

    from evaluation.ruler import format_publication_header, publication_header, write_json_with_header

    print(format_publication_header(publication_header()))
    print(f"\nrows={len(rows)} (calibration {len(rows) - len(holdout)} / holdout {len(holdout)})")
    for m in metrics:
        p = per[m]
        c, k = p["credit_binary"]["chosen"], p["claim_binary"]["chosen"]
        print(
            f"[{m:<14}] CREDIT cal t2={c['t2']} rec={c['recovery_rate']:.2f} prec={c['precision']:.2f} · "
            f"CLAIM cal t2={k['t2']} rec={k['recovery_rate']:.2f} prec={k['precision']:.2f} · "
            f"holdout credit fbeta={_fbeta(p['credit_holdout'], 2.0):.2f} - soft A7 MAE={p['soft_full_mae']}"
        )
    print(f"primary metric (holdout-selected): {primary}")
    ch, kh = credit["chosen"], claim["chosen"]
    print(
        f"CREDIT chosen (beta-weighted): t2={ch['t2']}  rec={ch['recovery_rate']:.2f} prec={ch['precision']:.2f} "
        f"tp={ch['true_positive']} fp={ch['false_positive']} fn={ch['false_negative']}"
    )
    print(
        f"CLAIM chosen (beta-weighted): t2={kh['t2']}  rec={kh['recovery_rate']:.2f} prec={kh['precision']:.2f} "
        f"tp={kh['true_positive']} fp={kh['false_positive']} fn={kh['false_negative']}"
    )
    print(
        f"holdout   : credit rec={sel['credit_holdout']['recovery_rate']:.2f} prec={sel['credit_holdout']['precision']:.2f} · "
        f"claim rec={sel['claim_holdout']['recovery_rate']:.2f} prec={sel['claim_holdout']['precision']:.2f}"
    )
    print(
        f"soft v4   : best MAE={soft_best['mae']} (t2={soft_best['t2']}, full-set)  A7 gate (<=0.25): {scorecard['acceptance']['A7_soft_MAE_le_0.25']}"
    )
    for verdict, m in means.items():
        print(
            f"class {verdict:<16}: mean_soft[union]={m['mean_soft[coverage_union]']} "
            f"mean_cov[union]={m['mean_coverage[coverage_union]']} vs control mean_cov={m['mean_coverage[coverage]']} n={m['n']}"
        )
    print()

    write_json_with_header(
        RESULTS,
        scorecard,
        thresholds={
            "primary_metric": primary,
            "credit_t2": credit["chosen"]["t2"],
            "claim_t2": claim["chosen"]["t2"],
            "soft_t2": soft_best["t2"],
            "bundle_version": bundle_v,
            "calibrated_against": "full_review_tabulation.json",
        },
    )
    print(f"written: {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

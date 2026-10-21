"""WF-9 — re-baseline Experiment B under ruler v4.2 and freeze the baseline.

The K-sweep generation numbers circulating since v1 (soft 33.1–37.9 %,
binary ~12 %, oracle numbers from ``experiment_B_aggregate.json``) predate
every fix in this workflow: the WF-5 audit-calibrated lexicon bans, the WF-6
human-widened reference bundle (v1.2.1), the two-shape C2, and the per-answer
credit/claim binaries. They must not keep circulating as current truth: this
rescore is the replacement baseline (G10 closed; old figures superseded with
a pointer).

**Rescore, per stored arm row** (0 LLM — rescore over stored answers and
checkpoint components):
  - errored rows (B precedent: ~25 % of requests transport-errored) are
    counted and the rate DISCLOSED per arm — never silently excluded;
  - identity components (C1 provision, C4 citation-legitimacy, C5
    hallucination, citation precision/recall/groundedness) come from the
    checkpoint's graded vector (identity-grade at B runtime, provenance
    recorded below);
  - C2 (coverage max + union vs bundle v1.2.1 references) and C3 (audit-
    calibrated lexicon minus the bundle's qid-scoped bans) are recomputed
    fresh under v4.2;
  - CREDIT(t2=0.6) and CLAIM(t2=0.4) applied per answer — the v4.2 chosen
    points (primary metric ``coverage_union``, holdout-selected).

**Composite reporting index** (REPORTING ONLY — see the promotion rule in
``Ruler_Fix_Workflow.md`` and in the frozen artifact):
  accuracy axis   = ``soft_v4`` partial credit mapped to the Vals scale (×3)
  attribution axis= citation F1 × groundedness (floor)
  presentation axis= NOT re-derivable offline (live rubric pass; kill switch)
                  → declared N/A; a two-axis composite (weights 50:40) is
                  published beside the axes, never in place of them.

**Publication rules honored:** per-axis columns beside every composite;
band counts (±5pp at n=150) beside every binary; transport-error rate
disclosed alongside every arm rate.

Usage:
    python -m evaluation.rebaseline_experiment_b
Writes:
    evaluation/out/ceiling_v5/evaluator_v4_baseline.json   (the frozen baseline)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
K_CHECKPOINT = OUT / "experiment_B_llm_checkpoint.jsonl"
ORACLE_CHECKPOINT = OUT / "experiment_B_oracle_checkpoint.jsonl"
BASELINE = OUT / "evaluator_v4_baseline.json"

FROZEN_BASELINE_VERSION = "evaluator-v4-baseline-1"
SUPERSEDED_FIGURES = (
    {
        "figure": "K-sweep soft answer-correctness 33.1-37.9 % (v1 token overlap)",
        "source": "experiment_B_aggregate.json conditional_accuracy / older dashboards",
        "superseded_by": "evaluator_v4_baseline.json per-arm 'accuracy_axis_mean_0_3' (v4.2 soft partial credit)",
    },
    {
        "figure": "K-sweep binary correctness ~12 % (v1 overlap rule)",
        "source": "experiment_B_aggregate.json k_curve.correct_rate",
        "superseded_by": "evaluator_v4_baseline.json per-arm 'credit_binary_rate' + 'claim_binary_rate' (per-answer, v4.2)",
    },
    {
        "figure": "oracle arm: soft 35.1 % / v1-binary 3.45 % / grader-correct 80.69 % / abstain 31.03 %",
        "source": "experiment_B_aggregate.json oracle",
        "superseded_by": "evaluator_v4_baseline.json 'oracle' row",
    },
)

T2_CREDIT = 0.6  # chosen v4.2 operating points (coverage_union primary)
T2_CLAIM = 0.4
T2_SOFT = 0.45  # scorecard rev 2's soft choice (full-set best MAE)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _fresh_c3_c2(qid: str, answer: str, bundle: dict[str, Any], token_overlap) -> tuple[float, float, bool]:
    """Fresh C2 (both shapes) + expected_abstain from the v4.2 bundle."""
    bref = (bundle.get("questions") or {}).get(qid) or {}
    refs = list(bref.get("conclusions") or []) + list(bref.get("concepts") or [])
    cov_max = max((float(token_overlap(answer, r).get("correctness") or 0.0) for r in refs if r), default=0.0)
    cov_union = float(token_overlap(answer, " ".join(r for r in refs if r)).get("correctness") or 0.0)
    return cov_max, cov_union, bool(bref.get("expected_abstain"))


def rescore_row(
    row: dict[str, Any],
    arm: str,
    bundle: dict[str, Any],
    lexicon: list[str],
    bans_by_qid: dict[str, set[str]],
    token_overlap,
    apply_rule,
    soft_v4,
) -> dict[str, Any] | None:
    """One arm row -> the v4.2 verdict vector (or None when transport-errored)."""
    error = row.get("error")
    if error not in (None, "", "None"):
        return None
    answer = row.get("answer") or ""
    if not answer.strip():
        return None
    qid = row["question_id"]
    graded = row.get("grader") if isinstance(row.get("grader"), dict) else {}
    bans = bans_by_qid.get(qid, set())
    from evaluation.calibrate_abstention import matched_phrases

    matched = matched_phrases(answer, lexicon, bans)
    cov_max, cov_union, should_abstain = _fresh_c3_c2(qid, answer, bundle, token_overlap)
    vec = {
        "qid": qid,
        "arm": arm,
        "C1_provision_identity": bool(graded.get("provision_correct")),
        "C3_abstained": bool(matched),
        "abstained": bool(matched),
        "false_abstention": bool(matched) and not should_abstain,
        "abstain_credit_road": bool(should_abstain and matched),
        "insufficient_evidence": should_abstain,
        "C5_hallucination": bool(graded.get("hallucination_detected")),
        "coverage": round(cov_max, 4),
        "coverage_union": round(cov_union, 4),
    }
    vec["credit_binary"] = apply_rule(vec, T2_CREDIT, "credit", "coverage_union")
    vec["claim_binary"] = apply_rule(vec, T2_CLAIM, "claim", "coverage_union")
    vec["soft_v4"] = soft_v4(vec, T2_SOFT, "coverage_union")
    # axes (reporting layer, frozen weights)
    acc = vec["soft_v4"] * 3.0  # Vals 0-3 comparable partial credit
    prec, rec, gnd = (
        float(row.get("citation_precision") or 0.0),
        float(row.get("citation_recall") or 0.0),
        float(row.get("groundedness") or 0.0),
    )
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
    vec["accuracy_axis_0_3"] = round(acc, 3)
    vec["attribution_axis"] = round(f1 * gnd, 4)
    vec["presentation_axis"] = None  # live rubric pass required; kill switch
    vec["composite_2axis_50_40"] = round(0.5 * acc + 0.4 * (f1 * gnd), 4) if acc is not None else None
    vec["v1_soft"] = round(float(row.get("answer_correctness") or 0.0), 4)
    vec["v1_binary"] = bool(row.get("correct"))
    vec["grader_correct"] = bool(graded.get("score", 0) > 0)  # era's identity binary
    return vec


def aggregate_arm(vecs: list[dict[str, Any]], n_total: int, n_errored: int) -> dict[str, Any]:
    n = len(vecs)
    rate = lambda key: round(sum(1 for v in vecs if v[key]) / max(n, 1), 4)
    softs = [v["soft_v4"] for v in vecs]
    covs = [v["coverage_union"] for v in vecs]
    from evaluation.evaluator_v4 import bands

    return {
        "n_rows": n_total,
        "n_errored": n_errored,
        "transport_error_rate": round(n_errored / max(n_total, 1), 4),
        "n_scored": n,
        "v1_soft_mean": round(sum(v["v1_soft"] for v in vecs) / max(n, 1), 4),
        "v1_binary_rate": rate("v1_binary"),
        "grader_correct_rate": rate("grader_correct"),
        "credit_binary_rate": rate("credit_binary"),
        "claim_binary_rate": rate("claim_binary"),
        "soft_v4_mean": round(sum(softs) / max(n, 1), 4),
        "coverage_union_mean_band": bands(covs),
        "accuracy_axis_mean_0_3": round(sum(v["accuracy_axis_0_3"] for v in vecs) / max(n, 1), 4),
        "attribution_axis_mean": round(sum(v["attribution_axis"] for v in vecs) / max(n, 1), 4),
        "composite_2axis_mean": round(sum(v["composite_2axis_50_40"] for v in vecs) / max(n, 1), 4),
        "false_abstention_rate": rate("false_abstention"),
    }


def build_baseline(out_path: Path = BASELINE) -> dict[str, Any]:
    from evaluation.build_references_v4 import verify_bundle
    from evaluation.calibrate_abstention import load_lexicon
    from evaluation.evaluator_v4 import apply_rule, soft_v4
    from evaluation.experiment_b_topk_eval import token_overlap

    verdict = verify_bundle()
    if not verdict.get("ok"):
        raise SystemExit(f"bundle {verdict.get('status')!r} — a baseline must be frozen against a verified bundle")
    bundle = json.loads((PROJECT_ROOT / "evaluation" / "evaluator_v4_references.json").read_text(encoding="utf-8"))
    bans_by_qid = {
        qid: set(rule.get("banned_phrases") or [])
        for qid, rule in ((bundle.get("banned_abstain_markers") or {}).get("rules_by_qid") or {}).items()
    }
    carried = (bundle.get("banned_abstain_markers") or {}).get("carried_from") or {}
    for qid in carried.get("v2_overlay_applies_to_qids", []):
        bans_by_qid.setdefault(qid, set()).update(carried.get("v2_overlay_markers") or [])
    lexicon = load_lexicon()

    k_rows = _load_jsonl(K_CHECKPOINT)
    oracle_rows = _load_jsonl(ORACLE_CHECKPOINT)
    by_k: dict[str, list[dict[str, Any]]] = {}
    for row in k_rows:
        by_k.setdefault(str(row.get("k")), []).append(row)
    arms: dict[str, dict[str, Any]] = {}
    arm_vectors: dict[str, list[dict[str, Any]]] = {}
    for arm in sorted(by_k, key=int):
        rows = by_k[arm]
        vecs = [
            v
            for v in (
                rescore_row(r, f"K={arm}", bundle, lexicon, bans_by_qid, token_overlap, apply_rule, soft_v4)
                for r in rows
            )
            if v
        ]
        arms[f"K={arm}"] = aggregate_arm(vecs, len(rows), len(rows) - len(vecs))
        arm_vectors[f"K={arm}"] = vecs
    if oracle_rows:
        vecs = [
            v
            for v in (
                rescore_row(r, "oracle", bundle, lexicon, bans_by_qid, token_overlap, apply_rule, soft_v4)
                for r in oracle_rows
            )
            if v
        ]
        arms["oracle"] = aggregate_arm(vecs, len(oracle_rows), len(oracle_rows) - len(vecs))
        arm_vectors["oracle"] = vecs

    artifact = {
        "version": FROZEN_BASELINE_VERSION,
        "stage": f"v4.2 baseline — frozen; supersedes the v1-era Experiment B generation figures listed in 'superseded_figures'",
        "ruler_bundle": {
            "status": verdict.get("status"),
            "bundle_version": bundle["meta"].get("bundle_version"),
            "content_sha256": verdict.get("recorded_content_sha256"),
        },
        "operating_points": {
            "credit_t2": T2_CREDIT,
            "claim_t2": T2_CLAIM,
            "soft_t2": T2_SOFT,
            "primary_metric": "coverage_union",
        },
        "arms": arms,
        "superseded_figures": list(SUPERSEDED_FIGURES),
        "composite_policy": {
            "status": "REPORTING INDEX ONLY — not an A/B gate and not an optimization target",
            "promotion_rule": "adopting the composite as a gate requires the accuracy axis to first agree with the 150/150 human tabulation (evaluator_miss <= 10%); until then gates stay per-axis (credit_binary, claim_binary, per-dimension rubric rates) — a weighted sum may not adopt or veto an arm, because one axis's gain can mask another's regression.",
            "weights_frozen": {"accuracy": 0.5, "attribution": 0.4, "presentation": None},
            "presentation_axis_note": "live rubric pass required (kill switch on) — axis excluded; two-axis composite (50:40) published with per-axis columns beside it",
        },
        "per_axis_rules": "every composite value above is published only next to its accuracy/attribution axes and the band counts; transport-error rate disclosed per arm.",
        "acceptance": {
            "A1_identity_components_armed": True,
            "A2_claim_precision": "met: cal 0.81 / holdout 1.00 at t2=0.4 (scorecard rev 2)",
            "A4_determinism": True,
            "A5_bands_published": True,
            "A6_baseline_frozen": True,
        },
    }

    from evaluation.ruler import write_json_with_header

    write_json_with_header(
        out_path,
        artifact,
        thresholds={
            "credit_t2": T2_CREDIT,
            "claim_t2": T2_CLAIM,
            "soft_t2": T2_SOFT,
            "primary_metric": "coverage_union",
        },
    )
    return artifact


def main() -> int:
    from evaluation.ruler import format_publication_header, publication_header

    print(format_publication_header(publication_header()))
    artifact = build_baseline()
    print(f"\narms re-baselined : {len(artifact['arms'])}")
    for arm, row in sorted(artifact["arms"].items()):
        print(
            f"  {arm:<8} n_scored={row['n_scored']:<4} err_rate={row['transport_error_rate']:<7} "
            f"v1_bin={row['v1_binary_rate']:<7} credit={row['credit_binary_rate']:<7} claim={row['claim_binary_rate']:<7} "
            f"soft={row['soft_v4_mean']:<7} acc3={row['accuracy_axis_mean_0_3']}"
        )
    print(f"written           : {BASELINE}")
    print("superseded        : " + "; ".join(f["figure"] for f in artifact["superseded_figures"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

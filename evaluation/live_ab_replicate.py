"""Live A/B under ruler v4.2 — 3-replicate, paired-gate (boot rule 6).

The first live-LLM measurement the workflow authorises: the kill switch is
re-enabled EXPLICITLY for this run only (``LLM_ENABLED=true`` +
``MAX_LLM_CALLS`` — the caller sets both; this module refuses to start a real
run without them, and runs the stub path otherwise).

Design (the WF-8 replicate discipline, executed live):
  - ONE live arm: K=10 (the frozen Experiment B configuration — CE ranking
    cache, context builder, prompt, model ``poolside/laguna-s-2.1:free``,
    temperature 0.1 — all reused unchanged via ``experiment_b_topk_eval``);
  - 20 questions, deterministically chosen: the first 12 calibration-split and
    first 8 holdout-split qids of the v4.2 scorecard rows (no cherry-picking);
  - 3 REPLICATES (r1/r2/r3) of the identical arm — boot rule 6;
  - scoring: every replicate row re-graded fresh under v4.2 (armed grader +
    bundle v1.2.1 coverage both shapes + WF-5 bans) and the per-answer
    credit/claim/soft applied at the frozen operating points;
  - PAIRED GATE: CLAIM on the CALIBRATION cohort per replicate vs the frozen
    baseline's K=10 CLAIM rate on the SAME qids; the holdout cohort is
    reported untouched (no gate on it) — rule 6 verbatim;
  - NOISE: cross-replicate spread (min/max) published per metric — a live
    number without its noise band is exactly the G9 failure the rule bans.

Everything lands in ``evaluation/out/ceiling_v5/live_ab_v4_2.json`` via the
publication header (deterministic fields; the live latencies live only in the
per-row payload).

Usage:
    python -m evaluation.live_ab_replicate --run        # needs the env opt-in
    python -m evaluation.live_ab_replicate --score      # rescore checkpoints only
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
AB_CKPT = OUT / "live_ab_replicates.jsonl"
RESULTS = OUT / "live_ab_v4_2.json"
BASELINE = OUT / "evaluator_v4_baseline.json"

ARM_K = 10
N_CAL, N_HOLD = 12, 8
REPLICATES = ("r1", "r2", "r3")


def pick_qids() -> dict[str, list[str]]:
    """First-N deterministic cohort split (published, never re-picked)."""
    from evaluation.evaluator_v4 import build_rows

    rows = build_rows()
    cal = [r["qid"] for r in rows if r["split"] == "calibration"][:N_CAL]
    hold = [r["qid"] for r in rows if r["split"] == "holdout"][:N_HOLD]
    return {"calibration": cal, "holdout": hold}


def baseline_reference(qids: list[str]) -> dict[str, Any]:
    """The frozen baseline's K=10 numbers restricted to the SAME qids (paired)."""
    from evaluation.rebaseline_experiment_b import rescore_row, _load_jsonl, T2_CREDIT, T2_CLAIM
    from evaluation.build_references_v4 import verify_bundle
    from evaluation.calibrate_abstention import load_lexicon
    from evaluation.evaluator_v4 import apply_rule, soft_v4
    from evaluation.experiment_b_topk_eval import token_overlap

    bundle = json.loads((PROJECT_ROOT / "evaluation" / "evaluator_v4_references.json").read_text(encoding="utf-8"))
    bans_by_qid = {
        q: set(rule.get("banned_phrases") or [])
        for q, rule in ((bundle.get("banned_abstain_markers") or {}).get("rules_by_qid") or {}).items()
    }
    lexicon = load_lexicon()
    wanted = set(qids)
    vecs = []
    for row in _load_jsonl(OUT / "experiment_B_llm_checkpoint.jsonl"):
        if str(row.get("k")) != str(ARM_K) or row["question_id"] not in wanted:
            continue
        vec = rescore_row(row, "K=10-paired", bundle, lexicon, bans_by_qid, token_overlap, apply_rule, soft_v4)
        if vec:
            vecs.append(vec)
    return {
        "n_scored": len(vecs),
        "claim_rate": round(sum(1 for v in vecs if v["claim_binary"]) / max(len(vecs), 1), 4),
        "credit_rate": round(sum(1 for v in vecs if v["credit_binary"]) / max(len(vecs), 1), 4),
        "soft_mean": round(sum(v["soft_v4"] for v in vecs) / max(len(vecs), 1), 4),
    }


def run_live(cohorts: dict[str, list[str]], opt_in: tuple[bool, int] | None = None) -> int:
    """3 replicates of the K=10 arm over the cohort — REAL calls only on opt-in.

    ``opt_in`` (live, budget) MUST be captured by the CLI entry point BEFORE
    any evaluation import: the Experiment-B import chain re-runs
    ``load_dotenv(override=True)`` (via eval_e2e_v2), which overwrites the
    calling environment with whatever ``.env`` ships — and this machine's
    ``.env`` carries a standing ``LLM_ENABLED=true`` + ``MAX_LLM_CALLS=700``.
    The gate judges the caller's explicit opt-in, never the post-import
    environ state.
    """
    import os

    if opt_in is None:  # direct/test callers: read the env before our own imports
        live = os.environ.get("LLM_ENABLED", "false").strip().lower() == "true"
        try:
            budget = int(os.environ.get("MAX_LLM_CALLS", "0"))
        except ValueError:
            budget = 0
    else:
        live, budget = opt_in
    needed = len(cohorts["calibration"]) + len(cohorts["holdout"])
    if not live or budget < needed:
        print(
            f"REFUSING real run: LLM_ENABLED={live}, MAX_LLM_CALLS={budget}. "
            f"Set LLM_ENABLED=true and MAX_LLM_CALLS>={needed} (in the calling "
            f"environment, before import) for this run.",
            flush=True,
        )
        return 2

    from evaluation.experiment_b_topk_eval import (
        _attach_ce_ranked,
        _llm_task,
        _load_ce_cache,
        _load_gold_index,
        _make_client,
    )
    from evaluation.benchmark import load_questions
    from evaluation.measure_component_rule_identity import load_payload_index
    from evaluation.resolution import FamilyMap

    os.environ.setdefault("RAG_USE_STUB_LLM", "false")

    ce_cache = _load_ce_cache()
    _attach_ce_ranked(ce_cache)
    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}
    gold_index = _load_gold_index(payload_index, family_map)
    client = _make_client(False)

    done = set()
    if AB_CKPT.exists():
        for line in AB_CKPT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                done.add((rec["replicate"], rec["question_id"]))

    tasks = [
        (rep, qid)
        for rep in REPLICATES
        for qid in cohorts["calibration"] + cohorts["holdout"]
        if (rep, qid) not in done
    ]
    print(f"live A/B: {len(tasks)} calls ({len(done)}/60 checkpointed) | budget={budget} | K={ARM_K}", flush=True)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    lock = threading.Lock()
    AB_CKPT.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {
            ex.submit(
                _llm_task,
                qid,
                ARM_K,
                questions[qid],
                ce_cache["questions"][qid],
                payload_index,
                family_map,
                gold_index,
                client,
            ): (rep, qid)
            for rep, qid in tasks
            if qid in ce_cache["questions"]
        }
        for fut in as_completed(futures):
            rep, qid = futures[fut]
            try:
                rec = fut.result()
            except Exception as exc:
                rec = {"question_id": qid, "k": ARM_K, "error": repr(exc)}
            rec["replicate"] = rep
            with lock, open(AB_CKPT, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"  [{rep}] {qid}: {'ERR ' + str(rec.get('error'))[:60] if rec.get('error') else 'ok'}", flush=True)
    return 0


def score(cohorts: dict[str, list[str]] | None = None) -> int:
    """Rescore every checkpointed replicate under v4.2 + paired gate + noise."""
    from evaluation.build_references_v4 import verify_bundle
    from evaluation.calibrate_abstention import load_lexicon
    from evaluation.evaluator_v4 import apply_rule, soft_v4
    from evaluation.experiment_b_topk_eval import token_overlap
    from evaluation.grading import grade_answer
    from evaluation.measure_component_rule_identity import evidence_texts_for, load_payload_index
    from evaluation.benchmark import load_questions as load_benchmark
    from evaluation.resolution import FamilyMap

    verdict = verify_bundle()
    if not verdict.get("ok"):
        raise SystemExit("bundle must be frozen before scoring")
    bundle = json.loads((PROJECT_ROOT / "evaluation" / "evaluator_v4_references.json").read_text(encoding="utf-8"))
    bans_by_qid = {
        q: set(rule.get("banned_phrases") or [])
        for q, rule in ((bundle.get("banned_abstain_markers") or {}).get("rules_by_qid") or {}).items()
    }
    lexicon = load_lexicon()
    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_benchmark()}

    if cohorts is None:
        cohorts = pick_qids()
    base_cal = baseline_reference(cohorts["calibration"])

    if not AB_CKPT.exists():
        print("no replicate checkpoint yet — running --score would publish an empty gate; refusing (run --run first)")
        return 2
    rows = [json.loads(l) for l in AB_CKPT.read_text(encoding="utf-8").splitlines() if l.strip()]
    per_replicate: dict[str, dict[str, Any]] = {}
    scored_rows = []
    for rep in REPLICATES:
        rep_rows = [r for r in rows if r.get("replicate") == rep]
        vecs, errors = [], 0
        for r in rep_rows:
            qid = r["question_id"]
            if r.get("error") not in (None, "", "None") or not (r.get("answer") or "").strip():
                errors += 1
                continue
            answer = r["answer"]
            q = questions.get(qid)
            if q is None:
                errors += 1
                continue
            graded = grade_answer(
                q,
                answer,
                cited_chunk_ids=r.get("cited_chunk_ids") or [],
                evidence_chunk_ids=r.get("context_ids") or [],
                evidence_texts=evidence_texts_for(r.get("context_ids") or [], payload_index),
                payload_index=payload_index,
                family_map=family_map,
            )
            bans = bans_by_qid.get(qid, set())
            from evaluation.calibrate_abstention import matched_phrases

            abstained = bool(matched_phrases(answer, lexicon, bans))
            should_abstain = bool(((bundle.get("questions") or {}).get(qid) or {}).get("expected_abstain"))
            refs = list((((bundle.get("questions") or {}).get(qid) or {}).get("conclusions")) or []) + list(
                (((bundle.get("questions") or {}).get(qid) or {}).get("concepts")) or []
            )
            cov_max = max(
                (float(token_overlap(answer, ref).get("correctness") or 0.0) for ref in refs if ref), default=0.0
            )
            cov_union = float(token_overlap(answer, " ".join(x for x in refs if x)).get("correctness") or 0.0)
            vec = {
                "qid": qid,
                "replicate": rep,
                "C1_provision_identity": bool(graded.get("provision_correct")),
                "abstained": abstained,
                "abstain_credit_road": bool(should_abstain and abstained),
                "insufficient_evidence": should_abstain,
                "false_abstention": bool(abstained and not should_abstain),
                "C5_hallucination": bool(graded.get("hallucination_detected")),
                "coverage": round(cov_max, 4),
                "coverage_union": round(cov_union, 4),
            }
            vec["credit_binary"] = apply_rule(vec, 0.6, "credit", "coverage_union")
            vec["claim_binary"] = apply_rule(vec, 0.4, "claim", "coverage_union")
            vec["soft_v4"] = soft_v4(vec, 0.45, "coverage_union")
            vecs.append(vec)
        split_cal = [v for v in vecs if v["qid"] in set(cohorts["calibration"])]
        split_hold = [v for v in vecs if v["qid"] in set(cohorts["holdout"])]
        rate = lambda vs, key: round(sum(1 for x in vs if x[key]) / max(len(vs), 1), 4)
        per_replicate[rep] = {
            "n_scored": len(vecs),
            "n_errored": errors,
            "claim_cal": rate(split_cal, "claim_binary"),
            "credit_cal": rate(split_cal, "credit_binary"),
            "soft_cal": round(sum(v["soft_v4"] for v in split_cal) / max(len(split_cal), 1), 4),
            "claim_holdout": rate(split_hold, "claim_binary"),
            "credit_holdout": rate(split_hold, "credit_binary"),
        }
        scored_rows += vecs

    claims = [per_replicate[r]["claim_cal"] for r in REPLICATES if per_replicate[r]["n_scored"]]
    paired_gate = {
        "rule": "boot rule 6: CLAIM must improve on the calibration cohort; holdout untouched",
        "baseline_claim_cal_paired": base_cal["claim_rate"],
        "baseline_n": base_cal["n_scored"],
        "live_claim_cal_by_replicate": claims,
        "live_claim_cal_mean": round(sum(claims) / max(len(claims), 1), 4) if claims else None,
        "verdict": None,
    }
    if claims:
        mean_claim = sum(claims) / len(claims)
        paired_gate["verdict"] = (
            "IMPROVED"
            if mean_claim > base_cal["claim_rate"]
            else ("TIED" if mean_claim == base_cal["claim_rate"] else "NOT_IMPROVED")
        )
    noise = {
        "claim_cal_min_max": [min(claims), max(claims)] if claims else None,
        "credit_cal_by_replicate": [per_replicate[r]["credit_cal"] for r in REPLICATES],
        "note": "a live figure without its cross-replicate band is the G9 failure mode",
    }
    artifact = {
        "version": "live-ab-v4.2-1",
        "arm": f"K={ARM_K} live, model poolside/laguna-s-2.1:free, temp 0.1 (frozen B config)",
        "cohorts": cohorts,
        "baseline_reference_cal": base_cal,
        "per_replicate": per_replicate,
        "paired_gate": paired_gate,
        "noise": noise,
        "rows": scored_rows,
        "transport_errors": sum(r.get("n_errored", 0) for r in per_replicate.values()),
    }
    from evaluation.ruler import write_json_with_header

    write_json_with_header(
        RESULTS,
        artifact,
        thresholds={
            "credit_t2": 0.6,
            "claim_t2": 0.4,
            "primary_metric": "coverage_union",
            "replicates": len(REPLICATES),
            "paired_gate_cohort": "calibration",
        },
    )
    print(f"\nbaseline K=10 claim (paired cal cohort): {base_cal['claim_rate']} (n={base_cal['n_scored']})")
    for rep in REPLICATES:
        print(
            f"  {rep}: claim_cal={per_replicate[rep]['claim_cal']} credit_cal={per_replicate[rep]['credit_cal']} (n={per_replicate[rep]['n_scored']}, err={per_replicate[rep]['n_errored']})"
        )
    print(f"paired gate: {paired_gate['verdict']} | noise claim band {noise['claim_cal_min_max']}")
    print(f"written: {RESULTS}")
    return 0


def main() -> int:
    import argparse
    import os

    # Capture the explicit opt-in BEFORE pick_qids(): that call imports the
    # Experiment-B chain, which re-runs load_dotenv(override=True) from .env
    # (eval_e2e_v2) and would mask the caller's environment.
    _live = os.environ.get("LLM_ENABLED", "false").strip().lower() == "true"
    try:
        _budget = int(os.environ.get("MAX_LLM_CALLS", "0"))
    except ValueError:
        _budget = 0

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", action="store_true", help="execute the live replicates (needs the env opt-in)")
    ap.add_argument("--score", action="store_true", help="rescore checkpoints + paired gate")
    ap.add_argument("--all", action="store_true", help="run then score")
    args = ap.parse_args()

    cohorts = pick_qids()
    if args.run or args.all:
        rc = run_live(cohorts, (_live, _budget))
        if rc:
            return rc
    if args.score or args.all:
        return score(cohorts)
    if not (args.run or args.score or args.all):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

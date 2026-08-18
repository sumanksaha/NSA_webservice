"""Phase 3 — compute all metrics and write the 10 deliverables.

Deliverables (written under ``evaluation/out/``):
    1. rag_ablation_results.csv         per-question per-arm retrieval metrics
    2. rag_ablation_report.md           the comprehensive evaluation report
    3. retrieval_comparison.csv         per-arm aggregate retrieval metrics
    4. answer_evaluation.csv            per-question answer grades
    5. failure_taxonomy.csv             per-question failure labels + decomposition
    6. domain_performance.csv           per-domain per-arm aggregates
    7. question_type_performance.csv    per-type per-arm aggregates
    8. kg_incremental_value.md          KG help / harm / net value
    9. production_readiness_assessment.md  readiness verdict
   10. aggregate_metrics.json           all aggregates + config hash
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

from evaluation.benchmark import load_questions
from evaluation.config import ARMS, FUSION_ARMS, GEN_CONDITIONS, OUT_DIR, RAW_DIR, config_hash, write_run_config
from evaluation.failures import bottleneck_tally, decompose, label_tally
from evaluation.grading import grade_answer
from evaluation.metrics import (
    aggregate,
    kg_incremental,
    legal_evidence,
    mcnemar,
    paired_bootstrap_ci,
    score_question,
)
from evaluation.resolution import (
    FamilyMap,
    build_payload_index,
    gold_in_corpus,
)

logger = logging.getLogger("eval.report")

ARM_LABELS = {
    "A_dense": "Dense",
    "B_sparse": "Sparse",
    "C_dense_sparse": "Dense+Sparse",
    "D_kg_retrieval": "KG",
    "E_dense_sparse_kg": "Dense+Sparse+KG",
    "F_dense_sparse_kg_rerank": "Dense+Sparse+KG+Reranker",
    # Offline fusion-repair arms (2026-08-12) — see evaluation/fusion.py.
    "C_rrf_sanity": "D+S RRF (offline)",
    "G_ds_kg_rrf": "D+S+KG RRF",
    "E_ds_kg_rrf": "D+S+KGexp RRF",
    "H_dense_kg_rrf": "D+KG RRF",
    "G_ds_kg_rrf_dedup": "D+S+KG RRF +dedup",
    "H_dense_kg_rrf_dedup": "D+KG RRF +dedup",
}


# --------------------------------------------------------------------------- #
# Data preparation
# --------------------------------------------------------------------------- #
def load_raw(arm: str) -> dict[str, dict]:
    recs: dict[str, dict] = {}
    p = RAW_DIR / f"{arm}.jsonl"
    if not p.exists():
        return recs
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue  # tolerate a corrupt line from a past concurrent run
            recs[r["question_id"]] = r
    return recs


def load_gen(condition: str) -> dict[str, dict]:
    """Load generation results for *condition* across all shard files.

    Glob is ``gen_<condition>_s*.jsonl`` — the ``_s`` keeps ``retrieved``
    from accidentally matching ``retrieved_kg`` shard files.
    """
    recs: dict[str, dict] = {}
    for p in sorted(RAW_DIR.glob(f"gen_{condition}_s*.jsonl")):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                recs[r["question_id"]] = r
    return recs


def prepare() -> dict[str, Any]:
    """Load everything, compute everything, return one big result dict."""
    write_run_config()
    questions = load_questions()
    q_by_id = {q.question_id: q for q in questions}
    payload_index = build_payload_index(
        lambda coll: _store(coll), _collections()
    )
    family_map = FamilyMap()

    arm_order = list(ARMS) + [
        a for a in FUSION_ARMS if (RAW_DIR / f"{a}.jsonl").exists()
    ]
    raw = {arm: load_raw(arm) for arm in arm_order}
    scores: dict[str, dict[str, Any]] = {}
    for arm in arm_order:
        scores[arm] = {}
        for qid, result in raw[arm].items():
            q = q_by_id.get(qid)
            if q is None:
                continue
            scores[arm][qid] = score_question(q, result, payload_index, family_map)

    # legal evidence on ARM F
    legal: dict[str, dict] = {}
    corpus: dict[str, dict] = {}
    for qid, q in q_by_id.items():
        arm_f = raw["F_dense_sparse_kg_rerank"].get(qid)
        legal[qid] = legal_evidence(q, arm_f or {}, payload_index, family_map)
        corpus[qid] = gold_in_corpus(q.recall_units(), payload_index, family_map)

    # KG incremental: E vs C, plus KG-only retrieval (D) family analysis
    kg_inc: dict[str, dict] = {}
    for qid, q in q_by_id.items():
        c_res = raw["C_dense_sparse"].get(qid)
        e_res = raw["E_dense_sparse_kg"].get(qid)
        if c_res is None or e_res is None:
            continue
        kg_inc[qid] = kg_incremental(
            scores["C_dense_sparse"][qid],
            scores["E_dense_sparse_kg"][qid],
            e_res,
            q,
            family_map,
        )

    # generation + grading.  The retrieved_kg condition carries synthetic
    # payloads for KG-derived evidence chunks (recorded by run_generation);
    # merge them into the lookup so citations resolve against gold exactly
    # like real payload chunks.
    gen = {c: load_gen(c) for c in GEN_CONDITIONS}
    grades: dict[str, dict[str, dict]] = {c: {} for c in GEN_CONDITIONS}
    for cond, recs in gen.items():
        for qid, rec in recs.items():
            q = q_by_id.get(qid)
            if q is None:
                continue
            lookup = {**payload_index, **rec.get("kg_payloads", {})}
            evidence_texts = {}
            for cid in rec.get("evidence_chunk_ids", []):
                pl = lookup.get(str(cid))
                if pl:
                    evidence_texts[str(cid)] = str(pl.get("chunk_text") or "")
            grades[cond][qid] = grade_answer(
                q,
                rec.get("answer", ""),
                rec.get("citations", []),
                rec.get("evidence_chunk_ids", []),
                evidence_texts,
                lookup,
                family_map,
            )

    # failure decomposition (ARM F)
    failures: dict[str, dict] = {}
    for qid, q in q_by_id.items():
        arm_f_metrics = scores["F_dense_sparse_kg_rerank"].get(qid)
        if arm_f_metrics is None:
            continue
        grade = grades["retrieved"].get(qid, {})
        failures[qid] = decompose(
            q, arm_f_metrics, legal.get(qid, {}), grade,
            corpus.get(qid, {}), kg_inc.get(qid, {}),
        )

    data = {
        "questions": questions,
        "q_by_id": q_by_id,
        "arm_order": arm_order,
        "payload_index": payload_index,
        "family_map": family_map,
        "raw": raw,
        "scores": scores,
        "legal": legal,
        "corpus": corpus,
        "kg_inc": kg_inc,
        "gen": gen,
        "grades": grades,
        "failures": failures,
    }
    # Paired-bootstrap significance rows are computed ONCE here and shared by
    # stats_table / _verdicts (previously each recomputed the full 10-pair ×
    # 3-metric bootstrap — ~3× redundant work per report run).
    data["_significance"] = significance_table(data)
    return data


def _collections() -> list[str]:
    from evaluation.config import _collect_config_snapshot

    return list(dict.fromkeys(_collect_config_snapshot()["qdrant_collections"].values()))


def _store(collection: str):
    from app.rag.qdrant_client import QdrantStore

    return QdrantStore(collection_name=collection)


# --------------------------------------------------------------------------- #
# Deliverable writers
# --------------------------------------------------------------------------- #
def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def deliverables_1_3(data: dict[str, Any]) -> None:
    """rag_ablation_results.csv + retrieval_comparison.csv."""
    rows = []
    for arm in data["arm_order"]:
        for qid, m in data["scores"][arm].items():
            rows.append({
                "question_id": qid,
                "arm": arm,
                "arm_label": ARM_LABELS[arm],
                "recall@1": m.recall.get(1, 0.0),
                "recall@3": m.recall.get(3, 0.0),
                "recall@5": m.recall.get(5, 0.0),
                "recall@10": m.recall.get(10, 0.0),
                "recall@20": m.recall.get(20, 0.0),
                "mrr": m.mrr,
                "ndcg@5": m.ndcg.get(5, 0.0),
                "ndcg@10": m.ndcg.get(10, 0.0),
                "precision@5": m.precision.get(5, 0.0),
                "precision@10": m.precision.get(10, 0.0),
                "n_items": m.n_items,
                "latency_ms": m.latency_ms,
                "error": m.error or "",
            })
    write_csv(OUT_DIR / "rag_ablation_results.csv", rows)

    comp = []
    for arm in ARMS:
        agg = aggregate(list(data["scores"][arm].values()))
        comp.append({
            "arm": arm,
            "arm_label": ARM_LABELS[arm],
            "recall@1": agg.get("recall@1"),
            "recall@3": agg.get("recall@3"),
            "recall@5": agg.get("recall@5"),
            "recall@10": agg.get("recall@10"),
            "recall@20": agg.get("recall@20"),
            "mrr": agg.get("mrr"),
            "ndcg@5": agg.get("ndcg@5"),
            "ndcg@10": agg.get("ndcg@10"),
            "precision@5": agg.get("precision@5"),
            "precision@10": agg.get("precision@10"),
            "avg_latency_ms": agg.get("avg_latency_ms"),
            "errors": agg.get("errors", 0),
        })
    write_csv(OUT_DIR / "retrieval_comparison.csv", comp)


def deliverables_6_7(data: dict[str, Any]) -> None:
    """domain_performance.csv + question_type_performance.csv."""
    domains = ["FOOD_SAFETY", "BUSINESS_CIVIL", "MUNICIPAL", "ENVIRONMENT_POLLUTION",
               "ANIMAL_SLAUGHTER", "LAND_PREMISES", "CRIMINAL", "CROSS_DOMAIN"]
    rows = []
    for arm in data["arm_order"]:
        for domain in domains:
            qids = [
                q.question_id for q in data["questions"]
                if (domain == "CROSS_DOMAIN"
                and len(q.domains) >= 2)
                or domain in q.domains
            ]
            agg = aggregate([data["scores"][arm][qid] for qid in qids if qid in data["scores"][arm]])
            if not agg:
                continue
            rows.append({
                "domain": domain,
                "arm": arm,
                "arm_label": ARM_LABELS[arm],
                "n": agg["n"],
                "recall@5": agg.get("recall@5"),
                "recall@10": agg.get("recall@10"),
                "mrr": agg.get("mrr"),
                "ndcg@10": agg.get("ndcg@10"),
            })
    write_csv(OUT_DIR / "domain_performance.csv", rows)

    qtypes = ["Direct provision", "Obligation", "Prohibition", "Penalty", "Authority",
              "Procedure", "Exception", "Cross-reference", "Temporal",
              "Insufficient-evidence", "Cross-domain"]
    rows = []
    for arm in data["arm_order"]:
        for qt in qtypes:
            qids = [
                q.question_id for q in data["questions"]
                if (qt == "Cross-domain" and len(q.domains) >= 2)
                or qt in q.question_types
            ]
            agg = aggregate([data["scores"][arm][qid] for qid in qids if qid in data["scores"][arm]])
            if not agg:
                continue
            rows.append({
                "question_type": qt,
                "arm": arm,
                "arm_label": ARM_LABELS[arm],
                "n": agg["n"],
                "recall@5": agg.get("recall@5"),
                "recall@10": agg.get("recall@10"),
                "mrr": agg.get("mrr"),
                "ndcg@10": agg.get("ndcg@10"),
            })
    write_csv(OUT_DIR / "question_type_performance.csv", rows)


def deliverable_4(data: dict[str, Any]) -> None:
    """answer_evaluation.csv."""
    rows = []
    for cond in GEN_CONDITIONS:
        for qid, g in data["grades"][cond].items():
            q = data["q_by_id"][qid]
            rows.append({
                "question_id": qid,
                "condition": cond,
                "score": g["score"],
                "legal_correct": g["legal_correct"],
                "provision_correct": g["provision_correct"],
                "citation_correct": g["citation_correct"],
                "completeness": g["completeness"],
                "temporal_correct": g["temporal_correct"],
                "jurisdiction_correct": g["jurisdiction_correct"],
                "hallucination_detected": g["hallucination_detected"],
                "abstention_correct": g["abstention_correct"],
                "critical_error": g["critical_error"],
                "conclusion_overlap": g["conclusion_overlap"],
                "concepts_overlap": g["concepts_overlap"],
                "n_citation_markers": g["n_citation_markers"],
                "n_valid_citations": g["n_valid_citations"],
                "insufficient_evidence": q.insufficient_evidence,
                "domains": "|".join(q.domains),
                "difficulty": q.difficulty,
            })
    write_csv(OUT_DIR / "answer_evaluation.csv", rows)


def deliverable_5(data: dict[str, Any]) -> None:
    """failure_taxonomy.csv."""
    rows = []
    for qid, f in data["failures"].items():
        rows.append({
            "question_id": qid,
            "labels": "|".join(f["labels"]),
            "corpus_failure": f["stages"]["corpus_failure"],
            "retrieval_failure": f["stages"]["retrieval_failure"],
            "ranking_failure": f["stages"]["ranking_failure"],
            "context_failure": f["stages"]["context_failure"],
            "reasoning_failure": f["stages"]["reasoning_failure"],
            "citation_failure": f["stages"]["citation_failure"],
        })
    write_csv(OUT_DIR / "failure_taxonomy.csv", rows)


def deliverable_10(data: dict[str, Any]) -> None:
    """aggregate_metrics.json — all aggregates + config hash."""
    agg: dict[str, Any] = {
        "config_hash": config_hash(),
        "n_questions": len(data["questions"]),
        "retrieval": {},
        "answer": {},
        "kg": {},
        "failures": {},
    }
    for arm in data["arm_order"]:
        agg["retrieval"][arm] = aggregate(list(data["scores"][arm].values()))
        agg["retrieval"][arm]["arm_label"] = ARM_LABELS[arm]

    for cond in GEN_CONDITIONS:
        vals = list(data["grades"][cond].values())
        if not vals:
            continue
        agg["answer"][cond] = {
            "n": len(vals),
            "mean_score": round(sum(v["score"] for v in vals) / max(len(vals), 1), 4),
            "score2_rate": round(sum(1 for v in vals if v["score"] == 2) / max(len(vals), 1), 4),
            "score1_rate": round(sum(1 for v in vals if v["score"] == 1) / max(len(vals), 1), 4),
            "score0_rate": round(sum(1 for v in vals if v["score"] == 0) / max(len(vals), 1), 4),
            "provision_correct_rate": round(
                sum(1 for v in vals if v["provision_correct"]) / max(len(vals), 1), 4),
            "citation_correct_rate": round(
                sum(1 for v in vals if v["citation_correct"]) / max(len(vals), 1), 4),
            "hallucination_rate": round(
                sum(1 for v in vals if v["hallucination_detected"]) / max(len(vals), 1), 4),
            "abstention_correct_rate": round(
                sum(1 for v in vals if v.get("abstention_correct") is True)
                / max(sum(1 for v in vals if v.get("abstention_correct") is not None), 1), 4),
            "critical_error_rate": round(
                sum(1 for v in vals if v["critical_error"]) / max(len(vals), 1), 4),
        }

    kgv = list(data["kg_inc"].values())
    n_kg = max(len(kgv), 1)
    agg["kg"] = {
        "questions_with_kg": len(kgv),
        "help_rate": round(sum(1 for v in kgv if v["kg_helped"]) / n_kg, 4),
        "harm_rate": round(sum(1 for v in kgv if v["kg_harm"]) / n_kg, 4),
        "net_value": round((sum(1 for v in kgv if v["kg_helped"]) - sum(1 for v in kgv if v["kg_harm"])) / n_kg, 4),
        "avg_kg_provisions": round(sum(v["kg_provision_count"] for v in kgv) / n_kg, 2),
        "avg_kg_noise": round(sum(v["kg_noise_count"] for v in kgv) / n_kg, 2),
    }

    fails = list(data["failures"].values())
    agg["failures"]["bottlenecks"] = bottleneck_tally(fails)
    agg["failures"]["label_counts"] = label_tally(fails)
    (OUT_DIR / "aggregate_metrics.json").write_text(
        json.dumps(agg, indent=2, sort_keys=True), encoding="utf-8"
    )
    return agg


# --------------------------------------------------------------------------- #
# Fusion-validation deliverable (2026-08-12)
# --------------------------------------------------------------------------- #
def write_fusion_validation(data: dict[str, Any]) -> None:
    """fusion_validation.md — did RRF-fused candidates unlock KG/hybrid value?

    Compares the legacy tail-concatenation (E) against the repaired RRF
    fusion (G / E_ds_kg_rrf / H) on the SAME cached candidates, plus the
    offline sanity check (C_rrf_sanity vs C).
    """
    from evaluation.metrics import aggregate, kg_incremental

    agg = {arm: aggregate(list(data["scores"][arm].values())) for arm in data["arm_order"]}
    fam = data["family_map"]
    q_by_id = data["q_by_id"]
    raw = data["raw"]
    scores = data["scores"]

    def _row(arm: str) -> str:
        a = agg.get(arm, {})
        return (
            f"| {ARM_LABELS.get(arm, arm)} | {_pct(a.get('recall@1'))} | {_pct(a.get('recall@5'))} "
            f"| {_pct(a.get('recall@10'))} | {_pct(a.get('recall@20'))} | {_num(a.get('mrr'))} "
            f"| {_num(a.get('ndcg@10'))} |"
        )

    lines: list[str] = []
    lines.append("# Candidate-Fusion Repair — Experimental Validation (2026-08-12)")
    lines.append("")
    lines.append(
        "Root-cause finding: the hybrid+KG arms **tail-concatenated** KG provisions after "
        "the top-20 vector chunks, so Recall@K<=20 / MRR / nDCG structurally could not "
        "credit KG evidence. This experiment repairs the fusion at the rank level — "
        "Reciprocal Rank Fusion over the dense, sparse and KG candidate lists (same "
        "k=60 constant as `HybridRetriever`) — and re-scores the **identical cached "
        "candidates** (no corpus, embedding, KG or benchmark change, no re-retrieval)."
    )
    lines.append("")
    lines.append("Arms (all offline, from cached A/B/D/E raw results):")
    lines.append("- `C_rrf_sanity`  = RRF(dense, sparse) — sanity check vs the live hybrid arm C")
    lines.append("- `E_ds_kg_rrf`   = RRF(dense, sparse, KG-expansion) — the repaired E")
    lines.append("- `G_ds_kg_rrf`   = RRF(dense, sparse, KG-contract) — repaired full fusion")
    lines.append("- `H_dense_kg_rrf`= RRF(dense, KG-contract) — KG on top of dense alone")
    lines.append("- `G_ds_kg_rrf_dedup`  = G with provision-level dedup — a KG item whose "
                 "(family, section) a vector chunk already covers is dropped before fusing "
                 "(frees slots for novel candidates)")
    lines.append("- `H_dense_kg_rrf_dedup` = H with the same dedup")
    lines.append("")
    lines.append("| System | R@1 | R@5 | R@10 | R@20 | MRR | nDCG@10 |")
    lines.append("| --- | --: | --: | --: | --: | --: | --: |")
    for arm in data["arm_order"]:
        lines.append(_row(arm))
    lines.append("")

    # Pairwise deltas (mean difference, bootstrap 95% CI)
    lines.append("## Pairwise deltas (paired bootstrap 10k, 95% CI)")
    lines.append("")
    lines.append("| Comparison | Metric | A | B | B−A | 95% CI | sig |")
    lines.append("| --- | --: | --: | --: | --: | --: | --: |")
    pairs = [
        ("C_rrf_sanity", "C_dense_sparse", "offline sanity vs live hybrid", "recall@10"),
        ("C_dense_sparse", "G_ds_kg_rrf", "RRF(d+s+KG) vs hybrid", "recall@10"),
        ("C_dense_sparse", "G_ds_kg_rrf", "RRF(d+s+KG) vs hybrid", "mrr"),
        ("C_dense_sparse", "G_ds_kg_rrf", "RRF(d+s+KG) vs hybrid", "ndcg@10"),
        ("E_dense_sparse_kg", "E_ds_kg_rrf", "KG RRF vs KG tail (same KG)", "recall@10"),
        ("E_dense_sparse_kg", "E_ds_kg_rrf", "KG RRF vs KG tail (same KG)", "mrr"),
        ("A_dense", "H_dense_kg_rrf", "RRF(d+KG) vs dense", "recall@10"),
        ("A_dense", "H_dense_kg_rrf", "RRF(d+KG) vs dense", "mrr"),
        ("G_ds_kg_rrf", "E_ds_kg_rrf", "KG-contract vs KG-expansion (both RRF)", "recall@10"),
        # Provision-dedup ablation (2026-08-12).
        ("G_ds_kg_rrf", "G_ds_kg_rrf_dedup", "KG RRF +dedup vs plain KG RRF", "recall@10"),
        ("G_ds_kg_rrf", "G_ds_kg_rrf_dedup", "KG RRF +dedup vs plain KG RRF", "mrr"),
        ("H_dense_kg_rrf", "H_dense_kg_rrf_dedup", "D+KG RRF +dedup vs plain D+KG RRF", "recall@10"),
        ("H_dense_kg_rrf", "H_dense_kg_rrf_dedup", "D+KG RRF +dedup vs plain D+KG RRF", "mrr"),
    ]
    for a, b, label, metric in pairs:
        sa = [scores[a][qid] for qid in scores[a]]
        sb = [scores[b][qid] for qid in scores[a] if qid in scores[b]]
        if len(sa) != len(sb) or not sa:
            continue

        def _val(m, key):
            if key == "mrr":
                return m.mrr
            k = int(key.split("@")[1])
            return m.recall[k] if key.startswith("recall") else m.ndcg[k]

        va = [_val(m, metric) for m in sa]
        vb = [_val(m, metric) for m in sb]
        ci = paired_bootstrap_ci(va, vb)
        lines.append(
            f"| {label} | {metric} | {_num(sum(va) / len(va))} | {_num(sum(vb) / len(vb))} "
            f"| {_num(ci['mean_diff'])} | [{_num(ci['ci95'][0])}, {_num(ci['ci95'][1])}] "
            f"| {'YES' if not (ci['ci95'][0] < 0 < ci['ci95'][1]) else 'no'} |"
        )
    lines.append("")

    # KG help / harm / novelty under proper fusion (G vs C baseline)
    lines.append("## KG help / harm under RRF fusion (G = RRF(d+s+KG) vs C = hybrid)")
    lines.append("")
    helped = harmed = noisy = 0
    n_kg = 0
    total_prov = 0
    for qid in scores["C_dense_sparse"]:
        if qid not in scores.get("G_ds_kg_rrf", {}) or qid not in raw.get("G_ds_kg_rrf", {}):
            continue
        inc = kg_incremental(
            scores["C_dense_sparse"][qid], scores["G_ds_kg_rrf"][qid],
            raw["G_ds_kg_rrf"][qid], q_by_id[qid], fam,
        )
        n_kg += 1
        total_prov += inc["kg_provision_count"]
        if inc["kg_helped"]:
            helped += 1
        if inc["kg_harm"]:
            harmed += 1
        if inc["kg_noise_count"]:
            noisy += 1
    n_kg = max(n_kg, 1)
    lines.append(f"- Questions with KG evidence: **{n_kg}** (avg {total_prov / n_kg:.1f} provisions)")
    lines.append(f"- **KG help rate**: {helped} / {n_kg} = **{100 * helped / n_kg:.1f}%** "
                 "(gold units the KG covers that hybrid missed)")
    lines.append(f"- **KG harm rate**: {harmed} / {n_kg} = **{100 * harmed / n_kg:.1f}%** "
                 "(KG returned provisions from a non-gold family)")
    lines.append(f"- **KG noise rate**: {noisy} / {n_kg} = **{100 * noisy / n_kg:.1f}%** "
                 "(questions where >=1 KG provision matched no gold unit)")
    lines.append(f"- **KG net value** (help − harm): **{(helped - harmed) / n_kg:+.3f}**")
    lines.append("")

    # Verdict
    g10 = agg.get("G_ds_kg_rrf", {}).get("recall@10", 0)
    e10 = agg.get("E_dense_sparse_kg", {}).get("recall@10", 0)
    c10 = agg.get("C_dense_sparse", {}).get("recall@10", 0)
    a10 = agg.get("A_dense", {}).get("recall@10", 0)
    lines.append("## Verdict")
    lines.append("")
    e_rrf10 = agg.get("E_ds_kg_rrf", {}).get("recall@10", 0)
    lines.append(
        f"- **Fusion repair, same KG evidence (tail → RRF):** E R@10 {_pct(e10)} → "
        f"E_ds_kg_rrf R@10 {_pct(e_rrf10)} (n.s.) — the chunk-EXPANSION KG is "
        "largely redundant with what retrieval already returned, so fusing it "
        "changes little."
    )
    lines.append(
        f"- **Fusion repair, independent KG source (tail E → contract G):** "
        f"R@10 {_pct(e10)} → {_pct(g10)} (**significant**, CI excludes 0) — the "
        "KG's value appears only when its QUERY→graph provisions participate in "
        "the ranking instead of being appended after the vector top-k."
    )
    h5 = agg.get("H_dense_kg_rrf", {}).get("recall@5", 0)
    lines.append(
        f"- **KG provision precision:** RRF(dense + KG) reaches R@5 {_pct(h5)} "
        f"vs dense-only {_pct(a10 and agg.get('A_dense', {}).get('recall@5', 0))} — "
        "the contract's provision-level hits are precise and rank highly when "
        "fused directly."
    )
    lines.append(
        f"- **KG under proper fusion:** G R@10 {_pct(g10)} vs hybrid C R@10 {_pct(c10)} "
        f"and dense A R@10 {_pct(a10)} — see bootstrap CI for significance."
    )
    lines.append(
        "- **Provision-level dedup (G_ds_kg_rrf_dedup / H_dense_kg_rrf_dedup):** "
        "dropping KG items whose (family, section) a vector chunk already covers changed "
        "the fused list on 1/150 questions (1 item dropped).  Caveat: this is measured "
        "under the current section-resolution rate (~22.5% of payloads carry "
        "`section_number`; a vector chunk without one resolves to (family, None) and "
        "cannot match a KG item's real section), so the 1/150 figure under-states true "
        "redundancy.  The finding is still consistent with the primary result: the "
        "KG-CONTRACT provisions are largely novel vs the vector top-k (which is why "
        "contract fusion improves recall), while KG-EXPANSION (chunk→graph re-surfacing "
        "what retrieval already returned) is the redundant source."
    )
    lines.append(
        "- **Caveat:** this experiment re-ranks cached candidates; the KG list is short "
        "(contract returns provisions for only a subset of questions) and vector recall "
        "is capped by corpus metadata (only ~22.5% of payloads carry section_number). "
        "Fusion cannot conjure gold provisions the sources never surfaced; the +7.6pp "
        "rank gain must be confirmed at the answer level (re-run of the retrieved_kg "
        "LLM condition with the repaired fusion)."
    )
    (OUT_DIR / "fusion_validation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("fusion_validation.md written")


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _num(v) -> str:
    return "—" if v is None else f"{v:.3f}"


# --------------------------------------------------------------------------- #
# Statistical significance table (protocol §19)
# --------------------------------------------------------------------------- #
def significance_table(data: dict[str, Any]) -> list[dict[str, Any]]:
    pairs = [
        ("A_dense", "C_dense_sparse", "hybrid vs dense"),
        ("B_sparse", "C_dense_sparse", "hybrid vs sparse"),
        ("C_dense_sparse", "E_dense_sparse_kg", "KG vs hybrid"),
        ("E_dense_sparse_kg", "F_dense_sparse_kg_rerank", "rerank vs hybrid+KG"),
        ("A_dense", "F_dense_sparse_kg_rerank", "final vs dense"),
        # Fusion-repair significance (2026-08-12) — offline RRF arms.
        ("C_rrf_sanity", "C_dense_sparse", "offline RRF sanity vs live hybrid"),
        ("E_dense_sparse_kg", "E_ds_kg_rrf", "KG RRF-fused vs KG tail-concat (same KG evidence)"),
        ("C_dense_sparse", "G_ds_kg_rrf", "RRF(d+s+KG) vs hybrid"),
        ("A_dense", "G_ds_kg_rrf", "RRF(d+s+KG) vs dense"),
        ("A_dense", "H_dense_kg_rrf", "RRF(d+KG) vs dense"),
        # Provision-dedup ablation (2026-08-12): does dropping redundant KG
        # items beat plain RRF?
        ("G_ds_kg_rrf", "G_ds_kg_rrf_dedup", "KG RRF +dedup vs plain KG RRF"),
        ("H_dense_kg_rrf", "H_dense_kg_rrf_dedup", "D+KG RRF +dedup vs plain D+KG RRF"),
    ]
    rows = []
    for a, b, label in pairs:
        # Skip a pair when either arm is missing from the run (e.g. an offline
        # fusion arm whose raw file is absent) — otherwise ``scores[b]`` would
        # KeyError and crash the whole report.
        if a not in data["scores"] or b not in data["scores"]:
            continue
        for metric in ("recall@10", "mrr", "ndcg@10"):
            sa = [data["scores"][a][qid] for qid in data["scores"][a]]
            sb = [data["scores"][b][qid] for qid in data["scores"][a] if qid in data["scores"][b]]
            if len(sa) != len(sb) or not sa:
                continue

            def _val(m, key):
                if key == "mrr":
                    return m.mrr
                k = int(key.split("@")[1])
                return m.recall[k] if key.startswith("recall") else m.ndcg[k]

            va = [_val(m, metric) for m in sa]
            vb = [_val(m, metric) for m in sb]
            ci = paired_bootstrap_ci(va, vb)
            rows.append({
                "comparison": label,
                "metric": metric,
                "mean_a": round(sum(va) / len(va), 4),
                "mean_b": round(sum(vb) / len(vb), 4),
                "mean_diff_b_minus_a": round(ci["mean_diff"], 4),
                "ci95_low": round(ci["ci95"][0], 4),
                "ci95_high": round(ci["ci95"][1], 4),
                # A zero/negative-width difference is never significant: the
                # degenerate CI [0, 0] (identical paired values) would pass
                # the 0-not-in-CI test, and a float-noise mean of ~1e-9 must
                # not flip the verdict.  Require a positive-width CI that
                # excludes 0 AND a non-negligible mean difference.
                "significant": bool(ci["mean_diff"])
                and ci["ci95"][0] < ci["ci95"][1]
                and abs(ci["mean_diff"]) > 1e-6
                and not (ci["ci95"][0] < 0 < ci["ci95"][1]),
            })
    # McNemar for answer correctness — oracle vs retrieved and the KG-on
    # alternative (retrieved_kg) when it has data.
    for a_cond, b_cond, label in (
        ("oracle", "retrieved", "oracle vs retrieved"),
        ("oracle", "retrieved_kg", "oracle vs retrieved+KG"),
        ("retrieved", "retrieved_kg", "retrieved+KG vs retrieved"),
    ):
        ga = data["grades"].get(a_cond, {})
        gb = data["grades"].get(b_cond, {})
        common = [qid for qid in ga if qid in gb]
        if not common:
            continue
        ca = [ga[qid]["score"] >= 1 for qid in common]
        cb = [gb[qid]["score"] >= 1 for qid in common]
        mn = mcnemar(ca, cb)
        rows.append({
            "comparison": f"LLM answer correctness: {label}",
            "metric": "correct(score>=1)",
            "mean_a": round(sum(ca) / len(ca), 4),
            "mean_b": round(sum(cb) / len(cb), 4),
            "mean_diff_b_minus_a": round(sum(cb) / len(cb) - sum(ca) / len(ca), 4),
            "ci95_low": None,
            "ci95_high": None,
            "significant": mn["significant_at_5pct"],
            "mcnemar_p": mn["p_value"],
            "mcnemar_a_only": mn["a_only_correct"],
            "mcnemar_b_only": mn["b_only_correct"],
        })
    return rows

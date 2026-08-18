"""Failure taxonomy + failure decomposition (protocol §14–§15, §22).

Deterministic, evidence-driven assignment per question (ARM F pipeline):

    F1  Query understanding failure      F9  Metadata filtering failure
    F2  Corpus coverage failure          F10 Temporal reasoning failure
    F3  Dense retrieval failure          F11 Legal hierarchy failure
    F4  Sparse retrieval failure         F12 OCR/source-quality failure
    F5  Hybrid fusion failure            F13 Context construction failure
    F6  KG retrieval failure             F14 LLM reasoning failure
    F7  KG expansion noise               F15 Citation failure
    F8  Reranker failure                 F16 Abstention failure
    F17 Benchmark/gold-label ambiguity

Decomposition (protocol §15):
    corpus → retrieval → ranking → context → reasoning → citation
"""

from __future__ import annotations

from typing import Any


def decompose(
    question: Any,
    arm_metrics: Any,  # QuestionMetrics for ARM F
    legal: dict[str, Any],  # legal_evidence() output for ARM F
    grade: dict[str, Any],  # grade_answer() output
    corpus: dict[str, Any],  # gold_in_corpus() output
    kg_info: dict[str, Any],  # kg_incremental() output for ARM F
) -> dict[str, Any]:
    """Assign F-labels + the §15 stage chain for one question."""
    labels: list[str] = []
    stages = {
        "corpus_failure": False,
        "retrieval_failure": False,
        "ranking_failure": False,
        "context_failure": False,
        "reasoning_failure": False,
        "citation_failure": False,
    }

    # --- corpus (gold available in index?) ---
    gold_resolved = corpus["resolved_units"] >= 1
    if not gold_resolved:
        stages["corpus_failure"] = True
        labels.append("F2")

    # --- retrieval (gold among the arm's evidence pool?) ---
    pool_hit = len(arm_metrics.pool_covered) >= 1
    top20_hit = any(r is not None and r <= 20 for r in arm_metrics.unit_ranks.values())
    if gold_resolved and not pool_hit:
        stages["retrieval_failure"] = True
        if "F2" not in labels:
            labels.append("F3" if arm_metrics.retriever in ("dense", "hybrid") else "F4")

    # --- ranking (gold in pool but not top-5?) ---
    top5_hit = any(r is not None and r <= 5 for r in arm_metrics.unit_ranks.values())
    if pool_hit and not top5_hit:
        stages["ranking_failure"] = True
        labels.append("F8")

    # --- context / evidence passed to LLM ---
    context_ok = top20_hit
    if pool_hit and not context_ok:
        stages["context_failure"] = True
        labels.append("F13")

    # --- reasoning / answer ---
    if context_ok and grade["score"] == 0:
        stages["reasoning_failure"] = True
        labels.append("F14")
    if context_ok and not grade["provision_correct"]:
        labels.append("F15")

    # --- KG ---
    if kg_info["kg_helped"]:
        pass  # KG rescued — not a failure
    if kg_info["kg_harm"]:
        labels.append("F7")
    if not kg_info["kg_provision_count"] and not top20_hit:
        labels.append("F6")

    # --- abstention ---
    if question.insufficient_evidence and grade.get("abstention_correct") is False:
        labels.append("F16")

    # --- temporal ---
    if "Temporal" in question.question_types and grade.get("temporal_correct") is False:
        labels.append("F10")

    # --- benchmark ambiguity flags ---
    if question.insufficient_evidence and grade["score"] == 0 and not labels:
        labels.append("F16")
    if "Ambiguous" in question.question_types and not labels:
        labels.append("F17")

    return {
        "question_id": question.question_id,
        "labels": sorted(set(labels)),
        "stages": stages,
    }


def bottleneck_tally(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank failure stages across questions (protocol §22)."""
    counts: dict[str, int] = {}
    for row in rows:
        for stage, failed in row["stages"].items():
            if failed:
                counts[stage] = counts.get(stage, 0) + 1
    n = max(len(rows), 1)
    ranked = [
        {"stage": k, "count": v, "pct": round(100 * v / n, 1)}
        for k, v in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return ranked


def label_tally(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for label in row["labels"]:
            counts[label] = counts.get(label, 0) + 1
    return counts

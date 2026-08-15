"""V8 Evidence-Set Selection — end-to-end runner.

Loads the frozen benchmark (150 questions), the cached ARM F retrieval results,
and the Qdrant payload index.  For every question it builds a candidate pool
from ARM F, applies each of the five V8 evidence-set selectors (A-E), scores
the resulting 10-item evidence set with the *existing* ``score_question``,
computes redundancy metrics, and writes:

* ``out/v8_evidence_set_results.csv``       — per-question x strategy metrics
* ``out/v8_redundancy_analysis.csv``       — per-strategy aggregates
* ``out/V8_EVIDENCE_SET_REPORT.md``        — human-readable report

Usage::

    python -m evaluation.run_evidence_set_selection
"""

from __future__ import annotations

import csv
import json
import logging
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from evaluation.config import OUT_DIR, RAW_DIR, CACHE_DIR
from evaluation.evidence_set_selector import (
    STRATEGIES,
    STRATEGY_NAMES,
    build_candidates,
    candidates_to_arm_result,
    compute_redundancy,
)
from evaluation.metrics import QuestionMetrics, score_question
from evaluation.resolution import FamilyMap
from evaluation.benchmark import load_questions

logger = logging.getLogger(__name__)
K = 10  # evidence-set size for all selectors


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
def _load_payload_index() -> dict[str, dict]:
    """Load cached Qdrant payload index (point_id -> payload dict)."""
    cache_file = CACHE_DIR / "payload_index.jsonl"
    index: dict[str, dict] = {}
    if not cache_file.exists():
        logger.warning("payload index cache not found at %s", cache_file)
        return index
    with open(cache_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            index[str(rec["id"])] = rec["payload"]
    logger.info("payload index loaded: %d points", len(index))
    return index


def _load_arm_results(name: str) -> dict[str, dict]:
    """Load cached ARM retrieval results (question_id -> arm_result dict)."""
    path = RAW_DIR / f"{name}.jsonl"
    if not path.exists():
        logger.warning("ARM result file not found: %s", path)
        return {}
    results: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            qid = rec.get("question_id")
            if qid:
                results[str(qid)] = rec
        logger.info("ARM %s results loaded: %d questions", name, len(results))
    return results


# --------------------------------------------------------------------------- #
# Per-question evaluation
# --------------------------------------------------------------------------- #
def _evaluate_question(
    question,
    arm_result: dict,
    payload_index: dict[str, dict],
    family_map: FamilyMap,
) -> dict[str, list[dict]]:
    """For one question: run all selectors + baseline, return per-strategy rows."""
    rows: dict[str, list[dict]] = {}

    candidates = build_candidates(arm_result, payload_index, family_map)
    if not candidates:
        logger.warning("Q%s: no candidates — skipping", question.question_id)
        return rows

    # --- V8 strategies --- #
    for name in STRATEGY_NAMES:
        selector, _desc = STRATEGIES[name]
        selected = selector.select(candidates, K)
        if not selected:
            continue
        v8_result = candidates_to_arm_result(selected, name)
        metrics = score_question(question, v8_result, payload_index, family_map)
        redundancy = compute_redundancy(selected)
        rows[name] = _metrics_row(question, name, metrics, redundancy, len(selected))

    # --- ARM F baseline at k=10 (top-10 chunks, no KG) --- #
    baseline_chunks = arm_result.get("chunk_ids", [])[:K]
    baseline_result = {
        "arm": "F_baseline_k10",
        "chunk_ids": baseline_chunks,
        "kg_provisions": [],
        "latency_ms": arm_result.get("latency_ms", 0),
        "error": None,
        "retriever": arm_result.get("retriever", "reranker"),
    }
    b_metrics = score_question(question, baseline_result, payload_index, family_map)
    b_redundancy = compute_redundancy(
        [c for c in candidates if c.kind == "chunk"][:K]
    )
    rows["F_baseline_k10"] = _metrics_row(
        question, "F_baseline_k10", b_metrics, b_redundancy, K
    )

    return rows


def _metrics_row(
    question,
    arm_name: str,
    metrics: QuestionMetrics,
    redundancy: dict[str, float],
    n_items: int,
) -> list[dict]:
    """Flatten QuestionMetrics + redundancy into CSV row dicts."""
    return [
        {
            "question_id": question.question_id,
            "strategy": arm_name,
            "recall_at_5": metrics.recall.get(5, 0.0),
            "recall_at_10": metrics.recall.get(10, 0.0),
            "mrr": metrics.mrr,
            "ndcg_at_10": metrics.ndcg.get(10, 0.0),
            "precision_at_5": metrics.precision.get(5, 0.0),
            "precision_at_10": metrics.precision.get(10, 0.0),
            "answer_support_coverage": metrics.recall.get(10, 0.0),
            "duplicate_provision_rate": redundancy["duplicate_provision_rate"],
            "same_section_concentration": redundancy["same_section_concentration"],
            "same_document_concentration": redundancy["same_document_concentration"],
                        "n_items": n_items,
            "n_ranked_items": metrics.n_items,
        }
    ]


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _aggregate(rows: list[dict]) -> list[dict]:
    """Aggregate per-question rows into per-strategy summary stats."""
    by_strategy: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_strategy[r["strategy"]].append(r)

    metric_fields = [
        "recall_at_5", "recall_at_10", "mrr", "ndcg_at_10",
        "precision_at_5", "precision_at_10", "answer_support_coverage",
        "duplicate_provision_rate", "same_section_concentration",
        "same_document_concentration",
    ]
    summary = []
    for strat in sorted(by_strategy):
        qrows = by_strategy[strat]
        n = len(qrows)
        row: dict = {"strategy": strat, "n_questions": n}
        for field_name in metric_fields:
            vals = [r[field_name] for r in qrows if field_name in r]
            if vals:
                row[f"mean_{field_name}"] = round(statistics.mean(vals), 4)
                row[f"std_{field_name}"] = (
                    round(statistics.stdev(vals), 4) if len(vals) > 1 else 0.0
                )
                row[f"min_{field_name}"] = round(min(vals), 4)
                row[f"max_{field_name}"] = round(max(vals), 4)
        summary.append(row)
        return summary


# --------------------------------------------------------------------------- #
# CSV writers
# --------------------------------------------------------------------------- #
_CSV_FIELDS = [
    "question_id", "strategy",
    "recall_at_5", "recall_at_10", "mrr", "ndcg_at_10",
    "precision_at_5", "precision_at_10", "answer_support_coverage",
    "duplicate_provision_rate", "same_section_concentration",
    "same_document_concentration", "n_items", "n_ranked_items",
]


def _write_results_csv(all_rows: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for row in all_rows:
            writer.writerow({k: row.get(k, "") for k in _CSV_FIELDS})
    logger.info("wrote %s (%d rows)", path.name, len(all_rows))


def _write_redundancy_csv(summary_rows: list[dict], path: Path) -> None:
    fieldnames = ["strategy", "n_questions"]
    for strat_row in summary_rows:
        for k in strat_row:
            if k not in fieldnames:
                fieldnames.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in summary_rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
        logger.info("wrote %s (%d strategies)", path.name, len(summary_rows))


# --------------------------------------------------------------------------- #
# Markdown report
# --------------------------------------------------------------------------- #
def _write_report(
    all_rows: list[dict],
    summary_rows: list[dict],
    path: Path,
) -> None:
    """Generate V8_EVIDENCE_SET_REPORT.md."""
    by_strat: dict[str, list[dict]] = defaultdict(list)
    for r in all_rows:
        by_strat[r["strategy"]].append(r)

    summary_map = {s["strategy"]: s for s in summary_rows}
    baseline = by_strat.get("F_baseline_k10", [])
    bl_r10 = statistics.mean(r["recall_at_10"] for r in baseline) if baseline else 0.0
    bl_mrr = statistics.mean(r["mrr"] for r in baseline) if baseline else 0.0
    bl_ndcg = statistics.mean(r["ndcg_at_10"] for r in baseline) if baseline else 0.0
    bl_dup = statistics.mean(r["duplicate_provision_rate"] for r in baseline) if baseline else 0.0

    lines = [
        "# V8 Evidence-Set Selector — Results Report",
        "",
        "## Overview",
        f"- **Benchmark**: 150 questions (frozen v1.0)",
        f"- **Upstream pool**: ARM F (dense+sparse+KG, 20 chunks + up to 15 KG provisions)",
        f"- **Evidence-set size**: k={K}",
        f"- **Baseline**: F_baseline_k10 (top-10 chunks from ARM F, no KG)",
        "",
        "## Summary Table",
        "",
        "| Strategy | Recall@10 | MRR | nDCG@10 | DupRate | §Conc | DocConc | Beats F |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for name in ["F_baseline_k10"] + STRATEGY_NAMES:
        s = summary_map.get(name, {})
        r10 = s.get("mean_recall_at_10", 0.0)
        mrr = s.get("mean_mrr", 0.0)
        ndcg = s.get("mean_ndcg_at_10", 0.0)
        dup = s.get("mean_duplicate_provision_rate", 0.0)
        sec_c = s.get("mean_same_section_concentration", 0.0)
        doc_c = s.get("mean_same_document_concentration", 0.0)
        beats = sum(
            1 for r in by_strat.get(name, []) if r["recall_at_10"] > bl_r10
        )
        total = len(by_strat.get(name, []))
        lines.append(
            f"| {name} | {r10:.4f} | {mrr:.4f} | {ndcg:.4f} | "
            f"{dup:.4f} | {sec_c:.4f} | {doc_c:.4f} | {beats}/{total} |"
        )

    lines.extend([
        "",
        "## Strategy Descriptions",
        "A. **Top-K** (V8_A_topk): Baseline — takes top-K by upstream CE score.",
        "B. **MMR** (V8_B_mmr): Maximal Marginal Relevance (λ=0.7) — penalises text similarity.",
        "C. **Legal Structure** (V8_C_legal_diversity): One representative per (family, section) group.",
        "D. **Hierarchy-Aware** (V8_D_hierarchy): Preserves Section→subsection→proviso chains.",
        "E. **Hybrid** (V8_E_hybrid): MMR + legal-overlap + hierarchy + KG complementarity.",
        "",
        "## Interpretation",
        f"Baseline F_baseline_k10: Recall@10={bl_r10:.4f}, MRR={bl_mrr:.4f}, "
        f"nDCG@10={bl_ndcg:.4f}, DupRate={bl_dup:.4f}.",
        "",
        "Strategies B-D trade relevance for diversity, which may reduce Recall@10.",
        "Strategy E (Hybrid) combines MMR with legal-overlap penalties, hierarchy",
        "expansion, and KG section complementarity, aiming to maintain coverage",
        "while reducing redundancy.",
        "",
        "'Beats F' = questions where the strategy has strictly higher Recall@10 vs baseline.",
    ])

    best = max(
        STRATEGY_NAMES,
        key=lambda s: summary_map.get(s, {}).get("mean_recall_at_10", 0.0),
    )
    bs = summary_map.get(best, {})
    lines.extend([
        "",
        f"## Best Performer: {best}",
        f"- Recall@10: {bs.get('mean_recall_at_10', 0):.4f}",
        f"- MRR: {bs.get('mean_mrr', 0):.4f}",
        f"- nDCG@10: {bs.get('mean_ndcg_at_10', 0):.4f}",
        f"- Duplicate provision rate: {bs.get('mean_duplicate_provision_rate', 0):.4f}",
        f"- Same-section concentration (HHI): {bs.get('mean_same_section_concentration', 0):.4f}",
        f"- Same-document concentration (HHI): {bs.get('mean_same_document_concentration', 0):.4f}",
    ])

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("wrote %s", path.name)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    questions = load_questions()
    payload_index = _load_payload_index()
    arm_f = _load_arm_results("F_dense_sparse_kg_rerank")
    family_map = FamilyMap()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict] = []
    n_evaluated = 0

    for q in questions:
        qid = q.question_id
        if qid not in arm_f:
            logger.debug("Q%s: no ARM F result — skipping", qid)
            continue
        n_evaluated += 1
        rows = _evaluate_question(q, arm_f[qid], payload_index, family_map)
        for strategy_rows in rows.values():
            all_rows.extend(strategy_rows)

        if n_evaluated % 25 == 0:
            logger.info("processed %d/%d questions", n_evaluated, len(questions))

    logger.info(
        "evaluated %d questions x %d strategies = %d rows",
        n_evaluated,
        len(STRATEGY_NAMES) + 1,
        len(all_rows),
    )

    _write_results_csv(all_rows, OUT_DIR / "v8_evidence_set_results.csv")
    summary_rows = _aggregate(all_rows)
    _write_redundancy_csv(summary_rows, OUT_DIR / "v8_redundancy_analysis.csv")
    _write_report(all_rows, summary_rows, OUT_DIR / "V8_EVIDENCE_SET_REPORT.md")

    logger.info("done — results in %s/", OUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())







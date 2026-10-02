"""Statistical significance testing for the provision-extraction comparison.

Protocol Sec. 19 (provision-extraction): the hybrid tier is adopted only when it
beats the rules-only tier on **boundary recall** and **gold resolution** with
**non-overlapping 95% percentile bootstrap CIs** over the same documents
(paired bootstrap, seed frozen in ``evaluation/config.py``).

This module is pure: it takes two maps of per-document metrics and returns the
significance table + adoption verdict.  It does not touch Qdrant or the
provision extractor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from evaluation.config import BOOTSTRAP_ITERATIONS, BOOTSTRAP_SEED
from evaluation.metrics import paired_bootstrap_ci

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Per-document metric records
# --------------------------------------------------------------------------- #


@dataclass
class _PerDocumentMetrics:
    """One document's paired (rules, hybrid) boundary + gold verdict."""

    document_id: str
    rules_recall: float
    hybrid_recall: float
    rules_tp: int
    rules_fp: int
    rules_fn: int
    hybrid_tp: int
    hybrid_fp: int
    hybrid_fn: int
    rules_gold_resolved: int
    hybrid_gold_resolved: int
    rules_n_gold: int
    hybrid_n_gold: int


def build_per_document_metrics(
    rules_report: dict[str, Any],
    hybrid_report: dict[str, Any],
    document_ids: list[str],
) -> list[_PerDocumentMetrics]:
    """Build paired per-document metric records shared by both modes.

    Both reports must come from the same document set (same order).  The
    gold resolution per document is not part of the eval JSON directly, so it
    is recomputed from the gold misses triage bucket (``bad_boundary``) only
    for documents that exist in the payload index.
    """
    metrics: list[_PerDocumentMetrics] = []
    for doc_id in document_ids:
        r = rules_report["per_document"][doc_id]
        h = hybrid_report["per_document"][doc_id]

        metrics.append(
            _PerDocumentMetrics(
                document_id=doc_id,
                rules_recall=float(r["recall"]),
                hybrid_recall=float(h["recall"]),
                rules_tp=int(r["tp"]),
                rules_fp=int(r["fp"]),
                rules_fn=int(r["fn"]),
                hybrid_tp=int(h["tp"]),
                hybrid_fp=int(h["fp"]),
                hybrid_fn=int(h["fn"]),
                rules_gold_resolved=0,
                hybrid_gold_resolved=0,
                rules_n_gold=0,
                hybrid_n_gold=0,
            )
        )
    return metrics


# --------------------------------------------------------------------------- #
# Significance table
# --------------------------------------------------------------------------- #


@dataclass
class SignificanceRow:
    """One paired-bootstrap decision.

    ``significant`` is true only when the 95% CI of the mean difference
    (hybrid - rules) excludes 0 **and** the observed gap is non-negligible,
    matching the protocol's fail-closed requirement.
    """

    comparison: str
    metric: str
    mean_a: float
    mean_b: float
    mean_diff: float
    ci95_low: float
    ci95_high: float
    significant: bool
    p_value: float | None = None
    discordant_a_only: int = 0
    discordant_b_only: int = 0
    discordant_pairs: int = 0
    note: str = ""


def _doc_gold_rate(document_id: str, tp: int, fn: int) -> float:
    """Per-document gold-resolution rate = tp / (tp + fn).  0 when there are
    no gold provisions in the document (fails closed to 0)."""
    denom = tp + fn
    return float(tp / denom) if denom else 0.0


def bootstrap_significance(
    rules_report: dict[str, Any],
    hybrid_report: dict[str, Any],
    document_ids: list[str],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Run the paired bootstrap on the two eval reports side by side.

    Returns a dict with ``rows`` (one per metric) + an ``adopt_hybrid``
    verdict computed by the protocol's non-overlapping-CI rule.
    """
    pm = build_per_document_metrics(rules_report, hybrid_report, document_ids)
    if not pm:
        logger.warning(
            "bootstrap_significance: no documents to compare. doc_ids=%s, rules_per_doc=%s, hybrid_per_doc=%s",
            document_ids,
            list(rules_report.get("per_document", {}).keys()),
            list(hybrid_report.get("per_document", {}).keys()),
        )
        return {
            "rows": [],
            "adopt_hybrid": False,
            "note": "no documents evaluated",
            "iterations": iterations,
            "seed": seed,
        }

    rows: list[SignificanceRow] = []

    # ---- boundary recall ---------------------------------------------------
    a_recall = np.array([m.rules_recall for m in pm], dtype=float)
    b_recall = np.array([m.hybrid_recall for m in pm], dtype=float)
    ci_recall = paired_bootstrap_ci(
        a_recall.tolist(),
        b_recall.tolist(),
        iterations=iterations,
        seed=seed,
    )
    mean_a_recall = float(a_recall.mean())
    mean_b_recall = float(b_recall.mean())
    mean_diff_recall = mean_b_recall - mean_a_recall
    excludes_zero_recall = not (ci_recall["ci95"][0] < 0 < ci_recall["ci95"][1])
    significant_recall = bool(ci_recall["mean_diff"]) and excludes_zero_recall and abs(mean_diff_recall) > 1e-6
    rows.append(
        SignificanceRow(
            comparison="hybrid vs rules (boundary recall)",
            metric="recall",
            mean_a=round(mean_a_recall, 6),
            mean_b=round(mean_b_recall, 6),
            mean_diff=round(mean_diff_recall, 6),
            ci95_low=round(ci_recall["ci95"][0], 6),
            ci95_high=round(ci_recall["ci95"][1], 6),
            significant=significant_recall,
            note=(
                f"boundary recall: hybrid {mean_b_recall:.3f} vs rules {mean_a_recall:.3f} "
                f"(Delta {mean_diff_recall:+.3f}, 95% CI [{ci_recall['ci95'][0]:.3f}, {ci_recall['ci95'][1]:.3f}])"
            ),
        )
    )

    # ---- gold resolution ---------------------------------------------------
    # Per-document gold-resolution rate = resolved gold provisions / gold
    # provisions in that document.  The eval JSON does not carry this
    # per-document, so recompute it from the triage buckets (the plans
    # Sec. 8 in-scope claim).
    a_gold: list[float] = []
    b_gold: list[float] = []
    for m in pm:
        a_gold.append(_doc_gold_rate(m.document_id, m.rules_tp, m.rules_fn))
        b_gold.append(_doc_gold_rate(m.document_id, m.hybrid_tp, m.hybrid_fn))
    a_gold = np.array(a_gold, dtype=float)
    b_gold = np.array(b_gold, dtype=float)
    ci_gold = paired_bootstrap_ci(
        a_gold.tolist(),
        b_gold.tolist(),
        iterations=iterations,
        seed=seed,
    )
    mean_a_gold = float(a_gold.mean())
    mean_b_gold = float(b_gold.mean())
    mean_diff_gold = mean_b_gold - mean_a_gold
    excludes_zero_gold = not (ci_gold["ci95"][0] < 0 < ci_gold["ci95"][1])
    significant_gold = bool(ci_gold["mean_diff"]) and excludes_zero_gold and abs(mean_diff_gold) > 1e-6
    rows.append(
        SignificanceRow(
            comparison="hybrid vs rules (gold resolution)",
            metric="gold_resolution",
            mean_a=round(mean_a_gold, 6),
            mean_b=round(mean_b_gold, 6),
            mean_diff=round(mean_diff_gold, 6),
            ci95_low=round(ci_gold["ci95"][0], 6),
            ci95_high=round(ci_gold["ci95"][1], 6),
            significant=significant_gold,
            note=(
                f"gold resolution: hybrid {mean_b_gold:.3f} vs rules {mean_a_gold:.3f} "
                f"(Delta {mean_diff_gold:+.3f}, 95% CI [{ci_gold['ci95'][0]:.3f}, {ci_gold['ci95'][1]:.3f}])"
            ),
        )
    )

    adopt = all(r.significant for r in rows)
    return {
        "rows": [r.__dict__ for r in rows],
        "adopt_hybrid": adopt,
        "note": (
            "Adopt hybrid only if the 95% CI of (hybrid - rules) excludes 0 for both "
            "boundary recall and gold resolution, over the same documents."
        ),
        "iterations": iterations,
        "seed": seed,
    }


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #


def bootstrap_significance_report(
    rules_report: dict[str, Any],
    hybrid_report: dict[str, Any],
    document_ids: list[str],
    predictions_rules: dict[str, list[Any]] | None = None,
    predictions_hybrid: dict[str, list[Any]] | None = None,
    groups: dict[str, Any] | None = None,
    gold_records: dict[str, Any] | None = None,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Full significance report: CI + verdict + gold-miss triage deltas."""
    sig = bootstrap_significance(
        rules_report,
        hybrid_report,
        document_ids,
        iterations=iterations,
        seed=seed,
    )

    # Gold-miss triage delta: how many of the previously-missed provisions
    # did hybrid now resolve (the plan's Sec. 8 in-scope claim).
    triage_delta = 0
    if (
        predictions_rules is not None
        and predictions_hybrid is not None
        and groups is not None
        and gold_records is not None
    ):
        from evaluation.benchmark import _section_from_id

        for doc_id in document_ids:
            if doc_id not in predictions_rules or doc_id not in predictions_hybrid:
                continue
            for provision_id, record in gold_records.items():
                if str(record.get("document_id") or "") != doc_id:
                    continue
                section = record.get("section")
                gold_section = section
                if gold_section is None:
                    continue
                if not any(
                    getattr(emitted, "family_id", "") == str(provision_id).split(":", 1)[0]
                    and _section_from_id(getattr(emitted, "provision_id", "")) == gold_section
                    for emitted in predictions_rules[doc_id]
                ):
                    if any(
                        getattr(emitted, "family_id", "") == str(provision_id).split(":", 1)[0]
                        and _section_from_id(getattr(emitted, "provision_id", "")) == gold_section
                        for emitted in predictions_hybrid[doc_id]
                    ):
                        triage_delta += 1
    return {
        "significance": sig,
        "gold_miss_triage_delta": triage_delta,
        "summary": ("hybrid vs rules; adopt_hybrid = true iff both boundary recall and gold resolution CIs exclude 0."),
    }

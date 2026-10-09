"""Train the Tier-2 provision-boundary disambiguator (ADR-0009 §2.1, plan step 4).

Builds silver labels from stamps the corpus already carries — a candidate is a
positive when its base number equals the ``section_number`` of the chunk it
overlaps (and the chunk is not an L4-derived guess), negative otherwise.
Regulation / notification / rule documents are excluded by default because
their ``section_number`` is known noise (``scripts/strip_reg_section_noise.py``,
1,518 + 36 + 298 bogus stamps).

Training is CPU-only, seconds-to-minutes, and **sklearn is imported lazily**:
with the dependency absent the script still builds and reports the label
distribution (``--labels-only``), then exits without writing an artifact.

Artifacts::

    models/provision_boundaries.joblib          # StandardScaler + LogisticRegression
    models/provision_boundaries_metrics.json    # metrics + artifact sha256 + config

The split is **grouped by document_id** (never by candidate) so train/test
never share a document — the plan's leakage guard.

Usage::

    python scripts/train_provision_boundaries.py --labels-only       # inspect label supply
    python scripts/train_provision_boundaries.py                     # fit + write artifacts
    python scripts/train_provision_boundaries.py --payload-index path/to/payload_index.jsonl

Exit codes: 0 ok, 2 missing payload index, 3 insufficient labels, 4 sklearn unavailable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.provision_extraction_eval import (
    DEFAULT_PAYLOAD_INDEX,
    DocumentChunks,
    group_payload_documents,
    load_payload_index,
)
from evaluation.resolution import norm_section

logger = logging.getLogger("train.provision.boundaries")

DEFAULT_MODEL_OUT = PROJECT_ROOT / "models" / "provision_boundaries.joblib"
DEFAULT_METRICS_OUT = PROJECT_ROOT / "models" / "provision_boundaries_metrics.json"
DEFAULT_SEED = 20260928

#: Document types whose ``section_number`` is known noise — never train on them.
DEFAULT_EXCLUDED_DOC_TYPES = frozenset({"regulation", "notification", "rule"})


@dataclass
class TrainingRow:
    """One candidate's feature dict + silver label, tagged with its document."""

    features: dict[str, float]
    label: int
    document_id: str
    source_pattern: str


# --------------------------------------------------------------------------- #
# Label building (pure)
# --------------------------------------------------------------------------- #


def _join_document(chunks: list[dict[str, Any]]) -> tuple[str, list[int], list[int]]:
    """Join chunk text and return ``(text, starts, lengths)`` per chunk."""
    parts = [str(chunk.get("chunk_text") or "") for chunk in chunks]
    text = "\n".join(parts)
    starts: list[int] = []
    position = 0
    for part in parts:
        starts.append(position)
        position += len(part) + 1
    return text, starts, [len(part) for part in parts]


def _chunk_index_for_offset(starts: list[int], lengths: list[int], offset: int) -> int | None:
    for index, start in enumerate(starts):
        if start <= offset < start + lengths[index] + 1:
            return index
    return None


def build_training_rows(
    groups: dict[str, DocumentChunks],
    *,
    excluded_document_types: frozenset[str] = DEFAULT_EXCLUDED_DOC_TYPES,
) -> list[TrainingRow]:
    """Build silver-labeled training rows from already-indexed documents."""
    from app.rag.provision_extractor import extract_features, generate_candidates

    rows: list[TrainingRow] = []
    for document_id, group in groups.items():
        if group.document_type.lower() in excluded_document_types:
            continue
        if not group.chunks:
            continue
        text, starts, lengths = _join_document(group.chunks)
        candidates = generate_candidates(text)
        if not candidates:
            continue

        prev_accepted: int | None = None
        for candidate in candidates:
            chunk_index = _chunk_index_for_offset(starts, lengths, candidate.char_offset)
            chunk = group.chunks[chunk_index] if chunk_index is not None else {}
            silver_section = norm_section(chunk.get("section_number"))
            chunk_source = str(chunk.get("source") or "")
            candidate_base = norm_section(candidate.raw_number)

            is_positive = bool(
                silver_section
                and candidate_base
                and candidate_base == silver_section
                and chunk_source != "L4_override"
                and candidate.grammar_type != "dotted",
            )
            features = extract_features(
                text,
                candidate,
                act_name=group.act_name,
                prev_accepted=prev_accepted,
                first_occurrence=True,
                sections_covered=set(chunk.get("sections_covered") or []),
                engine_confidence=float(chunk.get("confidence") or 0.0),
            )
            rows.append(
                TrainingRow(
                    features=features,
                    label=1 if is_positive else 0,
                    document_id=document_id,
                    source_pattern=candidate.source_pattern,
                ),
            )
            if is_positive and candidate_base:
                prev_accepted = int(candidate_base)
    return rows


def split_by_document(
    rows: list[TrainingRow], *, test_fraction: float = 0.25, seed: int = DEFAULT_SEED,
) -> tuple[list[TrainingRow], list[TrainingRow]]:
    """Group-aware split: whole documents go to train or test, never both."""
    document_ids = sorted({row.document_id for row in rows})
    rng = random.Random(seed)  # noqa: S311 - deterministic split, not security
    rng.shuffle(document_ids)
    test_count = max(1, int(len(document_ids) * test_fraction)) if len(document_ids) > 1 else 0
    test_documents = set(document_ids[:test_count])
    train = [row for row in rows if row.document_id not in test_documents]
    test = [row for row in rows if row.document_id in test_documents]
    return train, test


# --------------------------------------------------------------------------- #
# Fitting (lazy sklearn)
# --------------------------------------------------------------------------- #


def _matrix(rows: list[TrainingRow]) -> tuple[list[list[float]], list[int]]:
    from app.rag.provision_extractor import feature_vector

    return [feature_vector(row.features) for row in rows], [row.label for row in rows]


def _binary_metrics(y_true: list[int], y_pred: list[int]) -> dict[str, float]:
    true_positive = sum(1 for truth, pred in zip(y_true, y_pred, strict=True) if truth == 1 and pred == 1)
    false_positive = sum(1 for truth, pred in zip(y_true, y_pred, strict=True) if truth == 0 and pred == 1)
    false_negative = sum(1 for truth, pred in zip(y_true, y_pred, strict=True) if truth == 1 and pred == 0)
    correct = sum(1 for truth, pred in zip(y_true, y_pred, strict=True) if truth == pred)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "accuracy": round(correct / len(y_true), 6) if y_true else 0.0,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def fit_model(rows: list[TrainingRow], *, seed: int = DEFAULT_SEED):
    """Fit the scaled LogisticRegression pipeline (raises when sklearn is absent)."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    train_rows, test_rows = split_by_document(rows, seed=seed)
    if not train_rows:
        train_rows = rows
    x_train, y_train = _matrix(train_rows)
    model = Pipeline([
        ("scale", StandardScaler()),
        (
            "lr",
            LogisticRegression(class_weight="balanced", C=0.5, solver="lbfgs", max_iter=1000, random_state=seed),
        ),
    ])
    model.fit(x_train, y_train)

    x_test, y_test = _matrix(test_rows) if test_rows else (x_train, y_train)
    predictions = [int(probability >= 0.5) for probability in model.predict_proba(x_test)[:, 1]]
    metrics = {
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "train_documents": len({row.document_id for row in train_rows}),
        "test_documents": len({row.document_id for row in test_rows}),
        "test": _binary_metrics(y_test, predictions),
    }
    return model, metrics


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train the Tier-2 provision-boundary disambiguator.")
    parser.add_argument("--payload-index", type=Path, default=DEFAULT_PAYLOAD_INDEX, help="payload_index.jsonl path.")
    parser.add_argument("--model-out", type=Path, default=DEFAULT_MODEL_OUT, help="Joblib artifact output path.")
    parser.add_argument("--metrics-out", type=Path, default=DEFAULT_METRICS_OUT, help="Metrics JSON output path.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Random seed (split + LR).")
    parser.add_argument("--min-positives", type=int, default=25, help="Minimum positive labels required to fit.")
    parser.add_argument("--labels-only", action="store_true", help="Build + report labels; do not fit.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.payload_index.exists():
        logger.error("payload index not found: %s", args.payload_index)
        return 2

    groups = group_payload_documents(load_payload_index(args.payload_index))
    rows = build_training_rows(groups)
    distribution = Counter(row.label for row in rows)
    logger.info(
        "silver labels: %d rows (%d positive / %d negative) across %d documents",
        len(rows),
        distribution.get(1, 0),
        distribution.get(0, 0),
        len({row.document_id for row in rows}),
    )

    if args.labels_only:
        summary = {
            "rows": len(rows),
            "positives": distribution.get(1, 0),
            "negatives": distribution.get(0, 0),
            "documents": len({row.document_id for row in rows}),
            "by_source_pattern": dict(Counter(row.source_pattern for row in rows if row.label == 1)),
        }
        args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
        args.metrics_out.write_text(json.dumps({"labels_only": summary}, indent=2), encoding="utf-8")
        return 0

    if distribution.get(1, 0) < args.min_positives:
        logger.error("insufficient positive labels (%d < %d); not fitting", distribution.get(1, 0), args.min_positives)
        return 3

    try:
        model, metrics = fit_model(rows, seed=args.seed)
    except ImportError as exc:
        logger.error("scikit-learn unavailable (%s); rerun with --labels-only or install the Tier-2 dependency", exc)
        return 4

    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    import joblib

    joblib.dump(model, args.model_out)
    artifact_sha = hashlib.sha256(args.model_out.read_bytes()).hexdigest()

    metrics.update({
        "seed": args.seed,
        "rows": len(rows),
        "positives": distribution.get(1, 0),
        "negatives": distribution.get(0, 0),
        "artifact": str(args.model_out),
        "artifact_sha256": artifact_sha,
    })
    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    logger.info("wrote %s + %s (test F1=%.3f)", args.model_out, args.metrics_out, metrics["test"]["f1"])
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())

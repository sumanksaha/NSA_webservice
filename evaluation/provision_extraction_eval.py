"""Provision-extraction evaluation harness (ADR-0009 / provision-extraction-plan §4).

Scores the Tier-1/Tier-2 provision extractor over already-indexed corpus text:

* **Boundary precision / recall / F1** — per ``(family, section)`` key, micro
  overall and macro per numbering family (Acts / dotted regulations / …).
* **Noise-stamp rate** — emitted stamps that are out of the act's range,
  year-like, or duplicated within a document; target ≈ 0.
* **Gold resolution** — the share of the 99 gold provisions whose emitted
  ``provision_id`` round-trips through ``benchmark._section_from_id`` to the
  gold section (the plan's "gold id round-trip" gate).

The harness is pure over an input payload index (``{"id", "payload"}`` records,
the cache shape written by ``evaluation.resolution.build_payload_index``), so
it runs offline in tests without Qdrant.  Chunk ids and vectors are never
touched — this is a read-only scorer.

Usage::

    python -m evaluation.provision_extraction_eval                 # frozen payload cache
    python -m evaluation.provision_extraction_eval --mode hybrid
    python -m evaluation.provision_extraction_eval --limit-docs 20
    python -m evaluation.provision_extraction_eval --payload-index path/to/payload_index.jsonl

Exit codes: 0 ok, 2 missing/unusable payload index.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.benchmark import _section_from_id, load_gold_registry
from evaluation.config import BOOTSTRAP_ITERATIONS, BOOTSTRAP_SEED, CACHE_DIR
from evaluation.provision_significance import bootstrap_significance_report

logger = logging.getLogger(__name__)

DEFAULT_PAYLOAD_INDEX = CACHE_DIR / "payload_index.jsonl"
from evaluation.resolution import norm_section

logger = logging.getLogger(__name__)

DEFAULT_PAYLOAD_INDEX = CACHE_DIR / "payload_index.jsonl"
DEFAULT_OUT_PATH = PROJECT_ROOT / "evaluation" / "out" / "provision_extraction_eval.json"


# --------------------------------------------------------------------------- #
# Payload grouping
# --------------------------------------------------------------------------- #


@dataclass
class DocumentChunks:
    """One document's ordered chunks + document-level stamps."""

    document_id: str
    act_name: str = ""
    document_title: str = ""
    document_type: str = ""
    chunks: list[dict[str, Any]] = field(default_factory=list)


def load_payload_index(path: Path | str) -> list[dict[str, Any]]:
    """Read a ``payload_index.jsonl`` file into a list of ``{id, payload}``."""
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def group_payload_documents(records: Iterable[Mapping[str, Any]]) -> dict[str, DocumentChunks]:
    """Group payload records by ``document_id``, ordered by ``chunk_index``."""
    groups: dict[str, DocumentChunks] = {}
    for record in records:
        payload = dict(record.get("payload") or {})
        point_id = str(record.get("id") or payload.get("chunk_id") or "")
        document_id = str(payload.get("document_id") or point_id)
        if not document_id:
            continue
        group = groups.get(document_id)
        if group is None:
            group = DocumentChunks(
                document_id=document_id,
                act_name=str(payload.get("act_name") or ""),
                document_title=str(payload.get("document_title") or payload.get("title") or ""),
                document_type=str(payload.get("document_type") or ""),
            )
            groups[document_id] = group
        else:
            group.act_name = group.act_name or str(payload.get("act_name") or "")
            group.document_title = group.document_title or str(payload.get("document_title") or "")
            group.document_type = group.document_type or str(payload.get("document_type") or "")
        group.chunks.append({
            "chunk_id": point_id,
            "chunk_index": int(payload.get("chunk_index") or 0),
            "chunk_text": str(payload.get("chunk_text") or ""),
            "section_number": payload.get("section_number"),
            "clause_number": payload.get("clause_number"),
            "source": payload.get("source"),
            "sections_covered": list(payload.get("sections_covered") or []),
            "confidence": float(payload.get("confidence") or 0.0),
        })
    for group in groups.values():
        group.chunks.sort(key=lambda chunk: chunk["chunk_index"])
    return groups


def predict_documents(
    groups: Mapping[str, DocumentChunks],
    disambiguator: Any | None = None,
) -> dict[str, list[Any]]:
    """Run the extractor over every document; return ``{document_id: records}``."""
    from app.rag.provision_extractor import isolate_chunks

    predictions: dict[str, list[Any]] = {}
    for document_id, group in groups.items():
        predictions[document_id] = isolate_chunks(
            group.chunks,
            act_name=group.act_name,
            document_id=document_id,
            document_title=group.document_title,
            disambiguator=disambiguator,
        )
    return predictions


# --------------------------------------------------------------------------- #
# Metric primitives
# --------------------------------------------------------------------------- #


def boundary_prf(predicted: set[tuple[str, str]], reference: set[tuple[str, str]]) -> dict[str, float | int]:
    """Precision / recall / F1 over ``(family, section)`` key sets."""
    true_positive = len(predicted & reference)
    false_positive = len(predicted - reference)
    false_negative = len(reference - predicted)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "tp": true_positive,
        "fp": false_positive,
        "fn": false_negative,
    }


def per_family_prf(
    predicted: set[tuple[str, str]], reference: set[tuple[str, str]]
) -> dict[str, dict[str, float | int]]:
    """Macro P/R/F1 broken out by family prefix."""
    families = sorted({family for family, _ in predicted | reference})
    return {
        family: boundary_prf(
            {key for key in predicted if key[0] == family},
            {key for key in reference if key[0] == family},
        )
        for family in families
    }


def predicted_keys(records: Iterable[Any]) -> set[tuple[str, str]]:
    """``(family, base section)`` keys for a document's provision records."""
    keys: set[tuple[str, str]] = set()
    for record in records:
        section = norm_section(getattr(record, "section", None))
        family = getattr(record, "family_id", "") or ""
        if section:
            keys.add((family, section))
    return keys


def gold_reference_keys(gold_records: Mapping[str, Mapping[str, Any]]) -> dict[str, set[tuple[str, str]]]:
    """Per-document ``(family, section)`` reference sets from the gold registry."""
    references: dict[str, set[tuple[str, str]]] = {}
    for provision_id, record in gold_records.items():
        document_id = str(record.get("document_id") or "")
        family = str(provision_id).split(":", 1)[0]
        section = norm_section(record.get("section") or _section_from_id(provision_id))
        if not document_id or not section:
            continue
        references.setdefault(document_id, set()).add((family, section))
    return references


# --------------------------------------------------------------------------- #
# Full evaluation
# --------------------------------------------------------------------------- #


def noise_stamp_rate(predictions: Mapping[str, list[Any]], groups: Mapping[str, DocumentChunks]) -> dict[str, Any]:
    """Fraction of emitted stamps that are noise (range / year / duplicate)."""
    from app.rag.legal_sections import is_known_section_for_act

    total = 0
    noise = 0
    samples: list[dict[str, str]] = []
    for document_id, records in predictions.items():
        act_name = groups[document_id].act_name if document_id in groups else ""
        seen: set[str] = set()
        for record in records:
            total += 1
            section = str(getattr(record, "section", "") or "")
            # Duplicate identity is the full provision id — ``31`` and ``31(2)``
            # are distinct records that merely share a base section.
            key = str(getattr(record, "provision_id", ""))
            base = norm_section(section)
            known = is_known_section_for_act(section, act_name)
            year_like = bool(base) and 1900 <= int(base) <= 2100
            duplicate = key in seen
            seen.add(key)
            if known is False or year_like or duplicate:
                noise += 1
                if len(samples) < 25:
                    reason = "out_of_range" if known is False else "year_like" if year_like else "duplicate"
                    samples.append({
                        "document_id": document_id,
                        "provision_id": getattr(record, "provision_id", ""),
                        "reason": reason,
                    })
    return {
        "noise": noise,
        "total": total,
        "rate": round(noise / total, 6) if total else 0.0,
        "samples": samples,
    }


def gold_resolution(
    predictions: Mapping[str, list[Any]], gold_records: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Share of gold provisions whose emitted id round-trips to the gold section."""
    resolved = 0
    misses: list[str] = []
    for provision_id, record in gold_records.items():
        document_id = str(record.get("document_id") or "")
        family = str(provision_id).split(":", 1)[0]
        section = norm_section(record.get("section") or _section_from_id(provision_id))
        hit = False
        for emitted in predictions.get(document_id, []):
            if getattr(emitted, "family_id", "") != family:
                continue
            if _section_from_id(getattr(emitted, "provision_id", "")) == section:
                hit = True
                break
        resolved += int(hit)
        if not hit:
            misses.append(provision_id)
    total = len(gold_records)
    return {
        "resolved": resolved,
        "total": total,
        "rate": round(resolved / total, 6) if total else 0.0,
        "misses": misses,
    }


def classify_gold_misses(
    predictions: Mapping[str, list[Any]],
    groups: Mapping[str, DocumentChunks],
    gold_records: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Split unresolved gold provisions into actionable buckets.

    Separates *absent text* (the instrument is not in the corpus / has no
    section number) from *bad boundaries* (the text is present but the
    extractor did not emit the gold section) — the plan's §8 requirement that
    only the second class is in extraction scope.
    """
    buckets: dict[str, list[str]] = {
        "instrument_level": [],  # whole-instrument refs (no section number)
        "document_absent": [],  # gold document_id not present in the payload index
        "bad_boundary": [],  # document present, but the section was not emitted
    }
    resolved = 0
    for provision_id, record in gold_records.items():
        document_id = str(record.get("document_id") or "")
        section = norm_section(record.get("section") or _section_from_id(provision_id))
        if not document_id or section is None:
            buckets["instrument_level"].append(provision_id)
            continue
        if document_id not in groups:
            buckets["document_absent"].append(provision_id)
            continue
        family = str(provision_id).split(":", 1)[0]
        hit = any(
            getattr(emitted, "family_id", "") == family
            and _section_from_id(getattr(emitted, "provision_id", "")) == section
            for emitted in predictions.get(document_id, [])
        )
        if hit:
            resolved += 1
        else:
            buckets["bad_boundary"].append(provision_id)
    return {
        "resolved": resolved,
        "instrument_level": {"count": len(buckets["instrument_level"]), "provisions": buckets["instrument_level"]},
        "document_absent": {"count": len(buckets["document_absent"]), "provisions": buckets["document_absent"]},
        "bad_boundary": {"count": len(buckets["bad_boundary"]), "provisions": buckets["bad_boundary"]},
    }


def evaluate(
    groups: Mapping[str, DocumentChunks],
    gold_records: Mapping[str, Mapping[str, Any]],
    disambiguator: Any | None = None,
    *,
    mode: str = "rules",
) -> dict[str, Any]:
    """Run the full evaluation over *groups* against the gold registry."""
    predictions = predict_documents(groups, disambiguator)
    references = gold_reference_keys(gold_records)
    evaluated_docs = sorted(set(predictions) & set(references))

    predicted_all: set[tuple[str, str]] = set()
    reference_all: set[tuple[str, str]] = set()
    per_document: dict[str, Any] = {}
    for document_id in evaluated_docs:
        predicted = predicted_keys(predictions[document_id])
        reference = references[document_id]
        predicted_all |= predicted
        reference_all |= reference
        per_document[document_id] = boundary_prf(predicted, reference)

    return {
        "mode": mode,
        "documents_available": len(groups),
        "documents_evaluated": len(evaluated_docs),
        "boundary": {
            "micro": boundary_prf(predicted_all, reference_all),
            "per_family": per_family_prf(predicted_all, reference_all),
        },
        "noise_stamp_rate": noise_stamp_rate(predictions, groups),
        "gold_resolution": gold_resolution(predictions, gold_records),
        "gold_miss_triage": classify_gold_misses(predictions, groups, gold_records),
        "per_document": per_document,
        "metric_note": (
            "The gold registry is the benchmark's question-relevance subset, not an exhaustive "
            "boundary annotation. Boundary precision against it is a lower-bound diagnostic; "
            "the hard gates are noise_stamp_rate (~0) and gold_resolution."
        ),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate the statutory provision extractor over an indexed corpus.")
    parser.add_argument("--payload-index", type=Path, default=DEFAULT_PAYLOAD_INDEX, help="payload_index.jsonl path.")
    parser.add_argument("--mode", type=str, default="rules", choices=["rules", "hybrid"], help="Disambiguation mode.")
    parser.add_argument("--limit-docs", type=int, default=None, help="Evaluate only the first N documents.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH, help="Where to write the JSON report.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the report to stdout.")
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Run the paired-bootstrap significance test on the rules vs hybrid boundary recall / gold resolution (policy gate, §19).",
    )
    parser.add_argument(
        "--bootstrap-iterations",
        type=int,
        default=None,
        help="Override the bootstrap iteration count (default 10_000, from ``evaluation.config.BOOTSTRAP_ITERATIONS``).",
    )
    parser.add_argument(
        "--bootstrap-seed",
        type=int,
        default=None,
        help="Override the bootstrap seed (default 20260811, from ``evaluation.config.BOOTSTRAP_SEED``).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.payload_index.exists():
        logger.error(
            "payload index not found: %s — build it with evaluation.resolution.build_payload_index(...)",
            args.payload_index,
        )
        return 2

    records = load_payload_index(args.payload_index)
    groups = group_payload_documents(records)
    if args.limit_docs:
        groups = dict(list(groups.items())[: args.limit_docs])

    from app.rag.provision_extractor import Disambiguator

    report = evaluate(groups, load_gold_registry(), Disambiguator(mode=args.mode), mode=args.mode)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    report["evaluation_time"] = datetime.now(UTC).isoformat()
    report["bootstrap"] = None

    micro = report["boundary"]["micro"]
    logger.info(
        "provision extraction [%s]: boundary P=%.3f R=%.3f F1=%.3f | noise=%.4f | gold_resolution=%.3f",
        args.mode,
        micro["precision"],
        micro["recall"],
        micro["f1"],
        report["noise_stamp_rate"]["rate"],
        report["gold_resolution"]["rate"],
    )
    if args.bootstrap:
        # The bootstrap needs per-document data for BOTH modes.  The main report
        # may be empty (e.g. --mode rules but nothing matched); so compute the
        # two comparison reports explicitly and reuse the per-document keys.
        rules_report = evaluate(groups, load_gold_registry(), Disambiguator(mode="rules"), mode="rules")
        hybrid_report = evaluate(groups, load_gold_registry(), Disambiguator(mode="hybrid"), mode="hybrid")
        predictions_rules = predict_documents(groups, Disambiguator(mode="rules"))
        predictions_hybrid = predict_documents(groups, Disambiguator(mode="hybrid"))
        doc_ids = sorted(set(rules_report["per_document"]) | set(hybrid_report["per_document"]))
        significance = bootstrap_significance_report(
            rules_report,
            hybrid_report,
            doc_ids,
            predictions_rules=predictions_rules,
            predictions_hybrid=predictions_hybrid,
            groups=groups,
            gold_records=load_gold_registry(),
            iterations=args.bootstrap_iterations or BOOTSTRAP_ITERATIONS,
            seed=args.bootstrap_seed if args.bootstrap_seed is not None else BOOTSTRAP_SEED,
        )
        report["bootstrap"] = significance
        sig_rows = significance.get("significance", {}).get("rows") or []
        if sig_rows:
            logger.info(
                "bootstrap: %s | adopt_hybrid=%s",
                "; ".join(
                    f"{r['metric']}: {r['mean_a']:.3f}->{r['mean_b']:.3f} (95% CI [{r['ci95_low']:.3f}, {r['ci95_high']:.3f}], sig={r['significant']})"
                    for r in sig_rows
                ),
                significance.get("significance", {}).get("adopt_hybrid"),
            )
        else:
            logger.info("bootstrap: skipped (no documents to compare)")

    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    if args.pretty:
        print(json.dumps(report, indent=2, default=str))  # noqa: T201
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())

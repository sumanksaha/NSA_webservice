"""Corpus discovery engine for autonomous legal research (Phase 1).

Scans the benchmark gold registry to identify legal provisions that are
**registered as required evidence but not yet present** (or not fully present)
in the Qdrant corpus.  Maps missing provisions to their source documents and
emits ingestion requests for the research orchestrator to action.

This extends the Step 0 pre-annotation (``evaluation/step0_label_residual.py``)
— which is **question-centric** (124 residual QIDs), **human-gated** (requires
confirmed labels before action), and **post-hoc** (only runs after a full
evaluation pass) — into an **autonomous, provision-centric** gap discovery that
runs on all 97+ registered gold provisions, regardless of whether any question
currently references them.  The Step 0 targets are still cross-referenced
(see :meth:`GapAnalyzer.cross_reference_step0_targets`) so the two views stay
consistent, but discovery no longer *blocks* on human labels.

Discovery checks two independent signals per provision:

(a) ``chunk_id`` presence — the gold registry's declared chunk id must be
    non-null AND exist in the Qdrant payload index.
(b) Section body text presence — the provision's section body must actually
    exist in the corpus (≥2 distinctive probe hits OR ≥200 resolved chars),
    checked via :class:`~evaluation.resolution.FamilyMap` +
    :func:`~evaluation.resolution.payload_to_keys` act/section matching across
    all payload-index chunks.  This catches provisions whose ``chunk_id`` is
    null (un-annotated) but whose text lives in an indexed chunk.

When the payload-index cache is unavailable the module degrades to
``chunk_id``-only checks (constraint: graceful fallback).

Data sources (all frozen, no LLM):

* ``benchmark/gold_provisions_v1.0.json`` — 97 provisions with ``chunk_id``,
  ``document_id``, ``collection``, ``section``, ``act``, ``title``, ``domain``.
* ``benchmark/gold_sources_v1.0.json`` — 22 source documents across 6
  collections (``collection → document_id → {act, provision_count}``).
* ``evaluation/out/ceiling_v5/cache/payload_index.jsonl`` — cached Qdrant
  chunk payloads (optional).

Design rules (mirroring the codebase's Phase 0/Phase 3 conventions):

* **Lazy imports** — heavy deps (Neo4j, Qdrant, evaluation modules) are
  imported inside functions so importing this module is side-effect-free.
* **Graceful degradation** — a missing payload-index cache, missing benchmark
  file, or unresolvable family does not raise; the module degrades to the least
  expensive signal available.
* **Pure function core** — :func:`classify_provision_gap` and
  :func:`group_provisions_by_document` are pure, fully unit-testable, and take
  plain dicts (no I/O).
* **Config-driven (Pattern A)** — ``RAG_RESEARCH_*`` flags resolve through
  :data:`app.shared.config.cfg` (Flask config wins, then env, then default).
* **Circuit breakers** — ingestion-request output is bounded by
  ``RAG_RESEARCH_MAX_INGESTION_REQUESTS``.

Gap types detected (per provision):

* ``unindexed`` — ``chunk_id`` is ``null`` and no chunk in the payload index
  covers this provision's (family, section).
* ``orphaned``  — ``chunk_id`` is set but absent from the payload index, AND
  no other chunk covers the (family, section).
* ``body_missing`` — a chunk covers the (family, section) but the section body
  text is absent (probe strings absent, resolved chars < 200).
* ``fragmented`` — a chunk covers the (family, section) and text is present
  but < 2 distinctive probe hits and < 150 chars (the 2026-09-27 corpus-gap
  audit finding: fragmented section bodies with the heading stranded in a
  neighbour chunk).
* ``present`` — chunk exists and section body verified (NOT a gap; the caller
  filters these out).

Severity (``high`` / ``medium`` / ``low``):

* ``high`` — referenced by >= 3 benchmark questions as a gold unit.
* ``medium`` — referenced by >= 1 question, or the document has >= 3 missing
  provisions total.
* ``low`` — no question references it and the document has < 3 missing
  provisions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.shared.config import cfg

logger = logging.getLogger(__name__)

__all__ = [
    "DiscoveryReport",
    "GapAnalyzer",
    "IngestionRequest",
    "ProvisionGap",
    "SCHEMA_VERSION",
    "SourceDocumentGap",
    "classify_provision_gap",
    "discover_corpus_gaps",
    "group_provisions_by_document",
]

#: Schema version for serialized reports (bumped on structure change).
SCHEMA_VERSION = "1.0"

#: Minimum combined characters of resolved corpus text for a provision to be
#: considered "body present" (mirrors ``MIN_RESOLVED_BODY_CHARS`` in
#: ``evaluation/step0_label_residual.py``).
BODY_PRESENT_MIN_CHARS = 200

#: Minimum combined characters for a provision to be considered "fragmented"
#: rather than fully "body_missing".
FRAGMENTED_MIN_CHARS = 150

#: Minimum distinctive probe hits for "body present" without the char threshold
#: (mirrors ``corpus_body_present`` in ``step0_label_residual.py``).
BODY_PROBE_MIN_HITS = 2

#: Threshold for high-severity gaps (question impact).
HIGH_SEVERITY_QUESTION_THRESHOLD = 3

#: Threshold for medium-severity gaps (question impact).
MEDIUM_SEVERITY_QUESTION_THRESHOLD = 1

#: Threshold for medium severity by document missing count.
MEDIUM_SEVERITY_DOC_MISSING_THRESHOLD = 3


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #


@dataclass
class ProvisionGap:
    """A gold provision that is missing or incomplete in the corpus.

    Attributes:
        provision_id: Registry id, e.g. ``"air_act:s15"``.
        family: Benchmark family prefix, e.g. ``"air_act"``.
        act: Full canonical Act name from the registry.
        section: Base section number (e.g. ``"15"``); ``None`` for whole-
            instrument references.
        title: Section title from the registry (often empty).
        domain: Domain tag, e.g. ``"ENVIRONMENT_POLLUTION"``.
        document_id: Source document id from the registry.
        collection: Qdrant collection name, e.g. ``"env_legal_768"``.
        chunk_id: The indexed chunk id if known; ``None`` if never indexed.
        gap_type: One of ``unindexed`` / ``orphaned`` / ``body_missing`` /
            ``fragmented`` / ``present``.
        evidence: Human-readable explanation of why the gap was classified.
        severity: ``high`` / ``medium`` / ``low``.
        question_refs: Sorted list of benchmark QIDs that reference this
            provision as a gold unit (populated when questions are loaded).
    """

    provision_id: str
    family: str
    act: str
    section: str | None
    title: str
    domain: str
    document_id: str | None
    collection: str | None
    chunk_id: str | None
    gap_type: str
    evidence: str
    severity: str = "low"
    question_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provision_id": self.provision_id,
            "family": self.family,
            "act": self.act,
            "section": self.section,
            "title": self.title,
            "domain": self.domain,
            "document_id": self.document_id,
            "collection": self.collection,
            "chunk_id": self.chunk_id,
            "gap_type": self.gap_type,
            "evidence": self.evidence,
            "severity": self.severity,
            "question_refs": list(self.question_refs),
        }


@dataclass
class SourceDocumentGap:
    """All missing provisions for one source document, grouped.

    Attributes:
        document_id: Source document id (matches ``gold_sources`` registry).
        act_name: Full Act name from the source registry.
        collection: Qdrant collection name.
        domain: Collection-level domain tag.
        provision_count: Total provisions registered for this document.
        missing: Missing provisions belonging to this document.
        indexed: Provision-level records for indexed (non-missing) provisions.
    """

    document_id: str
    act_name: str
    collection: str
    domain: str
    provision_count: int
    missing: list[ProvisionGap] = field(default_factory=list)
    indexed: list[ProvisionGap] = field(default_factory=list)

    @property
    def missing_count(self) -> int:
        return len(self.missing)

    @property
    def missing_ratio(self) -> float:
        """Share of the document's provisions that are missing."""
        if self.provision_count <= 0:
            return 0.0
        return self.missing_count / self.provision_count

    @property
    def affected_question_count(self) -> int:
        """Distinct benchmark questions that reference any missing provision."""
        return len({q for g in self.missing for q in g.question_refs})

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "act_name": self.act_name,
            "collection": self.collection,
            "domain": self.domain,
            "provision_count": self.provision_count,
            "missing_count": self.missing_count,
            "missing_ratio": round(self.missing_ratio, 3),
            "affected_question_count": self.affected_question_count,
            "missing_provision_ids": [g.provision_id for g in self.missing],
        }


@dataclass
class IngestionRequest:
    """A request for the research orchestrator / ingestion pipeline to action.

    Attributes:
        document_id: Source document id (for dedup against existing corpus).
        source_uri: File path or URL to fetch; ``None`` when only discoverable
            via the registry (the orchestrator / document-ingestion module
            resolves the actual source).
        act_name: Full Act name (human-readable label for the request).
        collection: Target Qdrant collection.
        domain: Domain tag.
        missing_provision_count: How many provisions are missing.
        total_provision_count: Registry count for this document.
        reason: Human-readable summary of why this document needs ingestion.
        gap_types: Breakdown of gap types among the missing provisions.
    """

    document_id: str
    source_uri: str | None
    act_name: str
    collection: str
    domain: str
    missing_provision_count: int
    total_provision_count: int
    reason: str
    gap_types: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source_uri": self.source_uri,
            "act_name": self.act_name,
            "collection": self.collection,
            "domain": self.domain,
            "missing_provision_count": self.missing_provision_count,
            "total_provision_count": self.total_provision_count,
            "reason": self.reason,
            "gap_types": dict(self.gap_types),
        }


@dataclass
class DiscoveryReport:
    """Full output of a corpus discovery run.

    Attributes:
        total_provisions: Total gold provisions scanned.
        indexed_provisions: Provisions confirmed present in the corpus.
        missing_provisions: Provision-level gap records (only actual gaps).
        source_gaps: Document-level gap groups.
        ingestion_requests: Bounded list of requests for the orchestrator.
        payload_index_size: Number of payload points consulted (``0`` if
            unavailable).
        timestamp: ISO-8601 UTC when the run completed.
        schema_version: Report schema version string.
        summary: Aggregate counts by gap_type + severity.
    """

    total_provisions: int
    indexed_provisions: int = 0
    missing_provisions: list[ProvisionGap] = field(default_factory=list)
    source_gaps: list[SourceDocumentGap] = field(default_factory=list)
    ingestion_requests: list[IngestionRequest] = field(default_factory=list)
    payload_index_size: int = 0
    timestamp: str = ""
    schema_version: str = SCHEMA_VERSION
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def missing_count(self) -> int:
        return len(self.missing_provisions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
            "total_provisions": self.total_provisions,
            "indexed_provisions": self.indexed_provisions,
            "missing_count": self.missing_count,
            "payload_index_size": self.payload_index_size,
            "summary": self.summary,
            "missing_provisions": [g.to_dict() for g in self.missing_provisions],
            "source_gaps": [g.to_dict() for g in self.source_gaps],
            "ingestion_requests": [r.to_dict() for r in self.ingestion_requests],
        }


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O — fully unit-testable with plain dicts)
# --------------------------------------------------------------------------- #


def _section_probe_strings(section: str | None) -> list[str]:
    """Build distinctive probe substrings for a section number.

    Mirrors the probe construction in ``step0_label_residual.py``:
    ``"section 15"``, ``"s.15"``, and the bare ``"15"``.
    """
    if not section:
        return []
    section_str = str(section)
    return [f"section {section_str}", f"s.{section_str}", section_str]


def _corpus_body_present(
    *,
    corpus_blob: str,
    section: str | None,
    corpus_probe_hits: int,
    resolved_chars: int,
) -> bool:
    """Whether a provision's section body is present in the corpus.

    Replicates the ``corpus_body_present`` logic from
    ``evaluation/step0_label_residual.py`` (additive, never flips True→False):

    * ``section is None`` (whole-instrument ref) → present if there are any
      corpus hits at all.
    * >= 2 distinctive probe hits → present.
    * else → present iff resolved text >= ``BODY_PRESENT_MIN_CHARS``.
    """
    if resolved_chars == 0 and corpus_probe_hits == 0:
        return False
    if section is None:
        return True
    if corpus_probe_hits >= BODY_PROBE_MIN_HITS:
        return True
    return resolved_chars >= BODY_PRESENT_MIN_CHARS


def classify_provision_gap(
    provision: dict[str, Any],
    payload_index: dict[str, dict] | None,
    coverage_index: dict[tuple[str, str | None], list[str]] | None = None,
) -> tuple[str, str]:
    """Classify a single gold-provision record into a gap type + evidence.

    Pure function: takes a provision dict (from ``gold_provisions_v1.0.json``),
    an optional payload index (``{chunk_id: payload}``), and an optional
    coverage index (``{(family, section): [chunk_id, ...]}``) built from the
    payload index via :func:`~evaluation.resolution.FamilyMap`.

    Returns ``(gap_type, evidence)``.

    Gap-type logic follows the research-doc spec and the Step 0 corpus-body
    heuristic:

    1. ``chunk_id`` is non-null AND in ``payload_index`` → check the body of
       that specific chunk.  If body present → ``"present"``; else
       ``"fragmented"`` / ``"body_missing"``.
    2. ``chunk_id`` is non-null but NOT in ``payload_index`` (orphaned) →
       fall back to ``coverage_index``: if any chunk covers the (family,
       section), check its body.  If none covers it → ``"orphaned"``.
    3. ``chunk_id`` is null → fall back to ``coverage_index``: if any chunk
       covers the (family, section), check its body.  If none covers it →
       ``"unindexed"``.
    4. No ``payload_index`` / ``coverage_index`` → ``chunk_id`` is null →
       ``"unindexed"``; ``chunk_id`` set → ``"present"`` (best effort).
    """
    chunk_id = provision.get("chunk_id")
    section = provision.get("section")

    # --- Case 4: no payload index available — chunk_id only ---
    if payload_index is None and coverage_index is None:
        if not chunk_id:
            return "unindexed", "chunk_id is null and no payload index available for body check"
        # chunk_id is set but we can't verify it exists or has a body.
        return "present", f"chunk_id '{chunk_id}' present (body check skipped: no payload index)"

    # --- Case 1: chunk_id is set and in the payload index ---
    if chunk_id is not None and payload_index is not None:
        key = str(chunk_id)
        if key in payload_index:
            payload = payload_index[key]
            text = str(payload.get("chunk_text") or payload.get("text") or "")
            return _check_body_text(provision, text, chunk_label=key)

    # --- Cases 2 & 3: chunk_id is null or orphaned — scan coverage index ---
    if coverage_index is not None:
        family = str(provision.get("id", "")).split(":", 1)[0] if provision.get("id") else ""
        section_str = str(section) if section else None
        covering_chunk_ids = coverage_index.get((family, section_str), [])

        if covering_chunk_ids:
            # A chunk covers this (family, section) — check its body.
            texts: list[str] = []
            for cid in covering_chunk_ids:
                if cid in payload_index:
                    p = payload_index[cid]
                    texts.append(str(p.get("chunk_text") or p.get("text") or ""))
            combined = "\n".join(texts)
            label = (
                f"{len(covering_chunk_ids)} chunk(s) covering ({family}, {section_str}) "
                f"via coverage index"
            )
            return _check_body_text(provision, combined, label)

        # No chunk covers this (family, section) in the payload index.
        if chunk_id is not None:
            return "orphaned", (
                f"chunk_id '{chunk_id}' not in payload index and no chunk covers "
                f"({family}, {section_str}) in coverage index"
            )
        return "unindexed", (
            f"chunk_id is null and no chunk in the payload index covers "
            f"({family}, {section_str})"
        )

    # --- No coverage index but payload_index present — chunk_id only ---
    if chunk_id is not None:
        if str(chunk_id) not in payload_index:
            return "orphaned", f"chunk_id '{chunk_id}' not found in payload index"
        # chunk_id is in the index.
        return "present", f"chunk_id '{chunk_id}' present in payload index"
    return "unindexed", "chunk_id is null (no payload index for body check)"


def _check_body_text(
    provision: dict[str, Any],
    text: str,
    chunk_label: str,
) -> tuple[str, str]:
    """Check if a provision's section body is present in the given chunk text.

    Shared helper used by both the direct ``chunk_id`` match path (Case 1) and
    the coverage-index scan path — eliminates the duplicated body-presence
    computation between them.

    Returns ``(gap_type, evidence)`` where gap_type is ``"present"``,
    ``"fragmented"``, or ``"body_missing"``.

    Body-presence rules mirror ``corpus_body_present`` from
    ``evaluation/step0_label_residual.py``:

    * Instrument-level ref (``section`` is None): any corpus hit = present.
    * Section-level ref: >= 2 distinctive probe hits OR >= 200 resolved chars.
    * < 150 chars with < 2 probe hits → fragmented; 0 chars → body_missing.
    """
    section = provision.get("section")
    section_str = str(section) if section else None
    text_lower = text.lower()
    probes = _section_probe_strings(section_str)
    probe_hits = sum(1 for p in probes if p and p.lower() in text_lower)
    resolved_chars = len(text.strip())

    # Instrument-level reference (section is None): per the Step 0
    # corpus_body_present rule, ANY corpus hit counts as present.
    if section_str is None:
        if resolved_chars > 0:
            return "present", (
                f"instrument-level body found in '{chunk_label}' ({resolved_chars} chars)"
            )
        return "body_missing", f"chunk '{chunk_label}' exists but body text is empty"

    # Section-level reference: apply the probe + char thresholds.
    if resolved_chars == 0:
        return "body_missing", f"chunk '{chunk_label}' exists but body text is empty"
    if resolved_chars < FRAGMENTED_MIN_CHARS and probe_hits < BODY_PROBE_MIN_HITS:
        return (
            "fragmented",
            f"chunk '{chunk_label}' exists but body is fragmented "
            f"({resolved_chars} chars, {probe_hits} probe hits < {BODY_PROBE_MIN_HITS})",
        )
    present = _corpus_body_present(
        corpus_blob=text_lower,
        section=section_str,
        corpus_probe_hits=probe_hits,
        resolved_chars=resolved_chars,
    )
    if not present:
        return (
            "body_missing",
            f"chunk '{chunk_label}' exists but section body probes absent "
            f"({probe_hits}/{BODY_PROBE_MIN_HITS} hits, {resolved_chars} chars)",
        )
    return "present", (
        f"chunk '{chunk_label}' body verified ({resolved_chars} chars, {probe_hits} probe hits)"
    )


# --------------------------------------------------------------------------- #
# Loaders (lazy imports, graceful degradation)
# --------------------------------------------------------------------------- #


def _load_gold_provisions() -> dict[str, dict[str, Any]]:
    """Load the gold provision registry, keyed by provision id."""
    from evaluation.benchmark import load_gold_registry

    return load_gold_registry()


def _load_gold_sources() -> dict[str, Any]:
    """Load the canonical source-document registry."""
    from evaluation.benchmark import load_gold_sources

    return load_gold_sources()


def _load_payload_index() -> dict[str, dict] | None:
    """Load the cached Qdrant payload index, gracefully.

    Returns ``None`` when the cache file does not exist (e.g. first run, or
    Qdrant has not been populated).  The discovery engine degrades to
    ``chunk_id``-only checks in that case.
    """
    try:
        from evaluation.config import CACHE_DIR
        from evaluation.resolution import build_payload_index
    except ImportError:
        logger.debug("payload index loader not available (evaluation package not importable)")
        return None

    cache_path = CACHE_DIR / "payload_index.jsonl"
    if not cache_path.exists():
        logger.info("payload index cache not found at %s -- running in registry-only mode", cache_path)
        return None

    # build_payload_index reads the cache itself when present (no Qdrant call).
    # Pass a no-op store_factory that is never invoked because the cache exists.
    index = build_payload_index(store_factory=lambda _coll: None, collections=[], force=False)
    logger.info("payload index loaded: %d points", len(index))
    return index


def _build_coverage_index(
    payload_index: dict[str, dict],
    family_map: Any,
) -> dict[tuple[str, str | None], list[str]]:
    """Build a ``{(family, section): [chunk_id, ...]}`` coverage index.

    Uses ``payload_to_keys`` from ``evaluation.resolution`` to derive all
    ``(family, section)`` keys from each chunk's payload, then inverts the
    mapping so we can quickly look up which chunks cover a given provision.

    This is the body-text check signal (b) from the discovery spec: when a
    provision's ``chunk_id`` is null, we scan the coverage index to see if
    any other chunk actually contains the section body text.
    """
    from evaluation.resolution import payload_to_keys

    index: dict[tuple[str, str | None], list[str]] = {}
    for chunk_id_str, payload in payload_index.items():
        keys = payload_to_keys(payload, family_map)
        for key in keys:
            index.setdefault(key, []).append(chunk_id_str)
    logger.info("coverage index built: %d (family, section) -> chunk_id mappings", len(index))
    return index


def _build_family_map() -> Any | None:
    """Build a ``FamilyMap`` from the gold registry, gracefully."""
    try:
        from evaluation.resolution import FamilyMap

        return FamilyMap()
    except Exception as exc:
        logger.warning("FamilyMap construction failed (%s) -- coverage-index scan disabled", exc)
        return None


def _load_benchmark_questions() -> list[Any] | None:
    """Load benchmark questions (for question-impact analysis).

    Returns ``None`` when the benchmark file is unavailable, so gap analysis
    degrades gracefully without question impact data.
    """
    try:
        from evaluation.benchmark import load_questions
    except ImportError:
        logger.debug("benchmark loader not available")
        return None
    try:
        return load_questions()
    except FileNotFoundError:
        logger.info("benchmark file not found -- running without question-impact analysis")
        return None
    except Exception as exc:
        logger.warning("benchmark load failed (%s) -- running without question-impact analysis", exc)
        return None


# --------------------------------------------------------------------------- #
# Gap classification + grouping (pure logic over loaded data)
# --------------------------------------------------------------------------- #


def _build_question_ref_map(
    questions: list[Any],
    provisions: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Map each provision_id -> sorted list of QIDs that reference it.

    Reuses ``BenchmarkQuestion.gold_units`` (primary + acceptable + supporting)
    so every gold unit reference is captured, not just primary.
    """
    ref_map: dict[str, set[str]] = {}
    for q in questions:
        try:
            units = q.gold_units
        except AttributeError:
            continue
        for u in units:
            pid = str(getattr(u, "provision_id", "") or "")
            if pid:
                ref_map.setdefault(pid, set()).add(q.question_id)
    return {pid: sorted(qids) for pid, qids in ref_map.items()}


def _severity_for(
    question_refs: list[str],
    doc_missing_count: int,
) -> str:
    """Assign severity based on question impact and document gap count.

    * ``high``   — referenced by >= HIGH_SEVERITY_QUESTION_THRESHOLD questions.
    * ``medium`` — referenced by >= MEDIUM_SEVERITY_QUESTION_THRESHOLD question
      or document has >= MEDIUM_SEVERITY_DOC_MISSING_THRESHOLD missing provisions.
    * ``low``    — otherwise.
    """
    if len(question_refs) >= HIGH_SEVERITY_QUESTION_THRESHOLD:
        return "high"
    if (
        len(question_refs) >= MEDIUM_SEVERITY_QUESTION_THRESHOLD
        or doc_missing_count >= MEDIUM_SEVERITY_DOC_MISSING_THRESHOLD
    ):
        return "medium"
    return "low"


def group_provisions_by_document(
    gaps: list[tuple[str, str, str, dict[str, Any]]],
    source_lookup: dict[str, dict[str, Any]],
) -> list[SourceDocumentGap]:
    """Group classified gaps by their source document.

    Args:
        gaps: List of ``(provision_id, gap_type, evidence, provision_record)``
            tuples for provisions that are actually missing.
        source_lookup: Mapping ``document_id -> {act, collection, domain,
            provision_count}`` from the gold-sources registry.

    Returns:
        List of :class:`SourceDocumentGap` sorted by missing count desc.
    """
    by_doc: dict[str, list[tuple[str, str, str, dict[str, Any]]]] = {}
    for provision_id, gap_type, evidence, rec in gaps:
        doc_id = str(rec.get("document_id") or "")
        if not doc_id:
            doc_id = provision_id.split(":", 1)[0] if ":" in provision_id else "unknown"
        by_doc.setdefault(doc_id, []).append((provision_id, gap_type, evidence, rec))

    result: list[SourceDocumentGap] = []
    for doc_id, entries in by_doc.items():
        meta = source_lookup.get(doc_id, {"act": "", "collection": "", "domain": "", "provision_count": len(entries)})
        gap_list = [
            ProvisionGap(
                provision_id=pid,
                family=pid.split(":", 1)[0],
                act=str(rec.get("act", meta.get("act", ""))),
                section=rec.get("section"),
                title=str(rec.get("title", "")),
                domain=str(rec.get("domain", meta.get("domain", ""))),
                document_id=doc_id,
                collection=str(rec.get("collection", "") or meta.get("collection", "")),
                chunk_id=rec.get("chunk_id"),
                gap_type=gt,
                evidence=ev,
            )
            for pid, gt, ev, rec in entries
        ]
        result.append(
            SourceDocumentGap(
                document_id=doc_id,
                act_name=meta.get("act", ""),
                collection=meta.get("collection", ""),
                domain=meta.get("domain", ""),
                provision_count=meta.get("provision_count", 0),
                missing=gap_list,
            )
        )

    result.sort(key=lambda g: g.missing_count, reverse=True)
    return result


def _build_source_lookup(sources: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Flatten the gold-sources registry into ``document_id -> metadata``.

    ``gold_sources_v1.0.json`` structure::

        {"collections": {"<coll>": {"domain": "...", "document_ids": {
            "<doc_id>": {"act": "...", "provision_count": N}
        }}}}
    """
    lookup: dict[str, dict[str, Any]] = {}
    for coll_name, coll_info in sources.get("collections", {}).items():
        domain = coll_info.get("domain", "")
        for doc_id, doc_info in coll_info.get("document_ids", {}).items():
            lookup[doc_id] = {
                "act": doc_info.get("act", ""),
                "collection": coll_name,
                "domain": domain,
                "provision_count": doc_info.get("provision_count", 0),
            }
    return lookup


def _build_ingestion_requests(
    source_gaps: list[SourceDocumentGap],
    question_ref_map: dict[str, list[str]],
    max_requests: int,
) -> list[IngestionRequest]:
    """Turn source-document gaps into bounded ingestion requests.

    The ``max_requests`` cap is the circuit breaker from
    ``RAG_RESEARCH_MAX_INGESTION_REQUESTS``.
    """
    requests: list[IngestionRequest] = []
    # Rank by affected question count (desc), then missing count (desc).
    ranked = sorted(
        source_gaps,
        key=lambda g: (g.affected_question_count, g.missing_count, g.missing_ratio),
        reverse=True,
    )
    for gap in ranked:
        if not gap.missing:
            continue

        gap_type_counts: dict[str, int] = {}
        for g in gap.missing:
            gap_type_counts[g.gap_type] = gap_type_counts.get(g.gap_type, 0) + 1

        reason_parts = [
            f"{gap.missing_count}/{gap.provision_count} provisions missing "
            f"({gap.missing_ratio:.0%} gap ratio)",
        ]
        affected_qids: set[str] = set()
        for g in gap.missing:
            affected_qids.update(question_ref_map.get(g.provision_id, []))
        if affected_qids:
            reason_parts.append(f"affects {len(affected_qids)} question(s)")
        if gap_type_counts:
            breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(gap_type_counts.items()))
            reason_parts.append(f"gap types: {breakdown}")

        requests.append(
            IngestionRequest(
                document_id=gap.document_id,
                source_uri=None,  # resolved by the document-ingestion module
                act_name=gap.act_name,
                collection=gap.collection,
                domain=gap.domain,
                missing_provision_count=gap.missing_count,
                total_provision_count=gap.provision_count,
                reason="; ".join(reason_parts),
                gap_types=gap_type_counts,
            )
        )
        if len(requests) >= max_requests:
            logger.warning(
                "corpus discovery: ingestion request cap (%d) reached -- %d sources dropped",
                max_requests,
                len(ranked) - len(requests),
            )
            break

    return requests


def _summary(
    gaps: list[ProvisionGap],
    source_gaps: list[SourceDocumentGap],
) -> dict[str, Any]:
    """Aggregate counts by gap_type, severity, and domain."""
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for g in gaps:
        by_type[g.gap_type] = by_type.get(g.gap_type, 0) + 1
        by_severity[g.severity] = by_severity.get(g.severity, 0) + 1
    by_domain: dict[str, int] = {}
    for sg in source_gaps:
        key = sg.domain or "unknown"
        by_domain[key] = by_domain.get(key, 0) + sg.missing_count
    return {
        "by_gap_type": by_type,
        "by_severity": by_severity,
        "by_domain": by_domain,
        "source_documents_with_gaps": len(source_gaps),
    }


# --------------------------------------------------------------------------- #
# GapAnalyzer — extends discovery with question-impact + Step 0 cross-reference
# --------------------------------------------------------------------------- #


class GapAnalyzer:
    """Cross-reference a discovery report with benchmark questions.

    Extends the Step 0 pre-annotation (question-centric, 124 residual QIDs,
    human-gated) into an autonomous, full-benchmark question-impact analysis.

    This is the "extend evidence gap analysis beyond pre-annotation" item
    from the Phase 1 roadmap: the analyzer maps each missing provision to
    *all* benchmark questions that reference it (not just the residual set),
    ranks gaps by downstream impact, and cross-references Step 0's
    corpus-fill targets to avoid double-counting.
    """

    def __init__(
        self,
        report: DiscoveryReport,
        questions: list[Any] | None = None,
    ) -> None:
        self.report = report
        self._questions: list[Any] = list(questions or [])
        self._by_qid: dict[str, Any] = {q.question_id: q for q in self._questions}

    @classmethod
    def run(cls) -> "GapAnalyzer":
        """Convenience: load all data, run discovery, return a GapAnalyzer.

        Equivalent to ``discover_corpus_gaps()`` + ``GapAnalyzer(report, questions)``
        but loads questions only once and passes them through so the Step 0
        cross-reference works out of the box.

        Respects the ``RAG_RESEARCH_ENABLED`` / ``RAG_RESEARCH_CORPUS_DISCOVERY``
        config gates: when either is off, returns an analyzer with an empty
        (disabled) report and a summary note — no exception, just a no-op.
        """
        # Master switch: the autonomous research loop must be enabled.
        if not bool(cfg.research_enabled):
            logger.info("corpus discovery: RAG_RESEARCH_ENABLED is off — skipping autonomous discovery")
            return cls(
                DiscoveryReport(
                    total_provisions=0,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    summary={
                        "error": "RAG_RESEARCH_ENABLED is off; autonomous discovery skipped",
                        "by_gap_type": {},
                        "by_severity": {},
                        "by_domain": {},
                        "source_documents_with_gaps": 0,
                    },
                ),
            )
        # Sub-switch: corpus discovery from the gold registry.
        if not bool(cfg.research_corpus_discovery):
            logger.info("corpus discovery: RAG_RESEARCH_CORPUS_DISCOVERY is off — skipping registry scan")
            return cls(
                DiscoveryReport(
                    total_provisions=0,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    summary={
                        "error": "RAG_RESEARCH_CORPUS_DISCOVERY is off; registry scan skipped",
                        "by_gap_type": {},
                        "by_severity": {},
                        "by_domain": {},
                        "source_documents_with_gaps": 0,
                    },
                ),
            )

        questions = _load_benchmark_questions() or []
        report = discover_corpus_gaps(questions=questions)
        return cls(report, questions)

    # -- Reporting ---------------------------------------------------------- #

    def affected_questions(self) -> list[str]:
        """Distinct QIDs whose gold units include at least one missing provision."""
        return sorted({q for g in self.report.missing_provisions for q in g.question_refs})

    def missing_by_severity(self, severity: str) -> list[ProvisionGap]:
        """All missing provisions with the given severity label."""
        return [g for g in self.report.missing_provisions if g.severity == severity]

    def missing_by_gap_type(self, gap_type: str) -> list[ProvisionGap]:
        """All missing provisions with the given gap type."""
        return [g for g in self.report.missing_provisions if g.gap_type == gap_type]

    def question_impact(self) -> dict[str, list[str]]:
        """Map each missing provision_id -> sorted QIDs that reference it."""
        return {g.provision_id: sorted(g.question_refs) for g in self.report.missing_provisions}

    # -- Step 0 cross-reference --------------------------------------------- #

    def cross_reference_step0_targets(self, targets_path: str | Path | None = None) -> dict[str, Any]:
        """Cross-reference discovered gaps with Step 0's corpus-fill targets.

        Step 0 (``evaluation/step0_label_residual.py``) produces
        ``step0_corpus_fill_targets.json`` listing evidence-missing QIDs that
        need manual corpus fill.  This method checks whether our autonomous
        discovery already covers those gaps — if a Step 0 QID's primary gold
        units are all classified as missing here, the step-0 target is
        **covered by discovery** (discovery found the gap first, no manual
        action needed).  QIDs whose primary units are *not all* missing are
        **uncovered** (Step 0 found a subtler gap — retrieval failure, not a
        corpus-absent one — and should be retained for separate analysis).

        Args:
            targets_path: Path to ``step0_corpus_fill_targets.json``.  When
                ``None``, defaults to
                ``evaluation/out/ceiling_v5/step0_corpus_fill_targets.json``.
        """
        if not self._by_qid:
            return {
                "error": "no benchmark questions loaded; cannot cross-reference step-0 targets",
                "step0_targets_found": 0,
                "covered_by_discovery": 0,
                "uncovered_count": 0,
                "uncovered_qids": [],
            }

        if targets_path is None:
            targets_path = (
                Path(__file__).resolve().parents[3]
                / "evaluation"
                / "out"
                / "ceiling_v5"
                / "step0_corpus_fill_targets.json"
            )
        targets_path = Path(targets_path)
        if not targets_path.exists():
            return {
                "detail": "no step-0 targets file found",
                "step0_targets_found": 0,
                "covered_by_discovery": 0,
                "uncovered_count": 0,
                "uncovered_qids": [],
            }

        try:
            data = json.loads(targets_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"error": str(exc), "detail": "failed to read step-0 targets"}

        step0_qids: list[str] = list(data.get("qids", []))
        step0_n = data.get("n", len(step0_qids))
        missing_pid_set: set[str] = {g.provision_id for g in self.report.missing_provisions}

        covered: list[str] = []
        uncovered: list[str] = []
        for qid in step0_qids:
            q = self._by_qid.get(qid)
            if q is None:
                uncovered.append(qid)
                continue
            try:
                primary_ids = {str(u.provision_id) for u in q.primary_units()}
            except Exception:
                uncovered.append(qid)
                continue
            if not primary_ids:
                uncovered.append(qid)
                continue
            all_missing = primary_ids <= missing_pid_set
            (covered if all_missing else uncovered).append(qid)

        return {
            "step0_targets_found": step0_n,
            "covered_by_discovery": len(covered),
            "uncovered_count": len(uncovered),
            "uncovered_qids": uncovered,
            "covered_qids": covered,
            "detail": (
                "step-0 targets whose primary gold units are fully missing from the "
                "corpus (discovery found them first). Uncovered QIDs need Step 0's "
                "retrieval-gap analysis."
            ),
        }

    def to_report_dict(self) -> dict[str, Any]:
        """Serialized report + question-impact summary."""
        out = self.report.to_dict()
        out["affected_questions"] = self.affected_questions()
        out["affected_question_count"] = len(out["affected_questions"])
        out["missing_by_severity"] = {
            "high": len(self.missing_by_severity("high")),
            "medium": len(self.missing_by_severity("medium")),
            "low": len(self.missing_by_severity("low")),
        }
        out["question_impact"] = self.question_impact()
        out["step0_cross_reference"] = self.cross_reference_step0_targets()
        return out


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #


def discover_corpus_gaps(
    provisions: dict[str, dict[str, Any]] | None = None,
    payload_index: dict[str, dict] | None = None,
    sources: dict[str, Any] | None = None,
    questions: list[Any] | None = None,
    *,
    max_requests: int | None = None,
) -> DiscoveryReport:
    """Run a full corpus discovery pass and return a :class:`DiscoveryReport`.

    All four data sources are frozen benchmark artifacts — this function makes
    **no LLM calls** and **no network calls**.  It degrades gracefully when any
    source is unavailable.

    Args:
        provisions: Gold provision registry (``provision_id -> record``).
            When ``None``, loaded from ``benchmark/gold_provisions_v1.0.json``.
        payload_index: Cached Qdrant payload index.  When ``None`` and
            ``RAG_RESEARCH_USE_PAYLOAD_INDEX`` is on, the cache file is loaded;
            when unavailable, ``None`` falls through (registry-only mode).
        sources: Gold source-document registry.  When ``None``, loaded from
            ``benchmark/gold_sources_v1.0.json``.
        questions: Benchmark questions for impact analysis.  When ``None``,
            loaded from ``benchmark_v1.0.jsonl``.  Pass an empty list to skip
            question-impact analysis (faster, no question_refs populated).
        max_requests: Circuit-breaker cap on ingestion requests.  When
            ``None``, resolves from ``RAG_RESEARCH_MAX_INGESTION_REQUESTS``.

    Returns a :class:`DiscoveryReport` with:
    * ``missing_provisions`` — one :class:`ProvisionGap` per missing provision
      (severity + question_refs populated when questions are available).
    * ``source_gaps`` — gaps grouped by source document.
    * ``ingestion_requests`` — bounded list for the research orchestrator.
    """
    import_errors: list[str] = []

    # --- Load data (lazy, graceful) ---
    if provisions is None:
        try:
            provisions = _load_gold_provisions()
        except Exception as exc:
            import_errors.append(f"provisions: {exc}")
            provisions = {}
    if sources is None:
        try:
            sources = _load_gold_sources()
        except Exception as exc:
            import_errors.append(f"sources: {exc}")
            sources = {"collections": {}}

    # Payload index: auto-load from cache if not explicitly injected.
    # Caller can pass ``payload_index={}`` to force registry-only mode even
    # when the cache exists (useful for tests).
    if payload_index is None and bool(cfg.research_use_payload_index):
        payload_index = _load_payload_index()  # returns None if unavailable

    if questions is None:
        questions = _load_benchmark_questions() or []

    if import_errors:
        for e in import_errors:
            logger.warning("corpus discovery: data source unavailable (%s)", e)

    if not provisions:
        logger.warning("corpus discovery: no gold provisions loaded -- returning empty report")
        return DiscoveryReport(
            total_provisions=0,
            timestamp=datetime.now(timezone.utc).isoformat(),
            summary={
                "error": "no gold provisions loaded",
                "by_gap_type": {},
                "by_severity": {},
                "by_domain": {},
                "source_documents_with_gaps": 0,
            },
        )

    # --- Build coverage index (FamilyMap + payload_to_keys) when possible ---
    coverage_index: dict[tuple[str, str | None], list[str]] | None = None
    if payload_index is not None:
        family_map = _build_family_map()
        if family_map is not None:
            coverage_index = _build_coverage_index(payload_index, family_map)

    question_ref_map = _build_question_ref_map(questions, provisions) if questions else {}
    source_lookup = _build_source_lookup(sources)

    # --- Classify each provision ---
    classified: list[tuple[str, str, str, dict[str, Any]]] = []
    indexed_count = 0

    for pid, rec in provisions.items():
        gap_type, evidence = classify_provision_gap(rec, payload_index, coverage_index)

        if gap_type == "present":
            indexed_count += 1
            continue

        classified.append((pid, gap_type, evidence, rec))

    # --- Group by document ---
    source_gaps = group_provisions_by_document(classified, source_lookup)

    # --- Enrich severity (now we know per-doc question impact) ---
    for sg in source_gaps:
        doc_missing = sg.missing_count
        for idx, g in enumerate(sg.missing):
            refs = sorted(question_ref_map.get(g.provision_id, []))
            g.question_refs = refs
            g.severity = _severity_for(refs, doc_missing)

    # Also build ProvisionGap records with refs/severity for the flat list.
    all_gaps: list[ProvisionGap] = []
    for pid, gap_type, evidence, rec in classified:
        family = str(pid).split(":", 1)[0]
        refs = sorted(question_ref_map.get(pid, []))
        # Determine doc-level missing count for severity.
        doc_id = str(rec.get("document_id") or pid.split(":", 1)[0])
        doc_gap = next((sg for sg in source_gaps if sg.document_id == doc_id), None)
        doc_missing = doc_gap.missing_count if doc_gap else 0
        all_gaps.append(
            ProvisionGap(
                provision_id=pid,
                family=family,
                act=str(rec.get("act", "")),
                section=rec.get("section"),
                title=str(rec.get("title", "")),
                domain=str(rec.get("domain", "")),
                document_id=rec.get("document_id"),
                collection=rec.get("collection"),
                chunk_id=rec.get("chunk_id"),
                gap_type=gap_type,
                evidence=evidence,
                severity=_severity_for(refs, doc_missing),
                question_refs=refs,
            )
        )

    # --- Build ingestion requests (circuit-breaker bounded) ---
    if max_requests is None:
        max_requests = int(cfg.research_max_ingestion_requests)
    requests = _build_ingestion_requests(source_gaps, question_ref_map, max_requests)

    # --- Summary ---
    summary = _summary(all_gaps, source_gaps)
    summary["modes"] = {
        "payload_index_used": payload_index is not None,
        "coverage_index_used": coverage_index is not None,
        "question_impact_analyzed": bool(question_ref_map),
        "provisions_total": len(provisions),
        "provisions_indexed": indexed_count,
        "provisions_missing": len(all_gaps),
    }

    report = DiscoveryReport(
        total_provisions=len(provisions),
        indexed_provisions=indexed_count,
        missing_provisions=all_gaps,
        source_gaps=source_gaps,
        ingestion_requests=requests,
        payload_index_size=len(payload_index or {}),
        timestamp=datetime.now(timezone.utc).isoformat(),
        summary=summary,
    )

    logger.info(
        "corpus discovery complete: %d total provisions, %d missing (%s), %d ingestion requests",
        report.total_provisions,
        report.missing_count,
        summary["by_gap_type"],
        len(requests),
    )

    return report

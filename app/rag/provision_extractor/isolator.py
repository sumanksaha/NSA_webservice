"""Provision isolation — stitch chunks, accept boundaries, emit records.

Takes a document's chunks in ``chunk_index`` order, joins their text, runs
candidate generation + disambiguation, and cuts the accepted boundaries into
:class:`~app.rag.provision_extractor.models.ProvisionRecord` spans.

``chunk_id`` values are **never mutated**: every record records the underlying
chunk ids in ``source_chunk_ids`` so retrieval caches, gold source-chunk
mappings, and the hash-chained audit trail stay valid (ADR-0009 §2.1).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.rag.provision_extractor.candidates import BoundaryCandidate, generate_candidates
from app.rag.provision_extractor.disambiguator import Disambiguator
from app.rag.provision_extractor.features import extract_features
from app.rag.provision_extractor.models import DeonticModality, ProvisionRecord
from app.rag.provision_extractor.registry import family_token, provision_id

_SUBSECTION_RE = re.compile(r"\s*((?:\([^()]*\))+)")
_TITLE_PREFIX_RE = re.compile(r"^\s*(?:Section|Sec\.|§)?\s*\d{1,4}(?:\.\d{1,2})*\s*\.?\s*")
_MARKER_RE = re.compile(r"^(?:\([^()]*\)\s*)+")
_CROSSREF_RE = re.compile(r"\bSection\s+(\d{1,4})", re.IGNORECASE)

_MODALITY_PATTERNS: tuple[tuple[DeonticModality, re.Pattern[str]], ...] = (
    (DeonticModality.PENALTY, re.compile(r"\b(?:liable to (?:a )?penalty|punishable|shall be punish|imprison|fine)\b")),
    (DeonticModality.PROHIBITION, re.compile(r"\b(?:no person shall|shall not|prohibit\w*)\b")),
    (DeonticModality.EXEMPTION, re.compile(r"\b(?:provided that|exempt\w*|nothing .{0,40}shall apply)\b")),
    (DeonticModality.DEFINITION, re.compile(r"\b(?:means and includes|means|definition)\b")),
    (DeonticModality.POWER, re.compile(r"\bmay\b.{0,60}\b(?:seize|grant|issue|order|suspend|inspect)\b")),
    (DeonticModality.PROCEDURE, re.compile(r"\b(?:procedure|shall be sent|application shall)\b")),
    (DeonticModality.OBLIGATION, re.compile(r"\b(?:shall|must|required to)\b")),
)


def _chunk_field(chunk: Any, key: str, default: Any = None) -> Any:
    """Read *key* from a Chunk-like object or a payload dict."""
    if isinstance(chunk, dict):
        return chunk.get(key, default)
    return getattr(chunk, key, default)


def _set_chunk_field(chunk: Any, key: str, value: Any) -> None:
    """Set *key* on a Chunk-like object or payload dict."""
    if isinstance(chunk, dict):
        chunk[key] = value
    else:
        setattr(chunk, key, value)


def attach_provision_spans(chunks: list[Any], records: list[ProvisionRecord]) -> None:
    """Annotate *chunks* in place with the provisions overlapping each span.

    Sets ``provision_spans`` / ``provision_confidence`` / ``provision_modality``
    on every chunk that overlaps a record's ``char_span``.  ``chunk_id`` and the
    chunk text are never modified — this is an additive enrichment step.
    """
    if not chunks or not records:
        return
    ordered = sorted(chunks, key=lambda c: int(_chunk_field(c, "chunk_index", 0) or 0))
    parts = [str(_chunk_field(c, "chunk_text", "") or "") for c in ordered]
    starts: list[int] = []
    position = 0
    for part in parts:
        starts.append(position)
        position += len(part) + 1

    per_chunk: dict[int, list[ProvisionRecord]] = {}
    for record in records:
        span = record.char_span
        if not span:
            continue
        span_start, span_end = span
        for index, start in enumerate(starts):
            if start < span_end and start + len(parts[index]) > span_start:
                per_chunk.setdefault(index, []).append(record)

    for index, chunk_records in per_chunk.items():
        modalities = Counter(str(record.modality) for record in chunk_records)
        _set_chunk_field(
            ordered[index],
            "provision_spans",
            [
                {
                    "provision_id": record.provision_id,
                    "section": record.section,
                    "subsection": list(record.subsection),
                }
                for record in chunk_records
            ],
        )
        _set_chunk_field(
            ordered[index],
            "provision_confidence",
            round(max(float(record.confidence) for record in chunk_records), 6),
        )
        _set_chunk_field(ordered[index], "provision_modality", modalities.most_common(1)[0][0])


def classify_modality(text: str) -> DeonticModality:
    """Deterministic Tier-1 deontic modality guess (order-sensitive).

    This is a *rule-based* first pass — the ML modality classifier is a later
    tier; an unmatched provision stays :attr:`DeonticModality.UNKNOWN` rather
    than being guessed.
    """
    lowered = (text or "").lower()
    for modality, pattern in _MODALITY_PATTERNS:
        if pattern.search(lowered):
            return modality
    return DeonticModality.UNKNOWN


def _extract_title(span_text: str) -> str:
    """Margin title of a provision span (text after the number, pre-em-dash)."""
    match = _TITLE_PREFIX_RE.match(span_text)
    rest = span_text[match.end() :] if match else span_text
    rest = _MARKER_RE.sub("", rest)
    rest = re.split(r"[—\n]", rest, maxsplit=1)[0]
    return rest.strip(" .:-")[:200]


def _extract_subsection(text: str, match_end: int) -> list[str]:
    """Parenthetical subsection chain immediately after the matched number."""
    match = _SUBSECTION_RE.match(text[match_end:])
    if not match:
        return []
    return [group.strip() for group in re.findall(r"\(([^()]*)\)", match.group(1)) if group.strip()]


def _cross_references(span_text: str) -> list[str]:
    seen: list[str] = []
    for number in _CROSSREF_RE.findall(span_text):
        ref = f"Section {number}"
        if ref not in seen:
            seen.append(ref)
    return seen


def isolate_chunks(
    chunks: list[Any],
    *,
    act_name: str = "",
    document_id: str = "",
    document_title: str = "",
    disambiguator: Disambiguator | None = None,
) -> list[ProvisionRecord]:
    """Extract provision records from one document's ordered chunks.

    Args:
        chunks: Chunk-like objects or payload dicts (``chunk_id``,
            ``chunk_text``, ``chunk_index``, optional ``sections_covered`` and
            ``confidence``).
        act_name: Owning Act name (drives the fail-closed range gate).
        document_id: Source document id (provenance only).
        document_title: Document title (family resolution fallback).
        disambiguator: Injected for tests; built from config when omitted.

    Returns:
        Provision records in document order; ``[]`` when no boundary clears
        the confidence threshold.
    """
    ordered = sorted(chunks, key=lambda c: int(_chunk_field(c, "chunk_index", 0) or 0))
    parts = [str(_chunk_field(c, "chunk_text", "") or "") for c in ordered]
    if not any(part.strip() for part in parts):
        return []

    text = "\n".join(parts)
    starts: list[int] = []
    position = 0
    for part in parts:
        starts.append(position)
        position += len(part) + 1

    engine = disambiguator or Disambiguator()
    candidates = generate_candidates(text)

    accepted: list[tuple[BoundaryCandidate, Any]] = []
    prev_accepted: int | None = None
    seen_keys: set[tuple[str, tuple[str, ...]]] = set()
    emitted_keys: set[tuple[str, tuple[str, ...]]] = set()

    for candidate in candidates:
        chunk = _overlapping_chunk(ordered, starts, parts, candidate.char_offset)
        covered = _chunk_field(chunk, "sections_covered", []) if chunk is not None else []
        # Identity key = normalized number + subsection chain.  Re-mentions of
        # the same key are cross-references in running text, never separate
        # provisions (E2); the first occurrence is weighted positively, later
        # ones are suppressed from the emitted records entirely.
        key = (_base_section(candidate), tuple(_extract_subsection(text, candidate.match_end)))
        features = extract_features(
            text,
            candidate,
            act_name=act_name,
            prev_accepted=prev_accepted,
            first_occurrence=key not in seen_keys,
            sections_covered=set(covered or []),
            engine_confidence=float(_chunk_field(chunk, "confidence", 0.0) or 0.0),
        )
        seen_keys.add(key)
        decision = engine.predict(features)
        if not decision.accepted or key in emitted_keys:
            continue
        emitted_keys.add(key)
        accepted.append((candidate, decision))
        base = _base_int(candidate.raw_number)
        if base:
            prev_accepted = base

    if not accepted:
        return []

    family = family_token(document_title, act_name) if (document_title or act_name) else "unknown"
    records: list[ProvisionRecord] = []
    for index, (candidate, decision) in enumerate(accepted):
        span_start = candidate.char_offset
        span_end = accepted[index + 1][0].char_offset if index + 1 < len(accepted) else len(text)
        span_text = text[span_start:span_end].strip()
        subsection = _extract_subsection(text, candidate.match_end)
        base_section = _base_section(candidate)
        records.append(
            ProvisionRecord(
                provision_id=provision_id(family, base_section, subsection),
                act_name=act_name,
                family_id=family,
                section=base_section,
                subsection=subsection,
                title=_extract_title(span_text),
                text=span_text,
                modality=classify_modality(span_text),
                cross_references=_cross_references(span_text),
                is_proviso="provided that" in span_text.lower(),
                source_chunk_ids=_source_chunk_ids(starts, parts, ordered, span_start, span_end),
                char_span=(span_start, span_end),
                confidence=round(decision.probability, 6),
                extraction_tier=decision.tier,
                source=candidate.source_pattern,
            )
        )
    return records


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _base_int(raw_number: str) -> int:
    match = re.match(r"\d{1,4}", raw_number or "")
    return int(match.group(0)) if match else 0


def _base_section(candidate: BoundaryCandidate) -> str:
    """Canonical section string: leading zeros stripped, dotted form preserved."""
    if candidate.grammar_type == "dotted":
        return candidate.raw_number
    return str(_base_int(candidate.raw_number) or candidate.raw_number)


def _overlapping_chunk(ordered: list[Any], starts: list[int], parts: list[str], offset: int) -> Any:
    """The chunk whose span contains *offset*, or ``None``."""
    for index, start in enumerate(starts):
        if start <= offset < start + len(parts[index]) + 1:
            return ordered[index]
    return None


def _source_chunk_ids(
    starts: list[int],
    parts: list[str],
    ordered: list[Any],
    span_start: int,
    span_end: int,
) -> list[str]:
    """Chunk ids overlapping ``[span_start, span_end)``."""
    ids: list[str] = []
    for index, start in enumerate(starts):
        end = start + len(parts[index])
        if start < span_end and end > span_start:
            chunk_id = _chunk_field(ordered[index], "chunk_id")
            if chunk_id:
                ids.append(str(chunk_id))
    return ids

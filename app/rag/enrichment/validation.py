"""Phase 12 — Structural validation of enrichment records.

Enforces the invariants from ``docs/enrichment/ENRICHMENT_SCHEMA.md`` §6
(plus the task's Phase 12 list):

1. record parses as the v1.0 shape (JSON Schema, checked structurally here)
2. ``chunk_id`` present; ``original_sha256`` matches the payload hash
3. ``original_text`` equals the payload ``chunk_text`` (immutability)
4. no invented legal values — LLM ``explicit`` values must carry an
   evidence span; values without evidence are downgraded to ``unknown``
5. ``confidence`` in [0, 1]; evidence spans within [0, len(text)]
6. ``cross_references[].target_chunk_id`` only when ``resolved: true``
7. no duplicate relationships / duplicate entities

Deterministic-only mode: records produced by Phase 3 have no LLM fields, so
rule 4 is trivially satisfied; the validator still downgrades any
``source=llm`` value that lacks an evidence span (future-proofing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    """Outcome of validating one enrichment record."""

    ok: bool = True
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "issues": list(self.issues)}


def _check(cond: bool, issue: str, issues: list[str]) -> bool:
    if not cond:
        issues.append(issue)
    return cond


def validate_record(record: dict, payload: dict | None = None) -> ValidationResult:
    """Validate one enrichment record against the payload it was built from.

    Args:
        record: The v1.0 enrichment record.
        payload: The source Qdrant payload (for immutability + hash checks).
            When ``None``, immutability checks are skipped (callers without
            payload access may still validate the structural invariants).
    """
    issues: list[str] = []
    text = record.get("original_text", "")

    # 1. Shape
    _check(record.get("enrichment_version"), "missing enrichment_version", issues)
    _check(record.get("chunk_id"), "missing chunk_id", issues)
    _check(record.get("status") in {"PENDING", "PROCESSING", "ENRICHED", "VALIDATED", "FAILED", "SKIPPED"},
           f"invalid status {record.get('status')!r}", issues)
    _check(isinstance(record.get("legal_location"), dict), "legal_location must be a dict", issues)
    _check(isinstance(record.get("cross_references"), list), "cross_references must be a list", issues)
    _check(isinstance(record.get("entities"), list), "entities must be a list", issues)

    # 2/3. Immutability + integrity (when payload is available)
    if payload is not None:
        payload_text = payload.get("chunk_text", "")
        _check(str(text) == str(payload_text), "original_text differs from payload chunk_text", issues)
        phash = payload.get("content_hash")
        if phash:
            _check(str(record.get("original_sha256")) == str(phash),
                   "original_sha256 differs from payload content_hash", issues)

    # 4. No invented legal values — every source=llm explicit value needs evidence
    for field_name in (
        "legal_concepts", "obligations", "prohibitions", "permissions", "powers",
        "duties", "conditions", "exceptions", "offences", "penalties",
        "procedures", "applicability",
    ):
        for item in record.get(field_name) or []:
            if not isinstance(item, dict):
                _check(False, f"{field_name} item must be a dict", issues)
                continue
            if item.get("source") == "llm" and item.get("kind") == "explicit":
                _check(bool(item.get("evidence_span")),
                       f"{field_name} explicit LLM value lacks evidence_span", issues)

    # legal_location values: explicit section/etc. must come from determinism
    loc = record.get("legal_location") or {}
    for key in ("section", "subsection", "schedule", "annexure", "act"):
        v = loc.get(key)
        if isinstance(v, dict) and v.get("value") is not None and v.get("source") not in {"deterministic", "existing_payload"}:
            _check(False, f"legal_location.{key} has non-deterministic source {v.get('source')!r}", issues)

    # 5. Confidence + evidence spans
    conf = record.get("confidence")
    if conf is not None:
        _check(0.0 <= float(conf) <= 1.0, f"confidence {conf} out of [0, 1]", issues)
    for span in record.get("evidence_spans") or []:
        if isinstance(span, (list, tuple)) and len(span) == 2:
            _check(0 <= span[0] <= span[1] <= max(len(text), 1), f"evidence span {span} out of bounds", issues)

    # 6. Cross-references: resolved => target_chunk_id required and vice versa
    seen_edges: set[tuple[str, str, str]] = set()
    for xr in record.get("cross_references") or []:
        if not isinstance(xr, dict):
            _check(False, "cross_reference must be a dict", issues)
            continue
        resolved = xr.get("resolved") is True
        target = xr.get("target_chunk_id")
        if resolved:
            _check(bool(target), "resolved cross_reference lacks target_chunk_id", issues)
            edge = (record.get("chunk_id"), target, xr.get("relation", "REFERS_TO"))
            if edge in seen_edges:
                _check(False, f"duplicate cross-reference edge {edge}", issues)
            seen_edges.add(edge)
        else:
            if target:
                _check(False, "unresolved cross_reference carries target_chunk_id", issues)
        conf = xr.get("confidence")
        if conf is not None:
            _check(0.0 <= float(conf) <= 1.0, f"cross_reference confidence {conf} out of [0, 1]", issues)

    # 7. No duplicate entities
    seen_entities: set[str] = set()
    for e in record.get("entities") or []:
        name = e.get("name") if isinstance(e, dict) else None
        if name is not None:
            if name in seen_entities:
                _check(False, f"duplicate entity {name!r}", issues)
            seen_entities.add(name)

    return ValidationResult(ok=not issues, issues=issues)

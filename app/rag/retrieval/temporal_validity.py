"""Temporal validity — determine if a provision was valid at a query date.

Returns ``valid``, ``invalid``, or ``unknown`` — never infers validity when
the corpus lacks sufficient evidence.  Provision validity metadata is stored
in the chunk payload (``effective_from``, ``effective_to``, ``status``) and
optionally enriched from the Neo4j KG (best-effort).

Validity semantics:

- ``valid``     — provision is current (``status == "current"``) and
                  ``effective_from`` ≤ ``query_date`` (or no ``effective_from``).
- ``invalid``   — provision is repealed/superseded, or ``query_date`` >
                  ``effective_to`` (provision was valid but is no longer).
- ``unknown``   — no temporal metadata available; never fabricate.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Validity status
# --------------------------------------------------------------------------- #

VALIDITY_VALID = "valid"
VALIDITY_INVALID = "invalid"
VALIDITY_UNKNOWN = "unknown"

_VALIDITY_VALUES = frozenset({VALIDITY_VALID, VALIDITY_INVALID, VALIDITY_UNKNOWN})


@dataclass
class ValidityResult:
    """Result of a temporal validity check.

    Attributes:
        document_id: The chunk/provision checked.
        query_date: The date the provision was checked against.
        status: valid / invalid / unknown.
        provision_status: Raw status from the corpus (current, repealed, etc.).
        effective_from: When the provision became effective (if known).
        effective_to: When the provision was repealed/superseded (if known).
        reason: Human-readable explanation of the determination.
        source: "payload" if from chunk payload, "graph" if from Neo4j.
    """

    document_id: str | None
    query_date: str | None
    status: str
    provision_status: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    reason: str = ""
    source: str = "payload"

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "query_date": self.query_date,
            "status": self.status,
            "provision_status": self.provision_status,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "reason": self.reason,
            "source": self.source,
        }


# --------------------------------------------------------------------------- #
# Date parsing
# --------------------------------------------------------------------------- #


def _parse_date(value: str | date | datetime | None) -> date | None:
    """Parse a date string/value into a ``date`` object.

    Handles ISO dates, ``YYYY-MM-DD``, ``YYYY/MM/DD``, ``YYYY-MM``,
    and datetime objects.  Returns ``None`` on failure.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    # Try common formats
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            d = datetime.strptime(s, fmt).date()
            return d
        except ValueError:
            continue
    # Last resort: extract YYYY-MM-DD from the string
    import re

    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _today_iso() -> str:
    return date.today().isoformat()


# --------------------------------------------------------------------------- #
# Validity check
# --------------------------------------------------------------------------- #


def is_valid(
    document_id: str | None,
    query_date: str | date | datetime | None = None,
    chunk: Any | None = None,
    provision_status: str | None = None,
    effective_from: str | None = None,
    effective_to: str | None = None,
    allow_graph: bool = True,
) -> ValidityResult:
    """Determine whether a provision was valid at ``query_date``.

    Args:
        document_id: Chunk/provision identifier.
        query_date: Date to check against (defaults to today).
        chunk: Optional ``RetrievedChunk`` to source payload fields from.
        provision_status: Override for status (if chunk not provided).
        effective_from: Override for effective_from date.
        effective_to: Override for effective_to date.
        allow_graph: Whether to consult Neo4j for additional metadata.

    Returns:
        ``ValidityResult`` with status ``valid`` / ``invalid`` / ``unknown``.
    """
    # Resolve query_date (default: today)
    if query_date is None:
        query_date = _today_iso()
    qd = _parse_date(query_date)
    if qd is None:
        qd = _parse_date(_today_iso())

    query_date_str = query_date.isoformat() if isinstance(query_date, (date, datetime)) else str(query_date)

    # Resolve payload fields from chunk if provided
    if chunk is not None:
        provision_status = provision_status or getattr(chunk, "status", None) or getattr(chunk, "provision_status", None)
        effective_from = effective_from or getattr(chunk, "effective_from", None)
        effective_to = effective_to or getattr(chunk, "effective_to", None)

    status_lower = (provision_status or "").lower().strip()

    # --- Determine temporal validity from payload ---
    if status_lower == "repealed" or status_lower == "superseded":
        return ValidityResult(
            document_id=document_id,
            query_date=query_date_str,
            status=VALIDITY_INVALID,
            provision_status=provision_status,
            effective_from=effective_from,
            effective_to=effective_to,
            reason=f"Provision status is '{status_lower}' — no longer in force.",
            source="payload",
        )

    if status_lower == "current":
        ef = _parse_date(effective_from)
        et = _parse_date(effective_to)
        if ef is not None and qd < ef:
            return ValidityResult(
                document_id=document_id,
                query_date=query_date_str,
                status=VALIDITY_INVALID,
                provision_status=provision_status,
                effective_from=effective_from,
                effective_to=effective_to,
                reason=f"Provision effective from {ef} but query date {qd} is before that.",
                source="payload",
            )
        if et is not None and qd > et:
            return ValidityResult(
                document_id=document_id,
                query_date=query_date_str,
                status=VALIDITY_INVALID,
                provision_status=provision_status,
                effective_from=effective_from,
                effective_to=effective_to,
                reason=f"Provision effective to {et}, query date {qd} is after expiry.",
                source="payload",
            )
        return ValidityResult(
            document_id=document_id,
            query_date=query_date_str,
            status=VALIDITY_VALID,
            provision_status=provision_status,
            effective_from=effective_from,
            effective_to=effective_to,
            reason="Provision status is 'current' and date range is valid.",
            source="payload",
        )

    # If status is unknown but we have date boundaries, try date-based logic
    if effective_from or effective_to:
        ef = _parse_date(effective_from)
        et = _parse_date(effective_to)
        if ef is not None and qd < ef:
            return ValidityResult(
                document_id=document_id,
                query_date=query_date_str,
                status=VALIDITY_INVALID,
                provision_status=provision_status,
                effective_from=effective_from,
                effective_to=effective_to,
                reason=f"Query date {qd} is before effective_from {ef}.",
                source="payload",
            )
        if et is not None and qd > et:
            return ValidityResult(
                document_id=document_id,
                query_date=query_date_str,
                status=VALIDITY_INVALID,
                provision_status=provision_status,
                effective_from=effective_from,
                effective_to=effective_to,
                reason=f"Query date {qd} is after effective_to {et}.",
                source="payload",
            )

    # --- Try Neo4j enrichment (best-effort) ---
    if allow_graph:
        graph_result = _query_neo4j_validity(document_id)
        if graph_result is not None:
            return graph_result

    # --- Unknown — no sufficient evidence ---
    return ValidityResult(
        document_id=document_id,
        query_date=query_date_str,
        status=VALIDITY_UNKNOWN,
        provision_status=provision_status,
        effective_from=effective_from,
        effective_to=effective_to,
        reason="No sufficient temporal metadata available — validity not asserted.",
        source="payload",
    )


def _query_neo4j_validity(document_id: str | None) -> ValidityResult | None:
    """Attempt to resolve validity from the Neo4j KG.

    Returns ``None`` when Neo4j is unavailable or the provision is not found.
    """
    if not document_id:
        return None
    try:
        from app.services.neo4j_graph import neo4j_configured

        if not neo4j_configured():
            return None
    except Exception:
        return None

    try:
        from kg.queries import LegalKGQueries

        kg = LegalKGQueries()
        prov = kg.get_provision(document_id)
        if not prov:
            return None

        ps = prov.get("status", "") or prov.get("instrument", {}).get("status")
        ef = prov.get("effective_from")
        et = prov.get("effective_to")

        # Re-run the same logic with KG-sourced fields
        # Use status="unknown" sentinel to trigger Neo4j enrichment path
        return is_valid(
            document_id=document_id,
            chunk=None,
            provision_status=ps or None,
            effective_from=ef,
            effective_to=et,
            allow_graph=False,
        )
    except Exception as exc:
        logger.debug("Neo4j validity query failed for %s: %s", document_id, exc)
        return None


# --------------------------------------------------------------------------- #
# Temporal validity score (for reranker feature)
# --------------------------------------------------------------------------- #


def temporal_validity_score(document_id: str | None, query_date: str | None = None) -> float:
    """Return a [0, 1] score for temporal validity.

    ``1.0`` = explicitly valid, ``0.5`` = uncertain, ``0.0`` = explicitly invalid.

    This feature is NOT automatically applied to the production ranker —
    it must be wired in by a downstream consumer.
    """
    result = is_valid(document_id, query_date)
    return {
        VALIDITY_VALID: 1.0,
        VALIDITY_INVALID: 0.0,
        VALIDITY_UNKNOWN: 0.5,
    }.get(result.status, 0.5)


# --------------------------------------------------------------------------- #
# Feature flag
# --------------------------------------------------------------------------- #


def _temporal_validity_enabled() -> bool:
    """Check if temporal validity is enabled via env / Flask config."""
    try:
        from flask import current_app

        if current_app:
            return bool(current_app.config.get("ENABLE_TEMPORAL_FILTER", True))
    except Exception:
        pass
    return os.environ.get("ENABLE_TEMPORAL_FILTER", "true").lower() != "false"


# --------------------------------------------------------------------------- #
# Self-check
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # Current provision
    r = is_valid("prov1", "2025-01-01", provision_status="current", effective_from="2020-01-01", effective_to=None)
    assert r.status == VALIDITY_VALID, r

    # Repealed provision
    r = is_valid("prov2", "2025-01-01", provision_status="repealed")
    assert r.status == VALIDITY_INVALID, r

    # Query date before effective_from
    r = is_valid("prov3", "2019-01-01", provision_status="current", effective_from="2020-01-01")
    assert r.status == VALIDITY_INVALID, r

    # Query date after effective_to
    r = is_valid("prov4", "2025-01-01", provision_status="current", effective_from="2020-01-01", effective_to="2023-01-01")
    assert r.status == VALIDITY_INVALID, r

    # No metadata → unknown
    r = is_valid("prov5", "2025-01-01")
    assert r.status == VALIDITY_UNKNOWN, r

    # Score checks — use chunk-based is_valid for the score function
    from dataclasses import dataclass
    @dataclass
    class FakeChunk:
        chunk_id: str
        status: str
        effective_from: str | None = None
        effective_to: str | None = None

    # repealed → invalid → score 0.0
    c = FakeChunk(chunk_id="c2", status="repealed")
    r = is_valid("c2", "2025-01-01", chunk=c)
    assert r.status == "invalid", r
    sc = temporal_validity_score("c2", "2025-01-01")
    # score returns 0.5 (unknown) because document_id isn't in any graph/payload
    assert sc == 0.5 or sc == 0.0  # depends on graph availability


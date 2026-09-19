"""FSO advisory nodes — thin adapters over the deterministic selector (ADR-0003).

Two invocations of the same :class:`DeterministicActSelector`:

* ``fso_advisory_hint_node`` (pre-generation): candidate Act from raw
  retrieval sections; stored as ``fso_hint`` for generate/synthesize
  context only — never surfaced to the client.
* ``fso_advisory_node`` (post-verification): authoritative Act from
  *verified* sections (citations filtered to the retrieved set);
  fail-closed on missing grounding. Surfaced via ``finalize_node``.

Both are pure and synchronous (no LLM, no network); sub-millisecond.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.rag.advisor.penalties import FSSAI_PENALTY_SCHEDULE
from app.rag.advisor.selector import DeterministicActSelector
from app.rag.agent.nodes.common import _ms

logger = logging.getLogger(__name__)

__all__ = [
    "extract_sections",
    "fso_advisory_hint_node",
    "fso_advisory_node",
]

#: Match "Section 51", "Sec. 55", "§63", sub-section "31(2)(a)" (→ "31").
_SECTION_RE = re.compile(r"(?:section|sec\.?|sub-section|subsection|§)\s*(\d{1,4})", re.IGNORECASE)

_selector = DeterministicActSelector()


def _section_from_value(value: Any) -> str | None:
    """Normalize one section-ish value to bare digits, else None."""
    if value is None:
        return None
    if isinstance(value, dict):
        # Nested legal-identity shape (e.g. {"section": "55", ...}).
        value = value.get("section", value.get("section_number"))
        if value is None:
            return None
    m = re.search(r"\d{1,4}", str(value))
    if not m:
        return None
    return re.match(r"\d{1,4}", m.group(0)).group(0)  # type: ignore[union-attr]


def _chunk_section(chunk: dict[str, Any]) -> str | None:
    """Authoritative § for one chunk dict: flat field → nested payload → identity."""
    direct = _section_from_value(chunk.get("section_number"))
    if direct:
        return direct
    payload = chunk.get("payload")
    if isinstance(payload, dict):
        nested = _section_from_value(payload.get("section_number"))
        if nested:
            return nested
    identity = chunk.get("legal_identity")
    if identity is not None:
        nested_identity = _section_from_value(identity)
        if nested_identity:
            return nested_identity
    return None


def extract_sections(
    chunks: list[Any] | None,
    response: dict[str, Any] | None = None,
    *,
    verified_only: bool = False,
) -> list[str]:
    """Derive grounded § sections from chunk dicts (+ optionally citations).

    Order per chunk: ``section_number`` field → regex over ``text``.
    Citations (``response["citations"]``) are included only when
    ``verified_only`` is False, or when the cited ``chunk_id`` is in the
    retrieved set (fail-closed: hallucinated citations contribute nothing).
    Unknown sections are kept here (telemetry); the selector filters them
    against the penalty schedule. Order-preserving dedupe.
    """
    found: list[str] = []
    chunks = [c for c in (chunks or []) if isinstance(c, dict)]
    retrieved_ids = {str(c.get("chunk_id")) for c in chunks if c.get("chunk_id")}

    for chunk in chunks:
        direct = _chunk_section(chunk)
        if direct:
            found.append(direct)
            continue
        text = str(chunk.get("text") or chunk.get("chunk_text") or "")
        for m in _SECTION_RE.finditer(text):
            found.append(m.group(1))

    citations = (response or {}).get("citations") or []
    for cit in citations:
        if not isinstance(cit, dict):
            continue
        if verified_only and str(cit.get("chunk_id") or "") not in retrieved_ids:
            continue
        sec = _section_from_value(cit.get("section") or cit.get("section_number"))
        if sec:
            found.append(sec)
        else:
            snippet = str(cit.get("snippet") or "")
            for m in _SECTION_RE.finditer(snippet):
                found.append(m.group(1))

    seen: set[str] = set()
    deduped: list[str] = []
    for sec in found:
        if sec not in seen:
            seen.add(sec)
            deduped.append(sec)
    return deduped


def _gather_chunks(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Linear chunks + all DAG-path evidence chunks (mirrors citation_quality)."""
    chunks = list(state.get("chunks") or [])
    for ev_chunks in (state.get("evidence") or {}).values():
        chunks.extend(ev_chunks or [])
    return [c for c in chunks if isinstance(c, dict)]


def _advisory_flags(state: dict[str, Any]) -> dict[str, bool]:
    return {
        "has_prior": bool(state.get("is_repeat_offender", False)),
        "has_lab": bool(state.get("has_lab_report", False)),
    }


def fso_advisory_hint_node(state: dict[str, Any]) -> dict[str, Any]:
    """Pre-generation candidate Act (internal hint, never client-surfaced)."""
    start = time.monotonic()
    flags = _advisory_flags(state)
    extracted = extract_sections(_gather_chunks(state), verified_only=False)
    result = _selector.select_act(
        retrieved_sections=extracted,
        has_prior_violations=flags["has_prior"],
        lab_report_available=flags["has_lab"],
    )
    return {
        "extracted_sections": extracted,
        "fso_hint": result["fso_act"],
        "fso_advisory_enabled": True,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "fso_advisory_hint",
                "latency_ms": _ms(start),
                "detail": {
                    "sections": extracted,
                    "hint": (result["fso_act"] or {}).get("escalation_level"),
                    "abstained": result["fso_act"] is None,
                },
            },
        ],
    }


def fso_advisory_node(state: dict[str, Any]) -> dict[str, Any]:
    """Post-verification authoritative Act (fail-closed, client-surfaced)."""
    start = time.monotonic()
    flags = _advisory_flags(state)
    chunks = _gather_chunks(state)
    response = state.get("response") if isinstance(state.get("response"), dict) else None
    extracted = extract_sections(chunks, response, verified_only=True)
    # Fail-closed on unverified grounding: when the citation gate failed and
    # no retrieved chunk itself carries a schedulable section, the cited
    # sections are untrustworthy — abstain rather than acting on them.
    if not state.get("citation_quality_ok", True):
        chunk_sections = extract_sections(chunks, verified_only=False)
        if not [s for s in chunk_sections if s in FSSAI_PENALTY_SCHEDULE]:
            return {
                "extracted_sections": chunk_sections,
                "fso_act": None,
                "advisory_abstain_reason": "insufficient_statutory_grounding",
                "fso_advisory_enabled": True,
                "audit_trail": [
                    *(state.get("audit_trail") or []),
                    {
                        "node": "fso_advisory",
                        "latency_ms": _ms(start),
                        "detail": {"sections": chunk_sections, "abstained": True, "reason": "citation_gate"},
                    },
                ],
            }
    result = _selector.select_act(
        retrieved_sections=extracted,
        has_prior_violations=flags["has_prior"],
        lab_report_available=flags["has_lab"],
    )
    return {
        "extracted_sections": extracted,
        "fso_act": result["fso_act"],
        "advisory_abstain_reason": result["abstain_reason"],
        "fso_advisory_enabled": True,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "fso_advisory",
                "latency_ms": _ms(start),
                "detail": {
                    "sections": extracted,
                    "act": (result["fso_act"] or {}).get("escalation_level"),
                    "abstained": result["fso_act"] is None,
                },
            },
        ],
    }

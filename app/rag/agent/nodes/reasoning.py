"""Structured-reasoning graph node (roadmap Phase 3, §32.1).

``structured_reasoner_node`` runs between retrieval and generation on the
flag-gated linear path: it renders the retrieved chunks as evidence
context, builds the :class:`StructuredLegalArgument` IR, and records the
legal-unit evidence the auditor reads.  On a revision pass (a FAIL audit
is already on state) it appends the defect corrections to the context
and bumps ``revision_count`` — ``route_after_audit`` caps the loop at
``max_revisions``.

``StructuredReasoner`` is a module-global seam: tests monkeypatch
``app.rag.agent.nodes.reasoning.StructuredReasoner``; production uses
the real LLM-backed class.
"""

from __future__ import annotations

import logging
from typing import Any

from app.rag.generation.structured_reasoner import StructuredLegalArgument, StructuredReasoner

logger = logging.getLogger(__name__)


def _evidence_context(chunks: list[Any]) -> tuple[str, list[dict[str, Any]]]:
    """Render ``[n] text`` context lines + legal-unit evidence entries.

    Each entry carries the chunk id, its canonical legal-unit id (so the
    auditor accepts canonical ``ACT::SEC`` citations), and the text.
    """
    from app.rag.retrieval.evidence_selector import canonical_unit_id

    lines: list[str] = []
    units: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks if isinstance(chunks, list) else [], 1):
        if isinstance(chunk, dict):
            chunk_id = str(chunk.get("chunk_id", f"c{i}"))
            text = str(chunk.get("text", "") or "")
        else:
            chunk_id = str(getattr(chunk, "chunk_id", f"c{i}"))
            text = str(getattr(chunk, "text", "") or "")
        lines.append(f"[{i}] ({chunk_id}) {text}")
        try:
            unit = canonical_unit_id(chunk)
        except Exception:
            unit = chunk_id
        units.append({"id": chunk_id, "unit": unit, "text": text})
    return "\n".join(lines), units


def structured_reasoner_node(state: dict[str, Any]) -> dict[str, Any]:
    """Build the structured argument IR from retrieved chunks.

    Reads ``query`` + ``chunks``; writes ``structured_argument`` (dict),
    ``legal_unit_evidence`` and ``revision_count``.  Never raises on
    caller-controlled input — the reasoner itself falls back to an empty
    skeleton when the LLM output is unusable.
    """
    chunks = state.get("chunks") or []
    query = str(state.get("query", "") or "")
    context, units = _evidence_context(chunks)

    audit = state.get("audit_result") or {}
    revision_count = int(state.get("revision_count", 0) or 0)
    if audit.get("status") == "FAIL":
        notes = "; ".join(
            f"{d.get('defect_type')}: {d.get('required_correction')}"
            for d in audit.get("defects", [])
            if isinstance(d, dict)
        )
        context = f"{context}\n\nCorrection required:\n{notes}"
        revision_count += 1

    try:
        dumped = StructuredReasoner().reason(query, context).model_dump()
    except Exception as exc:
        logger.warning("structured_reasoner_node fallback: %s", exc)
        skeleton = StructuredLegalArgument.empty(query)
        skeleton.uncertainties.append(f"reasoning unavailable ({exc})")
        dumped = skeleton.model_dump()
    return {"structured_argument": dumped, "legal_unit_evidence": units, "revision_count": revision_count}

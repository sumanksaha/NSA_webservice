"""Context builder for RAG generation.

Follows the orchestration pattern from app/services/document_lifecycle.py
(DocumentSaveCoordinator): ContextBuilder coordinates the multi-step process
of selecting, sorting, truncating, and formatting chunks into a structured
context string with citation labels that the LLM can reference as [n].
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.rag.retrieval.result import RetrievedChunk
from app.shared.config import cfg

logger = logging.getLogger(__name__)

from app.rag.constants import TOKENS_PER_CHAR

_TOKENS_PER_CHAR = TOKENS_PER_CHAR
_CHUNK_OVERHEAD_CHARS = 120

#: P0-1 prompt packing order — lower number packs earlier (analysis §6 item 1:
#: Primary → Exceptions → Definitions → Penalties → Cross-References).
#:
#: Deliberately NOT the selector's ``_EVIDENCE_TYPE_PRIORITY``. The two answer
#: different questions: selection asks *which* provisions to keep (a definition
#: may legitimately outrank an exception on evidence value), while packing asks
#: *in what order* to place them in the prompt window. Pushing definitions after
#: the primary provision and its exceptions is the anti-definition-anchoring
#: effect §6 calls for; reusing the selection order would place definitions
#: second and reintroduce the anchoring this feature exists to prevent.
_PACK_ORDER: dict[str, int] = {
    "primary_provision": 0,
    "subsection": 1,
    "exception": 2,
    "definition": 3,
    "penalty_provision": 4,
    "cross_reference": 5,
    "authority": 6,
    "adjacent_section": 7,
    "duplicate": 8,
}
_PACK_ORDER_DEFAULT = 7


@dataclass
class BuiltContext:
    """Result of assembling a retrieval context for the LLM."""

    context: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    chunk_count: int = 0
    truncated: bool = False
    total_tokens_estimate: int = 0
    enough_evidence: bool = True  # 2.8: answerability check flag
    missing: list[str] = field(default_factory=list)  # 2.8: missing requirement IDs


class ContextBuilder:
    """Assemble retrieved chunks into a structured LLM context.

    Args:
        max_context_chars: Maximum total characters for the context text.
        max_chunks: Maximum number of chunks to include.

    """

    # 2.6: Per-query-type context budgets, as CAPS relative to the operator's
    # ceiling (RAG_CONTEXT_MAX_CHUNKS / RAG_CONTEXT_MAX_CHARS).
    #
    # Raised from the original 8-12 chunk / 10k-16k char hardcodes after
    # measuring where the audited failures actually come from
    # (evaluation/failure_attribution.py): for 40 of the 89 model_wrong
    # questions the gold provision IS in the candidate pool but ranks below the
    # window — R@10 51.7% vs R@20 68.5%. Those were unanswerable by
    # construction. Two things forced this to move chars and chunks together:
    # measured evidence-set fit was flat across max_chunks 10/15/20 but jumped
    # when max_context_chars rose, so the char budget binds first.
    #
    # These are caps, applied via min() against the configured ceiling, so an
    # operator setting a SMALLER window still wins. They only raise the default
    # when the ceiling is larger.
    _QUERY_TYPE_BUDGETS: ClassVar[dict[str, dict[str, int]]] = {
        "case_law": {"max_context_chars": 24_000, "max_chunks": 20},
        "cross_reference": {"max_context_chars": 24_000, "max_chunks": 20},
        "prohibition": {"max_context_chars": 24_000, "max_chunks": 20},
        "definition": {"max_context_chars": 20_000, "max_chunks": 16},
        "penalty": {"max_context_chars": 24_000, "max_chunks": 20},
        "general": {"max_context_chars": 24_000, "max_chunks": 20},
        "procedure": {"max_context_chars": 24_000, "max_chunks": 20},
    }

    def __init__(
        self,
        max_context_chars: int | None = None,
        max_chunks: int | None = None,
        query_type: str = "",
    ) -> None:
        # 2.6: Adjust budget per query type when caller doesn't override.
        # Context-K lever (RAG_CONTEXT_MAX_CHUNKS / RAG_CONTEXT_MAX_CHARS)
        # sets the CEILING. Per-type budgets are caps applied beneath it via
        # min(), so raising the configured window actually reaches typed
        # queries instead of being silently overridden by a hardcoded table
        # (which is what made RAG_CONTEXT_MAX_CHUNKS=20 a no-op).
        base_chars = cfg.context_max_chars if max_context_chars is None else max_context_chars
        base_chunks = cfg.context_max_chunks if max_chunks is None else max_chunks
        budget = self._QUERY_TYPE_BUDGETS.get(query_type.lower(), {})
        self.max_context_chunks = min(budget.get("max_chunks", base_chunks), base_chunks)
        self.max_context_chars = min(budget.get("max_context_chars", base_chars), base_chars)
        self._query_type = query_type

    def build(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        query_type: str = "",
        evidence_set: dict[str, Any] | None = None,
    ) -> BuiltContext:
        """Build a structured LLM context from retrieved chunks.

        Chunks are sorted by retrieval score (descending), limited to
        ``max_chunks``, and formatted with citation labels [n] that the LLM
        can reference as [n].  Before building the final context, performs an
        answerability check (§2.8): if evidence coverage is insufficient for
        the query type, the method signals this so the caller can trigger
        targeted retrieval instead of proceeding to generation.

        P0-1: when *evidence_set* is supplied (the serialized dict produced
        by :func:`select_evidence_set` via ``apply_stages``), selected
        provisions are packed first in legal-role order (:data:`_PACK_ORDER`)
        so the primary provision leads the prompt, with unselected chunks
        retained as overflow.  Each ``<document>`` tag carries a ``role``
        attribute naming its evidence type.  When *evidence_set* is ``None``
        the behaviour is byte-identical to the score-only ordering.
        """
        if not chunks:
            return BuiltContext(
                context="",
                citations=[],
                chunk_count=0,
                truncated=False,
                total_tokens_estimate=0,
                enough_evidence=False,
                missing=["empty_chunks"],
            )

        # 2.8: Answerability check — assess whether retrieved chunks satisfy
        # evidence requirements for this query type.
        try:
            enough_evidence, missing = self._check_answerability(query, chunks, query_type or self._query_type)
        except Exception as exc:
            logger.warning("Answerability check failed: %s", exc)
            enough_evidence, missing = True, []  # fail open
        if not enough_evidence:
            return BuiltContext(
                context="",
                citations=[],
                chunk_count=0,
                truncated=False,
                total_tokens_estimate=0,
                enough_evidence=False,
                missing=missing,
            )

        ranked = sorted(chunks, key=lambda c: c.score, reverse=True)

        # P0-1: evidence-set packing.  The answerability gate above has
        # already judged the FULL pool — packing happens strictly after, so
        # filtering can never shrink the set that gate considered.
        roles: dict[Any, str] = {}
        if evidence_set:
            roles = self._roles_from_evidence_set(evidence_set, chunks)
            ranked = self._pack_by_role(ranked, roles)
        selected = ranked[: self.max_context_chunks]

        context_parts: list[str] = []
        citations: list[dict[str, Any]] = []
        total_chars = 0
        truncated = len(chunks) > self.max_context_chunks

        for idx, chunk in enumerate(selected, start=1):
            header = self._format_header(chunk)
            role = roles.get(chunk.chunk_id)
            entry = self._format_entry(idx, header, chunk.text, role=role)
            entry_len = len(entry) + _CHUNK_OVERHEAD_CHARS

            if total_chars + entry_len > self.max_context_chars:
                remaining = self.max_context_chars - total_chars
                if remaining > 200:
                    overhead = len(self._format_entry(idx, header, "", role=role))
                    max_text = remaining - overhead
                    truncated_text = chunk.text[: max(0, max_text)]
                    entry = self._format_entry(idx, header, truncated_text, role=role)
                    context_parts.append(entry)
                    total_chars += len(entry)
                    truncated = True
                    citations.append(self._citation_entry(idx, chunk, role))
                else:
                    truncated = True
                break

            context_parts.append(entry)
            total_chars += entry_len
            citations.append(self._citation_entry(idx, chunk, role))

        context = "\n\n---\n\n".join(context_parts)
        token_est = int(len(context) * _TOKENS_PER_CHAR)

        return BuiltContext(
            context=context,
            citations=citations,
            chunk_count=len(citations),
            truncated=truncated,
            total_tokens_estimate=token_est,
            enough_evidence=enough_evidence,
            missing=missing,
        )

    def _check_answerability(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        query_type: str,
    ) -> tuple[bool, list[str]]:
        """2.8: Determine if retrieved chunks satisfy evidence requirements.

        Heuristic: count how many distinct evidence types are covered by the
        retrieved chunks and compare against the minimum per query type.

        Returns:
            (enough_evidence: bool, missing: list of requirement IDs)

        """
        covered_types: set[str] = set()

        for chunk in chunks:
            ct = (chunk.text or "").lower()
            # Simple heuristic: detect evidence types from chunk text
            if any(t in ct for t in ["section", "article", "provision"]):
                covered_types.add("PROVISION")
            if any(t in ct for t in ["penalty", "fine", "punishment"]):
                covered_types.add("PENALTY_PROVISION")
            if any(t in ct for t in ["authority", "enforcement", "power"]):
                covered_types.add("AUTHORITY_PROVISION")
            if any(t in ct for t in ["definition", "means"]):
                covered_types.add("DEFINITION")
            if any(t in ct for t in ["exception", "unless", "except"]):
                covered_types.add("EXCEPTION")
            if any(t in ct for t in ["reference", "cross"]):
                covered_types.add("CROSS_REFERENCE")
            if any(t in ct for t in ["case", "precedent", "court"]):
                covered_types.add("CASE_LAW")
            if any(t in ct for t in ["temporal", "before", "after"]):
                covered_types.add("TEMPORAL")
            if any(t in ct for t in ["jurisdiction", "state", "district"]):
                covered_types.add("JURISDICTION")

        # Query-type minimum coverage thresholds (2.8)
        min_coverage: dict[str, int] = {
            "case_law": 3,
            "cross_reference": 2,
            "prohibition": 2,
            "definition": 1,
            "penalty": 2,
            "general": 2,
            "procedure": 2,
        }
        min_req = min_coverage.get(query_type.lower(), 2)

        if len(covered_types) < min_req:
            all_required = set(min_coverage.keys())
            missing_types = [t for t in all_required if t not in covered_types]
            return False, [f"type:{t}" for t in missing_types]

        if len(chunks) < 2 and query_type not in ("definition",):
            return False, ["count:minimum_chunks"]

        return True, []

    @staticmethod
    def _format_header(chunk: RetrievedChunk) -> str:
        parts: list[str] = []
        if chunk.document_title:
            parts.append(chunk.document_title)
        if chunk.section_number:
            parts.append(f"Section {chunk.section_number}")
        meta: list[str] = []
        if chunk.authority:
            meta.append(f"Authority: {chunk.authority}")
        if chunk.document_type:
            meta.append(f"Type: {chunk.document_type}")
        header = ", ".join(parts) if parts else "Unnamed document"
        if meta:
            header += f" ({', '.join(meta)})"
        return header

    @staticmethod
    def _roles_from_evidence_set(evidence_set: dict[str, Any], chunks: list[RetrievedChunk]) -> dict[str, str]:
        """Map chunk_id → evidence_type for the items the selector kept.

        Tolerates the serialized dict form (``EvidenceSet.to_dict()``) and
        ignores items whose chunk is no longer in the pool.  Unknown or
        malformed shapes degrade to an empty mapping, which leaves packing
        in score order rather than failing generation.
        """
        items = evidence_set.get("items") if isinstance(evidence_set, dict) else None
        if not isinstance(items, list):
            return {}
        known = {c.chunk_id for c in chunks}
        roles: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            chunk_id = item.get("chunk_id")
            evidence_type = item.get("evidence_type")
            if chunk_id and evidence_type and chunk_id in known:
                roles[chunk_id] = str(evidence_type)
        return roles

    @staticmethod
    def _pack_by_role(ranked: list[RetrievedChunk], roles: dict[str, str]) -> list[RetrievedChunk]:
        """Order selected provisions by legal role, unselected as overflow.

        Selected chunks sort by role (:data:`_PACK_ORDER`) then by descending score;
        unselected chunks follow in their original score order.
        """
        if not roles:
            return list(ranked)
        return sorted(
            ranked,
            key=lambda c: (
                0 if c.chunk_id in roles else 1,
                _PACK_ORDER.get(roles.get(c.chunk_id, ""), _PACK_ORDER_DEFAULT),
                -c.score,
            ),
        )

    @staticmethod
    def _format_entry(idx: int, header: str, text: str, role: str | None = None) -> str:
        """One per-source entry (research §3.1 document/source tags).

        P0-1: *role* annotates the tag with the provision's legal role so the
        model can distinguish the governing provision from definitions and
        exceptions.  Omitted entirely when no evidence set was supplied, so
        the default prompt is unchanged.
        """
        if role:
            return (
                f'<document index="{idx}" role="{role}">\n<source>[Source {idx}] {header}</source>\n{text}\n</document>'
            )
        return f'<document index="{idx}">\n<source>[Source {idx}] {header}</source>\n{text}\n</document>'

    @staticmethod
    def _citation_entry(idx: int, chunk: RetrievedChunk, role: str | None = None) -> dict[str, Any]:
        entry = {
            "index": idx,
            "chunk_id": chunk.chunk_id,
            "section_number": chunk.section_number,
            "document_title": chunk.document_title,
            "document_type": chunk.document_type,
            "authority": chunk.authority,
        }
        if role:
            entry["evidence_type"] = role
        return entry

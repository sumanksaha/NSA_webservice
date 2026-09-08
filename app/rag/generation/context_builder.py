"""Context builder for RAG generation.

Follows the orchestration pattern from app/services/document_lifecycle.py
(DocumentSaveCoordinator): ContextBuilder coordinates the multi-step process
of selecting, sorting, truncating, and formatting chunks into a structured
context string with citation labels that the LLM can reference as [n].
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.rag.retrieval.result import RetrievedChunk

logger = logging.getLogger(__name__)

from app.rag.constants import TOKENS_PER_CHAR

_TOKENS_PER_CHAR = TOKENS_PER_CHAR
_CHUNK_OVERHEAD_CHARS = 120


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

    # 2.6: Per-query-type context budgets.  case_law needs longer excerpts
    # (precedent chains); prohibition queries need fewer but more focused
    # chunks; cross_reference queries need more chunks to cover referenced
    # sections.
    _QUERY_TYPE_BUDGETS: dict[str, dict[str, int]] = {
        "case_law": {"max_context_chars": 16_000, "max_chunks": 12},
        "cross_reference": {"max_context_chars": 14_000, "max_chunks": 12},
        "prohibition": {"max_context_chars": 10_000, "max_chunks": 8},
        "definition": {"max_context_chars": 10_000, "max_chunks": 8},
        "penalty": {"max_context_chars": 12_000, "max_chunks": 10},
        "general": {"max_context_chars": 12_000, "max_chunks": 10},
        "procedure": {"max_context_chars": 12_000, "max_chunks": 10},
    }  # noqa: mutable-default-value

    def __init__(
        self,
        max_context_chars: int = 12_000,
        max_chunks: int = 10,
        query_type: str = "",
    ) -> None:
        # 2.6: Adjust budget per query type when caller doesn't override.
        budget = self._QUERY_TYPE_BUDGETS.get(query_type.lower(), {})
        self.max_context_chunks = budget.get("max_chunks", max_chunks)
        self.max_context_chars = budget.get("max_context_chars", max_context_chars)
        self._query_type = query_type

    def build(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        query_type: str = "",
    ) -> BuiltContext:
        """Build a structured LLM context from retrieved chunks.

        Chunks are sorted by retrieval score (descending), limited to
        ``max_chunks``, and formatted with citation labels [n] that the LLM
        can reference as [n].  Before building the final context, performs an
        answerability check (§2.8): if evidence coverage is insufficient for
        the query type, the method signals this so the caller can trigger
        targeted retrieval instead of proceeding to generation.
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
        selected = ranked[: self.max_context_chunks]

        context_parts: list[str] = []
        citations: list[dict[str, Any]] = []
        total_chars = 0
        truncated = len(chunks) > self.max_context_chunks

        for idx, chunk in enumerate(selected, start=1):
            header = self._format_header(chunk)
            entry = f"[Source {idx}] {header}\n{chunk.text}"
            entry_len = len(entry) + _CHUNK_OVERHEAD_CHARS

            if total_chars + entry_len > self.max_context_chars:
                remaining = self.max_context_chars - total_chars
                if remaining > 200:
                    max_text = remaining - len(header) - 50
                    truncated_text = chunk.text[: max(0, max_text)]
                    entry = f"[Source {idx}] {header}\n{truncated_text}"
                    context_parts.append(entry)
                    total_chars += len(entry)
                    truncated = True
                    citations.append(self._citation_entry(idx, chunk))
                else:
                    truncated = True
                break

            context_parts.append(entry)
            total_chars += entry_len
            citations.append(self._citation_entry(idx, chunk))

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
    def _citation_entry(idx: int, chunk: RetrievedChunk) -> dict[str, Any]:
        return {
            "index": idx,
            "chunk_id": chunk.chunk_id,
            "section_number": chunk.section_number,
            "document_title": chunk.document_title,
            "document_type": chunk.document_type,
            "authority": chunk.authority,
        }

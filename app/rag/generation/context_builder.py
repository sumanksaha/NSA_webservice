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

_TOKENS_PER_CHAR = 0.25
_CHUNK_OVERHEAD_CHARS = 120


@dataclass
class BuiltContext:
    """Result of assembling a retrieval context for the LLM."""

    context: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    chunk_count: int = 0
    truncated: bool = False
    total_tokens_estimate: int = 0


class ContextBuilder:
    """Assemble retrieved chunks into a structured LLM context.

    Args:
        max_context_chars: Maximum total characters for the context text.
        max_chunks: Maximum number of chunks to include.
    """

    def __init__(
        self,
        max_context_chars: int = 12_000,
        max_chunks: int = 10,
    ) -> None:
        self.max_context_chunks = max_chunks
        self.max_context_chars = max_context_chars

    def build(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        query_type: str = "",
    ) -> BuiltContext:
        """Build a structured LLM context from retrieved chunks.

        Chunks are sorted by retrieval score (descending), limited to
        ``max_chunks``, and formatted with metadata headers.  Each chunk
        receives a ``[Source n]`` label so the LLM can cite ``[n]``.
        """
        if not chunks:
            return BuiltContext(
                context="",
                citations=[],
                chunk_count=0,
                truncated=False,
                total_tokens_estimate=0,
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
        )

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

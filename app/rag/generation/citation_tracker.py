"""Citation tracker — extract and map citations from LLM responses.

Follows the reference-extraction patterns from
``app/services/legal_engine.py`` (``extract_section_references``) and
``app/metadata_extractor/extractors/regex.py``: the tracker parses
``[n]`` bracket-citation markers from the LLM response and maps them
to the corresponding source chunks.  It also detects inline section
references (e.g. "Section 55") and matches them to chunks that carry
``section_number`` metadata.
"""

from __future__ import annotations

import logging
import re

from app.rag.retrieval.result import Citation, RetrievedChunk

logger = logging.getLogger(__name__)

#: Regex for [n] bracket citations — e.g. [1], [12].
_BRACKET_CITATION_RE = re.compile(r"\[(\d+)\]")

#: Regex for the ``[Source n]`` form.  ``ContextBuilder`` labels every
#: prompt chunk ``<source>[Source {idx}] {header}</source>``, so this is the
#: citation form the model is actually shown and most often emits.  It was
#: previously ignored entirely, so ``[Source 7]`` counted as no citation at
#: all and citation recall was under-reported in every measurement.
_SOURCE_CITATION_RE = re.compile(r"\[Source\s+(\d+)\]", re.IGNORECASE)

#: Regex for inline section references — e.g. "Section 55", "Section 3(1)(a)".
_SECTION_REF_RE = re.compile(r"\bSection\s+(\d+(?:\([a-zA-Z0-9]+\))*)", re.IGNORECASE)


class CitationTracker:
    """Extract citations from an LLM response and map them to source chunks.

    The tracker works in two passes:

    1. **Bracket citations** — ``[n]`` and ``[Source n]`` markers in the
       response are matched to the ``n``-th chunk (1-based) in the citation
       map provided by :class:`ContextBuilder`.  ``ContextBuilder`` renders
       each prompt chunk as ``<source>[Source {idx}] ...</source>``, so the
       ``[Source n]`` form is the one the model is shown.

    2. **Inline section references** — bare mentions of ``Section <num>``
       are matched against chunks whose ``section_number`` field matches.
    """

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def extract(
        self,
        response_text: str,
        chunks: list[RetrievedChunk],
        citation_map: dict[int, RetrievedChunk] | None = None,
    ) -> list[Citation]:
        """Extract all citations from *response_text*.

        Args:
            response_text: The LLM's generated response.
            chunks: The full list of retrieved chunks (used for section
                reference matching and confidence computation).
            citation_map: Optional pre-built ``{index: chunk}`` mapping
                from :class:`ContextBuilder`.  If omitted, ``chunks``
                are numbered 1..N by their list order.

        Returns:
            A list of :class:`Citation` objects with ``confidence`` set
            based on how strongly the chunk supports the response.

        """
        if not response_text or not chunks:
            return []

        if citation_map is None:
            citation_map = {i + 1: c for i, c in enumerate(chunks)}

        citations: list[Citation] = []
        seen_chunk_ids: set[str] = set()

        # Pass 1 — bracket citations [n] and [Source n]
        citations.extend(self._extract_bracket_citations(response_text, citation_map, seen_chunk_ids))

        # Pass 2 — inline section references
        citations.extend(self._extract_section_citations(response_text, chunks, seen_chunk_ids))

        return citations

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def _extract_bracket_citations(
        cls,
        response_text: str,
        citation_map: dict[int, RetrievedChunk],
        seen: set[str],
    ) -> list[Citation]:
        """Parse ``[n]`` and ``[Source n]`` markers and map them to chunks.

        Both forms are scanned in a single left-to-right pass so the snippet
        window is taken around the marker the model actually wrote.  Scanning
        the two regexes independently would attribute a citation to the
        wrong span when both forms appear in one answer.
        """
        results: list[Citation] = []

        matches = [(m.start(), m.end(), int(m.group(1))) for m in _BRACKET_CITATION_RE.finditer(response_text)]
        matches += [(m.start(), m.end(), int(m.group(1))) for m in _SOURCE_CITATION_RE.finditer(response_text)]
        # Left-to-right by position; ties (an index inside a [Source n] label)
        # resolve to the longest match first so the [Source n] span wins.
        matches.sort(key=lambda t: (t[0], -(t[1] - t[0])))

        for start, end, idx in matches:
            chunk = citation_map.get(idx)
            if chunk is None:
                continue
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)

            snippet = cls._extract_snippet(response_text, start, end)
            confidence = cls._citation_confidence(chunk, snippet)

            results.append(
                Citation(
                    chunk_id=chunk.chunk_id,
                    section_number=chunk.section_number,
                    document_title=chunk.document_title,
                    document_type=chunk.document_type,
                    authority=chunk.authority,
                    url=None,
                    snippet=snippet,
                    confidence=confidence,
                ),
            )

        return results

    @staticmethod
    def _extract_section_citations(
        response_text: str,
        chunks: list[RetrievedChunk],
        seen: set[str],
    ) -> list[Citation]:
        """Match inline ``Section <num>`` references to chunk payloads."""
        results: list[Citation] = []

        for match in _SECTION_REF_RE.finditer(response_text):
            section_num = match.group(1)
            for chunk in chunks:
                if chunk.section_number and chunk.section_number == section_num:
                    if chunk.chunk_id in seen:
                        continue
                    seen.add(chunk.chunk_id)
                    snippet = CitationTracker._extract_snippet(response_text, match.start(), match.end())
                    confidence = CitationTracker._citation_confidence(chunk, snippet)
                    results.append(
                        Citation(
                            chunk_id=chunk.chunk_id,
                            section_number=chunk.section_number,
                            document_title=chunk.document_title,
                            document_type=chunk.document_type,
                            authority=chunk.authority,
                            url=None,
                            snippet=snippet,
                            confidence=confidence,
                        ),
                    )

        return results

    @staticmethod
    def _extract_snippet(text: str, start: int, end: int, window: int = 80) -> str:
        snippet_start = max(0, start - window)
        snippet_end = min(len(text), end + window)
        return text[snippet_start:snippet_end].strip()

    @staticmethod
    def _citation_confidence(chunk: RetrievedChunk, snippet: str) -> float:
        """Compute citation confidence (0.0-1.0).

        Mirrors the method-based scoring pattern from
        ``app/metadata_extractor/confidence.py``:
        - Section match (regex method) -> 0.85
        - Keyword/retrieval score match -> 0.70
        - Default -> 0.30
        """
        if chunk.section_number:
            return 0.85
        if chunk.score > 0.5:
            return 0.70
        return 0.30

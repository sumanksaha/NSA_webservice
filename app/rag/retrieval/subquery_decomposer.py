"""1.3 — Answer Decomposition / Sub-Question Routing.

Detects compound legal queries (multiple section references, "and"/"or"
conjunctions) and decomposes into focused sub-queries for independent
retrieval, improving answer completeness and reducing context contamination.
"""

from __future__ import annotations
import logging
import re

logger = logging.getLogger(__name__)
_COMPOUND_CONJUNCTION = re.compile(r"\b(?:and|or|both|with)\b", re.IGNORECASE)
_SECTION_REGEX = re.compile(r"\b(?:section|sec\.?)\s*(\d{1,3})", re.IGNORECASE)


class SubQueryDecomposer:
    """Decompose compound legal queries into independent sub-queries."""

    def __init__(self) -> None:
        pass

    def decompose(self, query: str) -> list[str]:
        """Split compound query into sub-queries. Returns [original] if single-fact."""
        if not query or not query.strip():
            return [query]
        query = query.strip()
        sections = _SECTION_REGEX.findall(query)
        if len(sections) < 2 or not _COMPOUND_CONJUNCTION.search(query):
            return [query]
        sub_queries = []
        for sec_num in sections:
            sub_queries.append(f"Section {sec_num} provisions")
        return sub_queries[: len(sections)] if sub_queries else [query]

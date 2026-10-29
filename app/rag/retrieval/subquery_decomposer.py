"""1.3 — Answer Decomposition / Sub-Question Routing.

Detects compound legal queries and decomposes them into focused sub-queries
for independent retrieval, improving answer completeness and reducing context
contamination.

The original implementation only handled two or more explicit ``Section N``
references joined by a conjunction.  P1 item 7 extends it to compound
factual/legal questions (penalty + procedure, two interrogatives, semicolon
lists) while keeping the conservative default: a noun phrase such as
"Food Safety and Standards" is never split just because it contains "and".
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_COMPOUND_CONJUNCTION = re.compile(r"\b(?:and|or|both|with)\b", re.IGNORECASE)
_SECTION_REGEX = re.compile(r"\b(?:section|sec\.?)\s*(\d{1,3})", re.IGNORECASE)
_REFERENCE_REGEX = re.compile(
    r"\b(?:section|sec\.?|rule|regulation|clause|article|schedule|form)\s+"
    r"(?:\d|[ivxlcdm]+\b)",
    re.IGNORECASE,
)
_LEGAL_CUE_REGEX = re.compile(
    r"\b(?:penalt(?:y|ies)|punish\w*|fine|imprison\w*|licen[cs]\w*|permit\w*|"
    r"procedure|authorit\w*|deadline|time[- ]limit|exempt\w*|proviso|exception|"
    r"definition|offence|offense|duty|duties|obligation|recall|import\w*|export\w*|"
    r"label\w*|packag\w*|advertis\w*|sampl\w*|inspection|appeal\w*|adjudicat\w*|"
    r"registration|notice|seizure|storage|hygiene|additive|contaminant)\b",
    re.IGNORECASE,
)
_INTERROGATIVE_REGEX = re.compile(
    r"^\s*(?:what|which|who|whom|whose|when|where|why|how|is|are|was|were|"
    r"can|could|may|might|must|shall|should|does|do|did)\b",
    re.IGNORECASE,
)
_WH_PHRASE_REGEX = re.compile(
    r"^\s*(what|which|who|whom|whose|when|where|why|how)\b",
    re.IGNORECASE,
)
_MAX_SUBQUERIES = 4


def _split_outside_groups(text: str) -> list[tuple[str, str]]:
    """Split on top-level semicolons / coordinating conjunctions.

    Quotes and parentheses are respected, so ``"A and B" (Section 3 and 4)``
    does not split inside the quoted phrase or the parenthetical.  Each
    returned tuple carries the separator that preceded the fragment, so a
    conservative merge can put ``or`` back instead of silently turning it
    into ``and``.
    """
    parts: list[tuple[str, str]] = []
    start = 0
    separator = ""
    i = 0
    quote: str | None = None
    depth = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote and (i == 0 or text[i - 1] != "\\"):
                quote = None
            i += 1
            continue
        if ch in {'"', "'"}:
            quote = ch
            i += 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth = max(depth - 1, 0)
            i += 1
            continue
        if depth == 0:
            if ch == ";":
                parts.append((separator, text[start:i].strip(" \t\r\n,.")))
                separator = ";"
                start = i + 1
                i += 1
                continue
            for token in (" as well as ", " and ", " or ", " also "):
                if text[i : i + len(token)].lower() == token:
                    parts.append((separator, text[start:i].strip(" \t\r\n,.")))
                    separator = token.strip()
                    start = i + len(token)
                    i += len(token) - 1
                    break
        i += 1
    parts.append((separator, text[start:].strip(" \t\r\n,.")))
    return [(sep, part) for sep, part in parts if part]


def _should_split(left: str, right: str) -> bool:
    """Conservative conjunction test: both sides must look independently useful."""
    if len(left.split()) < 2 or len(right.split()) < 2:
        return False
    left_ref = bool(_REFERENCE_REGEX.search(left))
    right_ref = bool(_REFERENCE_REGEX.search(right))
    if left_ref and right_ref:
        return True
    left_cue = bool(_LEGAL_CUE_REGEX.search(left))
    right_cue = bool(_LEGAL_CUE_REGEX.search(right))
    if left_cue and right_cue:
        return True
    if left_cue and _INTERROGATIVE_REGEX.search(right):
        return True
    if right_cue and _INTERROGATIVE_REGEX.search(left):
        return True
    return False


def _with_question_prefix(segment: str, original: str) -> str:
    """Carry the original wh-phrase onto a fragment that lacks one."""
    if _INTERROGATIVE_REGEX.search(segment) or _REFERENCE_REGEX.match(segment):
        return segment
    if segment.lower().startswith("the ") and original.lower().lstrip().startswith("what"):
        return f"What is {segment}"
    match = _WH_PHRASE_REGEX.search(original)
    if not match:
        return segment
    return f"{match.group(1).capitalize()} {segment[0].lower() + segment[1:]}" if segment else segment


class SubQueryDecomposer:
    """Decompose compound legal queries into independent sub-queries."""

    def __init__(self) -> None:
        pass

    def decompose(self, query: str) -> list[str]:
        """Split a compound query into sub-queries. Returns [original] if single-fact."""
        if not query or not query.strip():
            return [query]
        query = query.strip()

        # Preserve the original, heavily-tested section behaviour exactly.
        sections = _SECTION_REGEX.findall(query)
        if len(sections) >= 2 and _COMPOUND_CONJUNCTION.search(query):
            return [f"Section {sec_num} provisions" for sec_num in sections[: _MAX_SUBQUERIES]]

        candidates = _split_outside_groups(query)
        if len(candidates) < 2:
            return [query]

        # Merge adjacent fragments that fail the conservative split test.
        merged: list[str] = []
        for separator, fragment in candidates:
            if merged and not _should_split(merged[-1], fragment):
                merged[-1] = f"{merged[-1]} {separator or 'and'} {fragment}"
            else:
                merged.append(fragment)
        if len(merged) < 2:
            return [query]

        sub_queries: list[str] = []
        seen: set[str] = set()
        for fragment in merged:
            candidate = _with_question_prefix(fragment, query).strip()
            key = candidate.lower()
            if candidate and key not in seen:
                seen.add(key)
                sub_queries.append(candidate)
        if len(sub_queries) < 2:
            return [query]
        return sub_queries[: _MAX_SUBQUERIES]

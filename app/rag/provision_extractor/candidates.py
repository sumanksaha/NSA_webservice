"""Grammar-aware boundary candidate generation (ADR-0009 Tier 1, stage 1).

Proposes boundary candidates over ordered document text by **reusing** the
regexes the rest of the RAG module already trusts (``app/rag/chunker.py``),
rather than authoring a parallel grammar:

* ``engine_main``   — line-start ``NN. Title`` / ``NN. (1) …`` headers.
* ``engine_word``   — explicit ``Section N`` / ``Sec. N`` / ``§ N`` forms.
* ``dotted_clause`` — leading dotted regulation numbers (``2.4.15``).
* ``l4_header``     — the any-position, act-range-validated L4 header rule.

Candidates are proposals only: the :mod:`~app.rag.provision_extractor.disambiguator`
accepts or rejects them.  The module is pure text-in / data-out — no I/O, no
Qdrant, no third-party dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.chunker import _DOTTED_CLAUSE_RE, _L4_HEADER_RE

#: Line-start statutory header: ``26. Responsibilities`` / ``45. (I) The …``.
#: The uppercase look-ahead keeps dates/measurements (``0.75``) out.
_HEADER_RE = re.compile(r"(?m)^[ \t]*(\d{1,4})[ \t]*\.[ \t]*(?:\([ \t]*)?(?=[A-Z])")

#: Explicit ``Section 31`` / ``Sec. 31`` / ``§ 31`` reference form.
_WORD_RE = re.compile(r"\b(?:Section|Sec\.|§)[ \t]*(\d{1,4})", re.IGNORECASE)

#: The chunker's dotted rule is line-anchored without ``MULTILINE`` — recompile
#: with the flag so ``finditer`` can propose it on any line of a joined doc.
_DOTTED_LINE_RE = re.compile(_DOTTED_CLAUSE_RE.pattern, re.MULTILINE)


@dataclass(frozen=True)
class BoundaryCandidate:
    """A proposed provision boundary at a character offset in the document.

    Attributes:
        char_offset: Offset of the boundary's first character in the document.
        match_end: Offset just past the matched number (for subsection parsing).
        raw_number: The number token as matched (``"31"`` or ``"2.4.15"``).
        source_pattern: Which rule proposed it — ``engine_main``,
            ``engine_word``, ``dotted_clause``, or ``l4_header``.
        grammar_type: ``"section"`` or ``"dotted"``.
    """

    char_offset: int
    match_end: int
    raw_number: str
    source_pattern: str
    grammar_type: str


#: Lower value wins on offset collisions (more specific sources first).
_SOURCE_PRIORITY: dict[str, int] = {
    "engine_main": 0,
    "engine_word": 0,
    "dotted_clause": 1,
    "l4_header": 2,
}


def _grammar_of(raw_number: str) -> str:
    return "dotted" if "." in raw_number else "section"


def generate_candidates(text: str) -> list[BoundaryCandidate]:
    """Propose all boundary candidates in *text*, ordered by offset.

    Offset collisions are resolved by source priority — e.g. a line starting
    ``26. Title`` is proposed by both ``engine_main`` and the any-position L4
    rule; the more specific ``engine_main`` wins.
    """
    if not text:
        return []

    by_offset: dict[int, tuple[int, BoundaryCandidate]] = {}
    sources: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("engine_main", _HEADER_RE),
        ("engine_word", _WORD_RE),
        ("dotted_clause", _DOTTED_LINE_RE),
        ("l4_header", _L4_HEADER_RE),
    )
    for source, pattern in sources:
        priority = _SOURCE_PRIORITY[source]
        for match in pattern.finditer(text):
            offset = match.start()
            existing = by_offset.get(offset)
            if existing is not None and existing[0] <= priority:
                continue
            raw = match.group(1)
            by_offset[offset] = (
                priority,
                BoundaryCandidate(
                    char_offset=offset,
                    match_end=match.end(),
                    raw_number=raw,
                    source_pattern=source,
                    grammar_type=_grammar_of(raw),
                ),
            )

    return [candidate for _, (_, candidate) in sorted(by_offset.items())]

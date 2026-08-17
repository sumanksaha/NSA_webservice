"""Claim extractor — pull factual claims out of an LLM response.

A *claim* is any declarative sentence that asserts a fact about the legal
domain — section numbers, authorities, penalties, requirements, dates, etc.
The extractor is deliberately rule-based (regex + sentence splitting) so it
runs without an LLM; it follows the regex-extraction pattern from
``app/metadata_extractor/extractors/regex.py`` and
``app/services/legal_engine.py`` (``extract_section_references``).

Each :class:`ExtractedClaim` carries the sentence text plus any *entities*
(section numbers, authority mentions, percentages, amounts) that the
:class:`EvidenceVerifier` can cross-reference against retrieved chunks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Split into sentences.  Splits on ``[.!?]`` followed by whitespace, but
#: only when the next non-space character is an uppercase letter, quote,
#: or bracket — this avoids false splits on abbreviations like ``Rs. 10,000``.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'\[])")

#: Section references: "Section 55", "Sec. 55", "§55", "Section 3(1)(a)".
_SECTION_RE = re.compile(
    r"(?:[Ss]ection|\u00a7|Sec\.?)\s*(\d+(?:\([a-zA-Z0-9]+\))*)", re.IGNORECASE
)

#: Percentage figures: "100%", "fifty percent".
_PERCENT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*%")

#: Monetary amounts: "Rs. 5000", "₹5,000".
_AMOUNT_RE = re.compile(r"\b(Rs\.|\u20b9|INR)\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)

#: Act / rule names: "the FSS Act", "FSSAI regulations" (Phase 1 — the
#: FSS alternatives stay; the generic statute pattern below also matches any
#: capitalized statute name, e.g. "Companies Act, 2013").
_AUTHORITY_RE = re.compile(
    r"\b(?:FSS[\s]*Act|FSSAI|Food\s*Safety\s*and\s*Standards\s*Act|regulations?)",
    re.IGNORECASE,
)

#: Generic statute-name pattern: one or more capitalized words (plus optional
#: parenthetical groups, e.g. "(Prevention and Control of Pollution)") followed
#: by Act/Rules/Regulations.  Case-sensitive so lowercase boilerplate ("the
#: Act") does not match; a leading "The" is allowed.
_STATUTE_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9&.,()\-]*(?:\s+[A-Z][A-Za-z0-9&.,()\-]*|\s*\([^()]*\)){0,4}\s+"
    r"(?:Act|Rules?|Regulations?)\b"
)


@dataclass
class ExtractedClaim:
    """A single factual claim extracted from an LLM response.

    Attributes:
        text: The full sentence asserting the claim.
        index: 0-based position of the claim in the response.
        entities: Dict of entity-type -> list of extracted values
            (e.g. ``{"section": ["55", "3(1)(a)"], "percent": ["100"]}``).
        section_numbers: Flattened list of section numbers (convenience).
    """

    text: str
    index: int = 0
    entities: dict[str, list[str]] = field(default_factory=dict)

    @property
    def section_numbers(self) -> list[str]:
        """Convenience accessor for section-number entities."""
        return self.entities.get("section", [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "index": self.index,
            "entities": self.entities,
            "section_numbers": self.section_numbers,
        }


class ClaimExtractor:
    """Extract factual claims from an LLM response.

    The extractor splits the response into sentences and treats each
    non-trivial sentence as a potential claim.  Trivial sentences
    (greetings, empty, or pure filler) are filtered out.
    """

    #: Minimum token count for a sentence to be considered a claim.
    MIN_TOKENS = 3

    def __init__(self, min_tokens: int = MIN_TOKENS) -> None:
        self.min_tokens = min_tokens

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def extract(self, response_text: str) -> list[ExtractedClaim]:
        """Extract all factual claims from *response_text*.

        Args:
            response_text: The LLM's generated response (post-sanitization).

        Returns:
            A list of :class:`ExtractedClaim`, in order of appearance.
        """
        if not response_text or not response_text.strip():
            return []

        sentences = self._split_sentences(response_text)
        claims: list[ExtractedClaim] = []

        for _i, sent in enumerate(sentences):
            if not self._is_claim(sent):
                continue
            entities = self._extract_entities(sent)
            # A sentence is a *factual* claim if it carries at least
            # one extractable entity OR is substantive enough to assert
            # something verifiable.
            claims.append(
                ExtractedClaim(
                    text=sent.strip(),
                    index=len(claims),
                    entities=entities,
                )
            )

        return claims

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _split_sentences(self, text: str) -> list[str]:
        """Split *text* into sentence strings, keeping punctuation."""
        if not text.strip():
            return []
        parts = _SENTENCE_SPLIT_RE.split(text.strip())
        return [p.strip() for p in parts if p and p.strip()]

    def _is_claim(self, sentence: str) -> bool:
        """Heuristic: is *sentence* a substantive claim rather than filler?"""
        if len(sentence.split()) < self.min_tokens:
            return False
        # Filter out pure navigation / formatting sentences.
        lower = sentence.lower().strip()
        return lower not in ("yes", "no", "based on the provided context.", "")

    @staticmethod
    def _extract_entities(sentence: str) -> dict[str, list[str]]:
        """Pull legal entities out of a single sentence."""
        entities: dict[str, list[str]] = {}

        sections = _SECTION_RE.findall(sentence)
        entities["section"] = sections

        percents = _PERCENT_RE.findall(sentence)
        entities["percent"] = percents

        amounts = [f"{cur}{amt}" for cur, amt in _AMOUNT_RE.findall(sentence)]
        entities["amount"] = amounts

        authorities = _AUTHORITY_RE.findall(sentence) + _STATUTE_RE.findall(sentence)
        # Deduplicate (FSS names may be caught by both patterns) preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for a in authorities:
            if a.lower() not in seen:
                seen.add(a.lower())
                deduped.append(a)
        entities["authority"] = deduped

        # Remove empty lists.
        return {k: v for k, v in entities.items() if v}

    def to_dict(
        self, claims: list[ExtractedClaim]
    ) -> list[dict[str, Any]]:
        """Convenience: serialize a list of claims."""
        return [c.to_dict() for c in claims]

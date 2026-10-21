"""Text-based gold resolution fallback.

When matches_gold fails because a chunk lacks a section_number stamp
(or carries the wrong one), this module provides a textual fallback:
it searches the chunk text for the gold provision distinctive title
phrase (e.g. fssai:s52 -> Punitive damages).

The text fallback is opt-in: callers pass text_fallback=True.  The
default path (stamp-only) is unchanged so all existing metrics, gates,
and training-data derivations are byte-identical unless they choose the
enhanced resolver.

Design constraints:
* Family guard first: chunk act_name / document_title must resolve to
  the gold unit family before text match.
* Distinctiveness floor: titles with fewer than 2 content words rejected.
* Token-boundary matching: phrase tokens must appear as a contiguous
  word sequence in the chunk text.  Hyphens are stripped so
  "non-punitive" merges into one token and cannot false-match.
* Corrupt OCR: garbage chunks contain no phrase, so no false match.
"""

from __future__ import annotations

from typing import Any

from evaluation.benchmark import GoldUnit
from evaluation.resolution import FamilyMap

__all__ = [
    "MIN_TITLE_WORDS",
    "matches_gold_enhanced",
    "resolve_by_text",
    "text_matches_title",
    "title_is_distinctive",
    "title_phrase",
]

MIN_TITLE_WORDS = 2
_MAX_TITLE_LEN = 80


def _normalise_text(text: str) -> str:
    """Lower-case, strip punctuation (keep alnum + space), collapse whitespace.

    Hyphens are removed (merged into adjacent tokens) so that hyphenated
    compounds like "non-punitive" cannot fragment-match a phrase.
    """
    if not text:
        return ""
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch.isspace():
            out.append(" ")
        # everything else (hyphens, punctuation) is dropped
    s = "".join(out)
    return " ".join(s.split())


def _distinct_words(phrase: str) -> int:
    """Count word tokens (alphabetic sequences of 2+ chars)."""
    return len([w for w in phrase.split() if len(w) >= 2])


def title_phrase(title: str) -> str | None:
    """Extract a normalised, distinctive search phrase from a title.

    Returns None when the title is empty, too short, or not distinctive.
    """
    if not title:
        return None
    norm = _normalise_text(title)
    if not norm:
        return None
    if len(norm) > _MAX_TITLE_LEN:
        norm = norm[:_MAX_TITLE_LEN]
    if _distinct_words(norm) < MIN_TITLE_WORDS:
        return None
    return norm


def title_is_distinctive(title: str) -> bool:
    """Whether title produces a usable text-search phrase."""
    return title_phrase(title) is not None


def text_matches_title(chunk_text: str, title: str) -> bool:
    """Return True when chunk_text contains the gold provision title phrase.

    Pure function: no regex, no I/O, no model calls.  Normalises both
    the chunk text and the title phrase to lowercase alphanumeric+space,
    then checks for a contiguous token match.
    """
    phrase = title_phrase(title)
    if phrase is None:
        return False
    text = _normalise_text(chunk_text or "")
    if not text:
        return False
    phrase_words = phrase.split()
    text_words = text.split()
    n_phrase = len(phrase_words)
    if n_phrase == 0:
        return False
    for i in range(len(text_words) - n_phrase + 1):
        if text_words[i : i + n_phrase] == phrase_words:
            return True
    return False


def _payload_families(payload: dict, family_map: FamilyMap) -> set[str]:
    """Families a chunk belongs to, from act_name / document_title."""
    fams: set[str] = set()
    for source in (payload.get("act_name"), payload.get("document_title")):
        if not source:
            continue
        for fam in family_map.family_s_for_act(str(source)):
            fams.add(fam)
    return fams


def resolve_by_text(
    payload: dict,
    unit: GoldUnit,
    family_map: FamilyMap,
) -> bool:
    """Resolve a gold unit to a chunk via distinctive title-phrase matching.

    Guards: 1. Family must match.  2. Title must be distinctive.
    3. Corrupt OCR chunks that contain no phrase are not matched.
    """
    if not unit.family:
        return False
    families = _payload_families(payload, family_map)
    if not families or unit.family not in families:
        return False
    chunk_text = payload.get("chunk_text") or payload.get("text") or ""
    if not chunk_text:
        return False
    return text_matches_title(chunk_text, unit.title)


def matches_gold_enhanced(
    payload: dict,
    unit: GoldUnit,
    family_map: FamilyMap,
    *,
    text_fallback: bool = False,
) -> bool:
    """Like matches_gold, with an optional text fallback.

    Delegates to :func:`resolution.matches_gold` with the same
    ``text_fallback`` flag so the two entry points are always consistent.
    Preserved for callers that import from this module instead of
    ``resolution``.
    """
    from evaluation.resolution import matches_gold

    return matches_gold(payload, unit, family_map, text_fallback=text_fallback)

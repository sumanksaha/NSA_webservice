"""Per-candidate boundary feature extraction (ADR-0009 Tier 2 input).

Every feature is computable from already-indexed payload text — no new data
collection.  The same dictionary feeds both the Tier-1 rules score (P0) and,
in hybrid mode, the Tier-2 ``LogisticRegression`` vector, so the two tiers
share one contract.

``FEATURE_NAMES`` fixes the vector order: a model artifact trained against
one ordering must never be scored against another.
"""

from __future__ import annotations

import re

from app.rag.legal_sections import is_known_section_for_act
from app.rag.provision_extractor.candidates import BoundaryCandidate

#: Stable feature-vector ordering.  Never reorder without retraining.
FEATURE_NAMES: tuple[str, ...] = (
    "is_line_start",
    "in_act_range",  # 1.0 in range | -1.0 out of range | 0.0 unknown act
    "source_engine_main",
    "source_engine_word",
    "source_dotted_clause",
    "source_l4_header",
    "is_dotted",
    "number_value",
    "is_year_like",
    "delta_from_prev_accepted",
    "is_monotonic",
    "title_score",
    "has_emdash_title",
    "crossref_density",
    "page_number_only",
    "is_first_occurrence",
    "sections_covered_agree",
    "engine_confidence",
    # --- Richer structural features (Tier-2 recall, 2026-10-01) -------------
    "is_blank_line_before",
    "preceded_by_period",
    "is_subsection_header",
    "title_word_count",
    "is_short_title",
    "offset_ratio",
    "abs_delta",
)

_CROSSREF_RE = re.compile(r"\b(?:section|sec\.|rule|regulation|schedule|chapter)\s+\d", re.IGNORECASE)
_BASE_NUMBER_RE = re.compile(r"(\d{1,4})")


def _base_number(raw_number: str) -> int:
    """Integer value of the leading base number (0 when unparseable)."""
    match = _BASE_NUMBER_RE.match(raw_number or "")
    return int(match.group(1)) if match else 0


def is_line_start(text: str, offset: int) -> bool:
    """True when only whitespace precedes *offset* on its line."""
    line_start = text.rfind("\n", 0, offset) + 1
    return not text[line_start:offset].strip()


def _line_fragment(text: str, start: int) -> str:
    """The remainder of the line beginning at *start* (bounded to 200 chars)."""
    line_end = text.find("\n", start)
    end = line_end if line_end != -1 else len(text)
    return text[start : min(end, start + 200)]


def title_score(text: str, match_end: int) -> float:
    """Fraction of capitalized words in the title-shaped line fragment.

    The fragment is cut at the first em-dash (``NN. Title.—``), which marks the
    end of a statutory margin title.  Sentence-case statutory titles score
    modestly; that is intentional — the feature is a supporting signal, not a
    decision on its own.
    """
    fragment = _line_fragment(text, match_end)
    fragment = re.split(r"[—]", fragment, maxsplit=1)[0]
    words = fragment.split()
    if not words:
        return 0.0
    capitalized = sum(1 for word in words if word[:1].isupper())
    return round(capitalized / len(words), 4)


def _is_blank_line_before(text: str, offset: int) -> float:
    """1.0 when the line immediately above the candidate is blank.

    Statutory headers are almost always preceded by a blank line; a
    mid-paragraph cross-reference almost never is.
    """
    line_start = text.rfind("\n", 0, offset) + 1
    if line_start == 0:
        return 0.0
    previous_end = line_start - 1  # the newline that ends the previous line
    previous_start = text.rfind("\n", 0, previous_end) + 1
    return 1.0 if not text[previous_start:previous_end].strip() else 0.0


def _preceded_by_period(text: str, offset: int) -> float:
    """1.0 when the nearest non-space character before the candidate is a period."""
    index = offset - 1
    while index >= 0 and text[index] in " \t":
        index -= 1
    return 1.0 if index >= 0 and text[index] == "." else 0.0


def extract_features(
    text: str,
    candidate: BoundaryCandidate,
    *,
    act_name: str | None = None,
    prev_accepted: int | None = None,
    first_occurrence: bool = True,
    sections_covered: set[str] | None = None,
    engine_confidence: float = 0.0,
) -> dict[str, float]:
    """Build the numeric feature dict for one candidate.

    Args:
        text: The full ordered document text the candidate offset indexes.
        candidate: The boundary candidate to featurize.
        act_name: Owning Act name, used for the fail-closed range check.
        prev_accepted: Base number of the previously accepted boundary, if any.
        first_occurrence: Whether this is the first candidate for its number.
        sections_covered: Payload ``sections_covered`` for the candidate's chunk.
        engine_confidence: Engine-stamped confidence, when available.

    """
    number = _base_number(candidate.raw_number)
    known = is_known_section_for_act(str(number) if number else candidate.raw_number, act_name)
    in_act_range = 1.0 if known is True else -1.0 if known is False else 0.0

    line_start = is_line_start(text, candidate.char_offset)
    fragment = _line_fragment(text, candidate.match_end)
    window = text[max(0, candidate.char_offset - 200) : candidate.char_offset + 200]
    page_only = 1.0 if _line_fragment(text, candidate.char_offset).strip() == "" else 0.0
    if not line_start:
        page_only = 0.0

    delta = -1.0
    monotonic = 0.0
    if prev_accepted is not None and number:
        diff = number - prev_accepted
        delta = float(diff)
        monotonic = 1.0 if diff == 1 else 0.0

    return {
        "is_line_start": 1.0 if line_start else 0.0,
        "in_act_range": in_act_range,
        "source_engine_main": 1.0 if candidate.source_pattern == "engine_main" else 0.0,
        "source_engine_word": 1.0 if candidate.source_pattern == "engine_word" else 0.0,
        "source_dotted_clause": 1.0 if candidate.source_pattern == "dotted_clause" else 0.0,
        "source_l4_header": 1.0 if candidate.source_pattern == "l4_header" else 0.0,
        "is_dotted": 1.0 if candidate.grammar_type == "dotted" else 0.0,
        "number_value": float(number),
        "is_year_like": 1.0 if 1900 <= number <= 2100 else 0.0,
        "delta_from_prev_accepted": delta,
        "is_monotonic": monotonic,
        "title_score": title_score(text, candidate.match_end),
        "has_emdash_title": 1.0 if "—" in fragment else 0.0,
        "crossref_density": float(len(_CROSSREF_RE.findall(window))),
        "page_number_only": page_only,
        "is_first_occurrence": 1.0 if first_occurrence else 0.0,
        "sections_covered_agree": 1.0 if sections_covered and str(number) in sections_covered else 0.0,
        "engine_confidence": float(engine_confidence),
        "is_blank_line_before": _is_blank_line_before(text, candidate.char_offset),
        "preceded_by_period": _preceded_by_period(text, candidate.char_offset),
        "is_subsection_header": 1.0 if re.match(r"\s*\(", text[candidate.match_end : candidate.match_end + 4]) else 0.0,
        "title_word_count": float(len(re.split(r"[—]", fragment, maxsplit=1)[0].split())),
        "is_short_title": 1.0 if 1 <= len(re.split(r"[—]", fragment, maxsplit=1)[0].split()) <= 12 else 0.0,
        "offset_ratio": round(candidate.char_offset / len(text), 6) if text else 0.0,
        "abs_delta": min(abs(delta), 50.0) if delta >= 0 else 0.0,
    }


def feature_vector(features: dict[str, float]) -> list[float]:
    """Ordered vector for the Tier-2 model (stable ``FEATURE_NAMES`` order)."""
    return [float(features.get(name, 0.0)) for name in FEATURE_NAMES]

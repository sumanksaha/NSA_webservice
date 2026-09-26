"""Step 3 — multi-stage reference-quality and legal-aware fill gate (deterministic).

Motivation (plan sec 5.2 Step 3, human review 2026-09-26): the original fill
proposal rule was a single lexical margin::

    candidate_overlap > best_frozen_overlap + 0.05

High overlap can be produced by short/common words, form-field labels
(``Area:``, ``Designation:``), page/header fragments, OCR debris, headings
without body text, or legally genuine but semantically irrelevant provisions.
11 of the 14 first-round proposals were human-rejected for exactly these
reasons. This module replaces the single margin with a layered gate while
keeping the margin as ONE component, never as the whole decision.

Pipeline (all stages deterministic — 0 model calls):

    raw overlap / candidate retrieval   (step3_gated_generation.find_ref_fill)
        ↓
    reference quality filtering          evaluate_reference_quality()
        ↓
    metadata / OCR / fragment filtering evaluate_reference_quality()
        ↓
    legal identifier extraction          extract_legal_identifiers()
        ↓
    legal identifier consistency         gate_fill_candidate()
        ↓
    semantic relevance assessment        gate_fill_candidate()
        ↓
    frozen-vs-candidate margin           gate_fill_candidate()
        ↓
    decision REJECT | REVIEW | PASS + reason codes
        ↓
    generation only after explicit human approval (step3_gated_generation)

Decision contract
-----------------
REJECT  contaminated, fragmented, metadata-only, OCR debris, no semantic
        anchor, or a clear legal-instrument conflict. Never generates, even
        if a human tries to approve it (the CLI refuses).
REVIEW  potentially useful but semantic/legal relationship is uncertain.
        Never generates: an explicit human approval is recorded, but the
        plan contract (sec 11) allows generation only for PASS proposals —
        REVIEW is an invitation to inspect, not to run.
PASS    strong substantive reference with adequate semantic and legal
        consistency and a sufficient frozen-vs-candidate margin. Generates
        only after explicit human approval (both conditions required:
        gate PASS AND human approved).

The frozen scorer (`experiment_b_topk_eval.token_overlap`) is NOT modified;
its raw overlap is consumed here as one input among several.

Reason codes are machine-readable, UPPER_SNAKE, and always present (list may
be empty only for the impossible case of a perfect candidate — in practice
PASS records still carry informative codes like IDENTIFIER_MATCH/MARGIN_OK).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# Decision vocabulary
# --------------------------------------------------------------------------- #

REJECT = "REJECT"
REVIEW = "REVIEW"
PASS = "PASS"  # noqa: S105 -- decision vocabulary, not a secret

# Hard-reject reason codes
METADATA_FRAGMENT = "METADATA_FRAGMENT"
OCR_FRAGMENT = "OCR_FRAGMENT"
HEADING_WITHOUT_BODY = "HEADING_WITHOUT_BODY"
FRAGMENT_TOO_SHORT = "FRAGMENT_TOO_SHORT"
PUNCTUATION_OR_NUMBER_ONLY = "PUNCTUATION_OR_NUMBER_ONLY"
NO_SEMANTIC_ANCHOR = "NO_SEMANTIC_ANCHOR"
INSTRUMENT_CONFLICT = "INSTRUMENT_CONFLICT"
QUALITY_SCORE_LOW = "QUALITY_SCORE_LOW"
#: raw overlap far exceeds stopword-free reference overlap while the content
#: anchor is weak -> the margin was manufactured by common tokens
OVERLAP_INFLATED_BY_GENERIC_TOKENS = "OVERLAP_INFLATED_BY_GENERIC_TOKENS"

# Soft (REVIEW-capping / explanatory) reason codes
WEAK_SEMANTIC_ANCHOR = "WEAK_SEMANTIC_ANCHOR"
SECTION_MISMATCH = "SECTION_MISMATCH"
HIGH_COMMON_TOKEN_RATIO = "HIGH_COMMON_TOKEN_RATIO"  # noqa: S105
MARGIN_FAIL = "MARGIN_FAIL"
SHORT_MEANINGFUL_PROVISION = "SHORT_MEANINGFUL_PROVISION"

# Positive / informational codes
IDENTIFIER_PRESENT = "IDENTIFIER_PRESENT"
IDENTIFIER_MATCH = "IDENTIFIER_MATCH"
SECTION_MATCH = "SECTION_MATCH"
INSTRUMENT_MATCH = "INSTRUMENT_MATCH"
MARGIN_OK = "MARGIN_OK"
SEMANTIC_ANCHOR_OK = "SEMANTIC_ANCHOR_OK"

DECISIONS = (REJECT, REVIEW, PASS)

# --------------------------------------------------------------------------- #
# Thresholds (documented, deterministic; calibrated against the 14 first-round
# proposals which serve as a negative-control set — see tests)
# --------------------------------------------------------------------------- #

#: candidate must add this much raw overlap over the best frozen chunk
MARGIN = 0.05
#: substance volume: meaningful content tokens needed for a full score
SUBSTANTIVE_TOKENS = 8
#: PASS floor on reference_quality_score
QUALITY_PASS_FLOOR = 0.55
#: PASS floor on semantic anchor (max coverage of question / reference)
SEMANTIC_PASS_FLOOR = 0.25
#: below this the candidate has no semantic anchor at all -> REJECT
SEMANTIC_REJECT_CEILING = 0.05
#: PASS floor on semantic anchor (max coverage of question / reference)
#: (declared here; PASS rules above use this name)
#: the raw frozen-scorer overlap minus stopword-free reference overlap shows
#: how much of the reported similarity is generic-token inflation. Above this
#: ceiling, with a weak content anchor, the overlap is boilerplate-driven.
INFLATION_CEILING = 0.40
#: anchor ceiling paired with the inflation rule (both must hold)
INFLATION_ANCHOR_CEILING = 0.15
#: common-token (stopword/generic) ratio above which quality is penalized.
#: Calibrated so natural legal prose (reference sentences sit near 0.45) is not
#: flagged while boilerplate plateaus ("the notice shall ... as prescribed
#: under the rules") lands near 0.65.
COMMON_RATIO_PENALTY_START = 0.60
#: a candidate this short without a legal identifier is a fragment
FRAGMENT_MAX_WORDS = 3
#: content words above which a colon-label is not a lone field label
LABEL_MAX_WORDS = 4

# --------------------------------------------------------------------------- #
# Stopwords + generic legal filler (used for meaningful-token counting and the
# semantic anchor; deliberately broader than plain English stopwords so that
# "the Board:" style fragments score low)
# --------------------------------------------------------------------------- #

STOPWORDS: set[str] = {
    "a", "an", "the", "and", "or", "nor", "but", "if", "then", "else", "of",
    "to", "in", "on", "at", "by", "for", "with", "without", "from", "into",
    "onto", "up", "down", "out", "over", "under", "about", "against", "between",
    "is", "are", "was", "were", "be", "been", "being", "am", "do", "does",
    "did", "done", "have", "has", "had", "will", "would", "shall", "should",
    "may", "might", "must", "can", "could", "it", "its", "this", "that",
    "these", "those", "there", "here", "he", "she", "they", "them", "his",
    "her", "their", "we", "you", "your", "our", "i", "not", "no", "yes",
    "as", "so", "than", "too", "very", "also", "any", "all", "each", "every",
    "some", "such", "only", "own", "same", "other", "another", "more", "most",
    "both", "few", "many", "much", "due", "via", "per", "etc", "ie", "eg",
    "within", "where", "when", "while", "who", "whom", "which", "what", "how",
    "above", "before", "after", "during", "until",
    # generic legal filler that carries almost no discriminating content
    "act", "rule", "rules", "section", "sections", "subsection", "sub",
    "clause", "clauses", "schedule", "schedules", "form", "provision",
    "provisions", "chapter", "part", "article", "notification", "order",
    "regulation", "regulations", "hereby", "thereof", "therein", "aforesaid",
    "said", "anyone", "person", "persons", "authority", "board", "commission",
    "government", "state", "central", "prescribed", "provided", "subject",
    "accordance", "purpose", "purposes", "case", "cases", "matter", "matters",
    "day", "days", "period", "time", "times", "number", "numbers",
}

#: Field labels typical of forms/metadata. NEVER the sole basis for rejection
#: (structural checks below are primary) — used to corroborate.
METADATA_FIELD_NAMES: set[str] = {
    "area", "designation", "time", "capacity", "from", "to", "date", "name",
    "address", "corporation", "district", "ward", "phone", "fax", "email",
    "signature", "place", "shift", "grade", "scale", "code", "status",
    "reference", "remarks", "note", "notes", "page", "serial", "sr", "sl",
    "total", "amount", "quantity", "value", "rate", "period from", "period to",
}

# --------------------------------------------------------------------------- #
# Text primitives
# --------------------------------------------------------------------------- #

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9()'’\-]*")
#: "Area:", "Designation :", lone or leading colon-terminated label
_LABEL_RE = re.compile(r"^\s*([A-Za-z][A-Za-z ]{0,30})\s*:\s*(.*)$")
_PAGE_MARKER_RE = re.compile(
    r"(^\s*page\s+\d+(\s*(of|/)\s*\d+)?\s*$)|(\b\d{1,3}\s*/\s*\d{1,3}\b)",
    re.IGNORECASE | re.MULTILINE,
)
_HEADING_PREFIX_RE = re.compile(
    r"^\s*(part|chapter|section|schedule|form|annexure|appendix|annex)\b",
    re.IGNORECASE,
)
_PUNCT_RUN_RE = re.compile(r"([^\w\s])\1{2,}")


def words(text: str) -> list[str]:
    """All word-ish tokens, lowercased, preserving order."""
    return [w.lower() for w in _WORD_RE.findall(str(text or ""))]


def meaningful_tokens(text: str) -> list[str]:
    """Content tokens: non-stopword, non-trivial (drops 'a', 'the', 'of'...)."""
    out = []
    for w in words(text):
        if w in STOPWORDS:
            continue
        if len(w) == 1 and not w.isdigit():
            continue
        out.append(w)
    return out


def content_overlap(query: str, text: str) -> float:
    """Stopword-free coverage of query content tokens in text ∈ [0, 1].

    Unlike the frozen scorer's ``token_overlap`` this ignores function words,
    so shared boilerplate ('the', 'and', 'not', 'under') cannot fake relevance.
    """
    q = set(meaningful_tokens(query))
    if not q:
        return 0.0
    c = set(meaningful_tokens(text))
    return len(q & c) / len(q)


# --------------------------------------------------------------------------- #
# Legal identifier extraction (uses the project's own instrument vocabulary)
# --------------------------------------------------------------------------- #

_PROVISION_PATTERNS: dict[str, re.Pattern] = {
    "section": re.compile(r"\b(?:sections?|sec\.?|s)\.?\s*(\d{1,4})(?:\s*\(([0-9a-zA-Z]{1,6})\))?", re.IGNORECASE),
    "rule": re.compile(r"\brules?\s+(\d{1,4})(?:\s*\(([0-9a-zA-Z]{1,6})\))?", re.IGNORECASE),
    "regulation": re.compile(r"\bregulations?\s+(\d{1,4})", re.IGNORECASE),
    "clause": re.compile(r"\bclauses?\s+(\d{1,4})(?:\s*\(([0-9a-zA-Z]{1,6})\))?", re.IGNORECASE),
    "sub_clause": re.compile(r"\bsub[- ]clauses?\s+(\d{1,4})\s*\(([0-9a-zA-Z]{1,6})\)", re.IGNORECASE),
    "schedule": re.compile(r"\bschedule\s+([ivxlcdm]+|\d{1,3})\b", re.IGNORECASE),
    "form": re.compile(r"\bform\s+([a-z]|\d{1,2})\b", re.IGNORECASE),
    "notification": re.compile(r"\bnotification\s+(?:no\.?|number)?\s*([A-Za-z0-9/\-]{2,20})", re.IGNORECASE),
    "order": re.compile(r"\borders?\s+(\d{1,4})", re.IGNORECASE),
    "provision": re.compile(r"\bprovisions?\s+(\d{1,4})", re.IGNORECASE),
    "chapter": re.compile(r"\bchapters?\s+([ivxlcdm]+|\d{1,3})\b", re.IGNORECASE),
    "part": re.compile(r"\bparts?\s+([ivxlcdm]+|\d{1,3})\b", re.IGNORECASE),
    "article": re.compile(r"\barticles?\s+(\d{1,4})", re.IGNORECASE),
}
#: provision types that anchor section-consistency checks
_SECTION_LIKE = ("section", "rule", "clause", "sub_clause", "regulation", "article")


@dataclass
class LegalIdentifiers:
    provisions: dict[str, list[str]] = field(default_factory=dict)
    instruments: list[str] = field(default_factory=list)  # family ids

    @property
    def sections(self) -> list[str]:
        """Normalized section-like numbers (base number before any paren)."""
        out: list[str] = []
        for kind in _SECTION_LIKE:
            for raw in self.provisions.get(kind, []):
                base = re.match(r"(\d{1,4})", raw)
                if base and base.group(1) not in out:
                    out.append(base.group(1))
        return out

    @property
    def present(self) -> bool:
        return bool(self.provisions) or bool(self.instruments)


def _instrument_hits(text: str, fam_map: Any) -> list[str]:
    """Family ids whose act name / curated alias appears in ``text``.

    Uses the project's own vocabulary (``FamilyMap`` registry acts +
    ``_FAMILY_ALIASES``) — no new taxonomy is invented. Longest alias first so
    the most specific instrument wins when several match.
    """
    if not text or fam_map is None:
        return []
    low = str(text).lower()
    hits: list[str] = []
    for family, alias in getattr(fam_map, "alias_list", []):
        if family in hits:
            continue
        if alias and alias in low:
            hits.append(family)
    for family, acts in getattr(fam_map, "family_to_acts", {}).items():
        if family in hits:
            continue
        for act in acts:
            norm = re.sub(r"[^a-z0-9 ]", " ", str(act).lower())
            norm = re.sub(r"\s+", " ", norm).strip()
            if len(norm) >= 10 and norm in re.sub(r"\s+", " ", low):
                hits.append(family)
                break
    return hits


def extract_legal_identifiers(
    text: str, fam_map: Any = None, payload: dict | None = None
) -> LegalIdentifiers:
    """Extract provision numbers and legal instruments from text.

    ``payload`` (optional) adds the chunk's own stamped identity
    (``act_name``/``document_title``/``document_type``) — corpus metadata, not
    a new taxonomy.
    """
    t = str(text or "")
    ids = LegalIdentifiers()
    for kind, pat in _PROVISION_PATTERNS.items():
        found = ["+".join(p for p in m.groups() if p) for m in pat.finditer(t)]
        if found:
            ids.provisions[kind] = found
    ids.instruments = _instrument_hits(t, fam_map)
    if payload:
        for key in ("act_name", "document_title"):
            for fam in _instrument_hits(str(payload.get(key) or ""), fam_map):
                if fam not in ids.instruments:
                    ids.instruments.append(fam)
    return ids


# --------------------------------------------------------------------------- #
# Stage: reference quality (metadata / OCR / heading / fragment / substance)
# --------------------------------------------------------------------------- #

def detect_metadata_fragment(text: str) -> tuple[bool, str]:
    """Form-field / metadata structural detection → (flag, reason).

    Structural first (colon-label syntax, label:value line density, low
    substance), field-name list only corroborates — never the sole basis.
    """
    raw = str(text or "").strip()
    if not raw:
        return True, "empty candidate"
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    words_l = words(raw)
    mean = meaningful_tokens(raw)
    single = len(words_l) <= LABEL_MAX_WORDS

    # lone label: "Area" / "Area:" / "Area: value" with almost no content
    for ln in lines[:3]:
        m = _LABEL_RE.match(ln)
        if not m:
            continue
        label = m.group(1).strip().lower()
        value = (m.group(2) or "").strip()
        value_mean = meaningful_tokens(value)
        if label in METADATA_FIELD_NAMES and len(value_mean) <= 2:
            return True, f"form-field label '{m.group(1).strip()}:' with no substantive value"
        if not value and len(words(ln)) <= 2:
            return True, f"colon-terminated field label '{m.group(1).strip()}:' (no body text)"
        if label not in METADATA_FIELD_NAMES and not value_mean and len(words(label.split())) <= 2:
            return True, f"colon-terminated label '{m.group(1).strip()}:' (no body text)"

    # label:value density: most lines are key/value pairs with thin values
    labeled = [ln for ln in lines if _LABEL_RE.match(ln)]
    if lines and len(labeled) / len(lines) >= 0.5 and len(mean) <= 6:
        return True, "dominant label:value metadata layout with little content"

    if single and words_l and words_l[0] in METADATA_FIELD_NAMES:
        return True, f"single metadata field '{words_l[0]}'"
    if len(lines) == 1 and len(mean) <= 2 and raw.endswith(":"):
        return True, "lone colon-terminated label"
    return False, ""


def detect_ocr_fragment(text: str) -> tuple[bool, str]:
    """Page furniture / OCR debris → (flag, reason)."""
    raw = str(text or "")
    if not raw.strip():
        return True, "empty candidate"
    if _PAGE_MARKER_RE.search(raw):
        return True, "page marker / folio fragment"
    if "\ufffd" in raw or "\x0c" in raw:
        return True, "replacement/control characters (OCR artifact)"
    if _PUNCT_RUN_RE.search(raw):
        return True, "repeated punctuation run (OCR artifact)"
    stripped = re.sub(r"[\s\w]", "", raw)
    if len(raw) >= 8 and len(stripped) / len(raw) > 0.45:
        return True, "non-alphanumeric character density > 45% (OCR debris)"
    digits = sum(ch.isdigit() for ch in raw)
    if len(raw) >= 6 and digits / len(raw) > 0.5:
        return True, "majority-digit span (folio/table artifact)"
    return False, ""


def detect_heading_fragment(text: str, legal_ids: LegalIdentifiers | None) -> tuple[bool, str]:
    """Heading without substantive body → (flag, reason).

    A short heading that still carries a legal identifier AND substantive
    nouns ("Section 32 — Improvement notices") is NOT a fragment: the caller
    only hard-rejects headings that also fail substance/identifier checks.
    """
    raw = str(text or "").strip()
    if not raw:
        return True, "empty candidate"
    w = words(raw)
    mean = meaningful_tokens(raw)
    one_line = len(raw.splitlines()) <= 1
    has_ident = bool(legal_ids and legal_ids.present)
    structural = (
        (one_line and raw.upper() == raw and len(w) >= 2)
        or (_HEADING_PREFIX_RE.match(raw) is not None and len(w) <= 8)
        or (one_line and len(w) <= 6 and not raw.endswith((".", ";", ")")))
    )
    if structural and len(mean) <= 3 and not has_ident:
        return True, "heading/label without substantive body text"
    if structural and len(mean) <= 1:
        return True, "heading without substantive body text"
    return False, ""


def evaluate_reference_quality(
    candidate: str,
    question: str | None = None,
    frozen_reference: str | None = None,
    *,
    fam_map: Any = None,
    payload: dict | None = None,
) -> dict[str, Any]:
    """Structured reference-quality diagnostics for one candidate chunk.

    Returns (per plan §4) at minimum::

        reference_quality_score  candidate_length  word_count
        meaningful_token_count   common_token_ratio
        metadata_fragment        heading_fragment   ocr_fragment
        legal_identifier_present legal_identifier_match
        quality_flags

    plus explanation strings and component values. Deterministic; 0 LLM calls.
    """
    raw = str(candidate or "")
    w = words(raw)
    mean = meaningful_tokens(raw)
    flags: list[str] = []

    meta_flag, meta_reason = detect_metadata_fragment(raw)
    ids = extract_legal_identifiers(raw, fam_map=fam_map, payload=payload)
    ocr_flag, ocr_reason = detect_ocr_fragment(raw)
    head_flag, head_reason = detect_heading_fragment(raw, ids)

    common_ratio = 1.0 - (len(mean) / len(w)) if w else 1.0
    # Function-word-heavy is normal for legal prose, so a high ratio only
    # matters when there is little absolute substance — that is the fragment /
    # boilerplate signature, not the sentence signature.
    if (
        common_ratio > COMMON_RATIO_PENALTY_START
        and len(w) >= 4
        and len(mean) < SUBSTANTIVE_TOKENS
    ):
        flags.append(HIGH_COMMON_TOKEN_RATIO)

    # identifier match vs question / reference identifiers
    q_ids = extract_legal_identifiers(question or "", fam_map=fam_map) if question else None
    legal_identifier_present = ids.present
    legal_identifier_match: bool | None = None
    if q_ids is not None and q_ids.present and ids.present:
        q_sec, c_sec = set(q_ids.sections), set(ids.sections)
        q_ins, c_ins = set(q_ids.instruments), set(ids.instruments)
        shared_sec = bool(q_sec & c_sec)
        shared_ins = bool(q_ins & c_ins)
        if shared_sec or shared_ins:
            legal_identifier_match = True
        elif (q_sec and c_sec) or (q_ins and c_ins):
            # comparable on at least one axis, but nothing is shared
            legal_identifier_match = False
        else:
            # both sides have identifiers, yet none are comparable kinds
            legal_identifier_match = None
    if legal_identifier_present:
        flags.append(IDENTIFIER_PRESENT)

    if meta_flag:
        flags.append(METADATA_FRAGMENT)
    if ocr_flag:
        flags.append(OCR_FRAGMENT)
    if head_flag:
        flags.append(HEADING_WITHOUT_BODY)
    if len(w) <= FRAGMENT_MAX_WORDS and not legal_identifier_present:
        flags.append(FRAGMENT_TOO_SHORT)
    if w and not mean and not legal_identifier_present:
        flags.append(PUNCTUATION_OR_NUMBER_ONLY)

    # substance volume ∈ [0, 1]
    substance = min(1.0, len(mean) / SUBSTANTIVE_TOKENS)
    # semantic anchor: best coverage of the question or the reference
    sem_q = content_overlap(question, raw) if question else 0.0
    sem_r = content_overlap(frozen_reference, raw) if frozen_reference else 0.0
    anchor = max(sem_q, sem_r)

    score = (
        0.45 * substance
        + 0.30 * anchor
        + 0.15 * max(0.0, 1.0 - common_ratio)
        + 0.10 * (1.0 if legal_identifier_present else 0.0)
        - 0.50 * (1.0 if meta_flag else 0.0)
        - 0.40 * (1.0 if ocr_flag else 0.0)
        - 0.25 * (1.0 if head_flag else 0.0)
    )
    score = max(0.0, min(1.0, score))

    return {
        "reference_quality_score": round(score, 4),
        "candidate_length": len(raw),
        "word_count": len(w),
        "meaningful_token_count": len(mean),
        "common_token_ratio": round(common_ratio, 4),
        "metadata_fragment": meta_flag,
        "metadata_reason": meta_reason,
        "heading_fragment": head_flag,
        "heading_reason": head_reason,
        "ocr_fragment": ocr_flag,
        "ocr_reason": ocr_reason,
        "legal_identifier_present": legal_identifier_present,
        "legal_identifier_match": legal_identifier_match,
        "legal_provisions": dict(ids.provisions),
        "legal_instruments": list(ids.instruments),
        "semantic_overlap_question": round(sem_q, 4),
        "semantic_overlap_reference": round(sem_r, 4),
        "semantic_anchor": round(anchor, 4),
        "quality_flags": flags,
    }


# --------------------------------------------------------------------------- #
# Stage: legal identifier consistency + full gate
# --------------------------------------------------------------------------- #

def gate_fill_candidate(
    candidate: str,
    *,
    question: str = "",
    frozen_reference: str = "",
    raw_overlap: float = 0.0,
    best_frozen_overlap: float = 0.0,
    fam_map: Any = None,
    payload: dict | None = None,
    question_families: Iterable[str] = (),
    reference_families: Iterable[str] = (),
    payload_families: Iterable[str] = (),
) -> dict[str, Any]:
    """Full multi-stage gate for one candidate → decision + reason codes.

    ``raw_overlap`` / ``best_frozen_overlap`` are the frozen scorer's
    ``token_overlap`` values (candidate-vs-reference and best-frozen-vs-
    reference) so the original margin rule is retained as a component.
    ``*_families`` are benchmark family ids (gold units / payload stamps) —
    the project's existing instrument vocabulary.
    """
    q = evaluate_reference_quality(
        candidate, question or None, frozen_reference or None,
        fam_map=fam_map, payload=payload,
    )
    # recompute identifiers for the candidate (evaluate_* keeps its output
    # JSON-pure: no internal handles that would break persisted proposals)
    ids = extract_legal_identifiers(candidate, fam_map=fam_map, payload=payload)
    codes: list[str] = []

    # ---- question-side identifiers (question text + reference + gold units)
    q_text_ids = extract_legal_identifiers(
        f"{question} {frozen_reference}", fam_map=fam_map
    )
    q_sections = set(q_text_ids.sections)
    for fam in set(question_families) | set(reference_families):
        if fam not in q_text_ids.instruments:
            q_text_ids.instruments.append(fam)
    q_instruments = set(q_text_ids.instruments)
    c_sections = set(ids.sections)
    c_instruments = set(ids.instruments) | set(payload_families)

    # Consistency is POSITIVE evidence only: a side that carries no
    # identifiers is unknown (None), never a vacuous "match" — otherwise a
    # candidate with zero identifiers would be reported as identifier_match
    # True and could earn IDENTIFIER_PRESENT it does not have (review 2026-09-26).
    shared_sec = bool(q_sections & c_sections)
    shared_ins = bool(q_instruments & c_instruments)
    instrument_match = shared_ins if (q_instruments and c_instruments) else None
    instrument_conflict = bool(q_instruments and c_instruments) and not shared_ins
    if shared_sec or shared_ins:
        identifier_match = True
    elif (q_sections and c_sections) or (q_instruments and c_instruments):
        # comparable on at least one axis with nothing shared -> explicit False
        identifier_match = False
    else:
        identifier_match = None  # not assessable (a side has no identifiers)
    section_mismatch = bool(q_sections and c_sections) and not shared_sec

    # ---- margin (original rule, retained as one component)
    margin_ok = raw_overlap > (best_frozen_overlap + MARGIN)

    sem = q["semantic_anchor"]
    quality = q["reference_quality_score"]

    # ================= REJECT conditions (hard) =================
    if q["metadata_fragment"]:
        codes.append(METADATA_FRAGMENT)
    if q["ocr_fragment"]:
        codes.append(OCR_FRAGMENT)
    if q["heading_fragment"]:
        codes.append(HEADING_WITHOUT_BODY)
    if FRAGMENT_TOO_SHORT in q["quality_flags"]:
        codes.append(FRAGMENT_TOO_SHORT)
    if PUNCTUATION_OR_NUMBER_ONLY in q["quality_flags"]:
        codes.append(PUNCTUATION_OR_NUMBER_ONLY)
    if instrument_conflict:
        codes.append(INSTRUMENT_CONFLICT)

    hard = [
        METADATA_FRAGMENT, OCR_FRAGMENT, HEADING_WITHOUT_BODY,
        FRAGMENT_TOO_SHORT, PUNCTUATION_OR_NUMBER_ONLY, INSTRUMENT_CONFLICT,
        OVERLAP_INFLATED_BY_GENERIC_TOKENS,
    ]
    hard_hit = [c for c in hard if c in codes]

    # no shared content word with question AND reference -> irrelevant text
    if sem <= SEMANTIC_REJECT_CEILING:
        codes.append(NO_SEMANTIC_ANCHOR)
        hard_hit.append(NO_SEMANTIC_ANCHOR)

    # generic-token inflation: the frozen scorer's raw overlap counts function
    # words, so 'the Board:' / 'under' / 'no' can clear the margin. If most of
    # the reported similarity is NOT backed by stopword-free reference content
    # and the content anchor is weak, the overlap is boilerplate-driven.
    inflation = float(raw_overlap) - q["semantic_overlap_reference"]
    if inflation >= INFLATION_CEILING and sem < INFLATION_ANCHOR_CEILING:
        codes.append(OVERLAP_INFLATED_BY_GENERIC_TOKENS)
        hard_hit.append(OVERLAP_INFLATED_BY_GENERIC_TOKENS)

    if hard_hit and quality < QUALITY_PASS_FLOOR:
        codes.append(QUALITY_SCORE_LOW)

    decision: str
    if hard_hit:
        decision = REJECT
    else:
        # ============== REVIEW / PASS conditions ==============
        if HIGH_COMMON_TOKEN_RATIO in q["quality_flags"]:
            codes.append(HIGH_COMMON_TOKEN_RATIO)
        if section_mismatch:
            codes.append(SECTION_MISMATCH)
        if sem < SEMANTIC_PASS_FLOOR:
            codes.append(WEAK_SEMANTIC_ANCHOR)
        if not margin_ok:
            codes.append(MARGIN_FAIL)
        else:
            codes.append(MARGIN_OK)
        if sem >= SEMANTIC_PASS_FLOOR:
            codes.append(SEMANTIC_ANCHOR_OK)
        # positive evidence only: shared section numbers earn MATCH; a
        # candidate carrying identifiers that do not match earns PRESENT;
        # a candidate with no identifiers earns neither.
        if shared_sec:
            codes.append(IDENTIFIER_MATCH)
        elif c_sections or c_instruments:
            codes.append(IDENTIFIER_PRESENT)
        if q_sections and c_sections and (q_sections & c_sections):
            codes.append(SECTION_MATCH)
        if q_instruments and c_instruments and (q_instruments & c_instruments):
            codes.append(INSTRUMENT_MATCH)
        if q["word_count"] <= 6 and q["meaningful_token_count"] >= 2 and q["legal_identifier_present"]:
            codes.append(SHORT_MEANINGFUL_PROVISION)

        pass_ok = (
            quality >= QUALITY_PASS_FLOOR
            and sem >= SEMANTIC_PASS_FLOOR
            and margin_ok
            and not section_mismatch
            and HIGH_COMMON_TOKEN_RATIO not in q["quality_flags"]
        )
        decision = PASS if pass_ok else REVIEW

    # de-duplicate preserving order
    seen: set[str] = set()
    codes = [c for c in codes if not (c in seen or seen.add(c))]

    return {
        "decision": decision,
        "reason_codes": codes,
        "reference_quality_score": quality,
        "semantic_anchor": sem,
        "raw_overlap": round(float(raw_overlap), 4),
        "best_frozen_overlap": round(float(best_frozen_overlap), 4),
        "overlap_inflation": round(inflation, 4),
        "margin_ok": margin_ok,
        "question_sections": sorted(q_sections),
        "candidate_sections": sorted(c_sections),
        "question_act": sorted(q_instruments),
        "candidate_act": sorted(c_instruments),
        "identifier_match": identifier_match,
        "instrument_match": instrument_match,
        "identifier_conflict": instrument_conflict,
        "section_mismatch": section_mismatch,
        "quality": q,
    }


def gate_fill_proposal(candidate_gates: list[dict]) -> dict[str, Any]:
    """qid-level gate from its per-candidate gates: best candidate wins."""
    if not candidate_gates:
        return {"decision": REVIEW, "reason_codes": ["NO_CANDIDATE"], "n": 0}
    order = {PASS: 2, REVIEW: 1, REJECT: 0}
    best = max(candidate_gates, key=lambda g: order.get(g["decision"], 0))
    decisions = [g["decision"] for g in candidate_gates]
    return {
        "decision": best["decision"],
        "reason_codes": list(best["reason_codes"]),
        "n": len(candidate_gates),
        "n_pass": decisions.count(PASS),
        "n_review": decisions.count(REVIEW),
        "n_reject": decisions.count(REJECT),
        "best_candidate_index": candidate_gates.index(best),
    }

"""Phase 3 — Deterministic enrichment for existing FSSAI chunks (zero LLM).

Pure functions (no Flask / no DB / no network) that build the v1.0
enrichment record from an existing Qdrant point payload.  Everything here
is rule-based and provenance-tagged ``source=deterministic``; legal values
that cannot be established deterministically are left as ``unknown`` rather
than guessed (task Phase 3 / hallucination guardrails).

Key capability — **section attribution by paragraph inheritance**:
75 % of the corpus lacks ``section_number`` on text chunks, but 24.9 % of
chunks are header-bearing.  :func:`enrich_document_chunks` processes one
document's chunks in ``chunk_index`` order, carrying the last seen
header's section forward to subsequent text chunks (document-local,
deterministic).  Only a chunk that *itself* looks like a header may claim a
new section — a body reference ("subject to section 32") never does.

Cross-references are *candidates* here (``resolved: false``); chunk-level
resolution happens in :func:`resolve_cross_references` once a corpus-wide
``(document_id, section) -> chunk_id`` index exists (Phase 6 first pass).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any

# --------------------------------------------------------------------------- #
# Versioning / corpus constants
# --------------------------------------------------------------------------- #

ENRICHMENT_VERSION = "1.0"

#: The FSSAI corpus is the FSS Act family: acts, regulations, rules and
#: notifications all sit under the Food Safety and Standards Act, 2006.
FSS_ACT_NAME = "Food Safety and Standards Act, 2006"

#: Document types that inherit the FSS Act as their parent instrument.
_FSS_ACT_FAMILY = {"act", "regulation", "rule", "notification", "circular", "order"}

#: Common legal/English stopwords excluded from keyword extraction.
_STOPWORDS = frozenset(
    ["a", "an", "the", "and", "or", "of", "to", "in", "for", "on", "with", "shall", "may", "must", "not", "be", "is", "are", "was", "were", "as", "by", "from", "at", "this", "that", "these", "those", "any", "all", "each", "every", "such", "other", "than", "its", "his", "her", "their", "our", "your", "i", "we", "you", "he", "she", "it", "they", "them", "me", "us", "him", "her", "shall", "under", "over", "within", "into", "upon", "where", "when", "while", "which", "who", "whom", "whose", "provided", "subject", "accordance", "respect", "thereof", "therewith", "hereinbefore", "hereinafter", "notwithstanding", "pursuant", "aforesaid"]
)

# --------------------------------------------------------------------------- #
# Regexes (all case-insensitive; line-anchored for headers, unanchored for
# references so body text can be scanned for candidates).
# --------------------------------------------------------------------------- #

_HEADER_SECTION_RE = re.compile(
    r"^\s*(?:section|sec\.?|§)\s*(\d{1,4}[A-Za-z]?)\b\s*(?:[:\-—.]|\s*$)", re.IGNORECASE
)
_REF_SECTION_RE = re.compile(
    r"\b(?:section|sec\.?|sub-section|subsection)\s*(\d{1,4}[A-Za-z]?)\b", re.IGNORECASE
)
_SCHEDULE_RE = re.compile(
    r"\b(?:schedule|sch\.?)\s+(\d{1,3}|[ivxlcdm]+)\b", re.IGNORECASE
)
_ANNEXURE_RE = re.compile(
    r"\b(?:annexure|annex)\s+([A-Z]?\d{1,3}|[a-z])\b", re.IGNORECASE
)
_HEADWORD_RE = re.compile(r"\b[A-Z][A-Za-z]{2,}(?:\s+[A-Z][A-Za-z]{2,})?\b")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_KEYWORD_LOW_RE = re.compile(r"\b[a-z]{4,}\b")

#: Reference-y context triggers: a bare section mention only becomes a
#: cross-reference candidate when it appears in one of these legal phrasings
#: (mirrors the paragraph engine's citation extraction).
_REF_CONTEXT_RE = re.compile(
    r"(?:subject to|under|pursuant to|in accordance with|referred to in|as per|"
    r"by virtue of|notwithstanding|except as provided in|for the purposes of)\b",
    re.IGNORECASE,
)

#: General legal terminology likely to appear in user queries (FSSAI terms
#: kept as a subset — Phase 1 de-FSSAI: the keyword extractor now serves the
#: multi-domain corpus).
_LEGAL_TERMS = frozenset(
    ["fbo", "food", "business", "operator", "fssai", "licence", "license", "improvement", "notice", "adjudication", "adjudicating", "officer", "penalty", "offence", "compliance", "recall", "seizure", "sample", "inspection", "authority", "commissioner", "designated", "officer", "food", "safety", "officer", "laboratory", "analysis", "report", "appeal", "tribunal", "registration", "standards", "packaging", "labelling", "import", "export", "advertisement", "claims", "misbranded", "unsafe", "food", "quality", "safety", "hygiene", "sanitation", "act", "section", "rule", "regulation", "rules", "regulations", "notification", "order", "amendment", "repeal", "supersede", "enforce", "enforcement", "liability", "damages", "compensation", "contract", "breach", "consideration", "partnership", "firm", "company", "director", "shareholder", "winding", "insolvency", "arbitration", "limitation", "plaintiff", "defendant", "suit", "decree", "injunction", "specific", "performance", "pollution", "environment", "waste", "plastic", "water", "air", "emission", "consent", "board", "corporation", "municipal", "municipality", "tenancy", "tenant", "landlord", "livestock", "animal", "cruelty", "slaughter", "quarantine", "disease", "veterinary", "consumer", "goods", "services", "warranty", "defect", "unfair", "trade"]
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _val(value: Any, source: str = "deterministic", confidence: float = 0.9) -> dict | None:
    """Build a provenance-tagged value object (or None when value is empty)."""
    if value is None or value == "" or value == []:
        return None
    return {"value": value, "source": source, "confidence": round(float(confidence), 4)}


def _unknown() -> dict:
    return {"value": None, "source": "unknown"}


def sha256(text: str) -> str:
    """SHA-256 of normalized text (matches the deduper's content_hash)."""
    norm = re.sub(r"\s+", " ", (text or "")).strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Section attribution
# --------------------------------------------------------------------------- #


def _header_section_of(text: str) -> str | None:
    """Return the section number claimed by a header line, else None."""
    m = _HEADER_SECTION_RE.match(text or "")
    return m.group(1) if m else None


def _section_title_of(text: str) -> str | None:
    """Extract a title from ``Section N: Title``-style header text."""
    m = re.match(r"^\s*(?:section|sec\.?|§)\s*\d{1,4}[A-Za-z]?\s*[:\-—.]?\s*(.+)$", text or "", re.IGNORECASE)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return None


def attribute_sections(points: list[dict]) -> dict[str, dict]:
    """Attribute sections to a document's chunks via paragraph inheritance.

    Args:
        points: One document's point dicts (``{"id", "payload"}``), in any
            order (sorted by ``chunk_index`` internally).

    Returns:
        ``{chunk_id: {"section": str|None, "title": str|None, "inherited":
        bool}}``.
    """
    ordered = sorted(
        (p for p in points if isinstance(p.get("payload"), dict)),
        key=lambda p: int(p["payload"].get("chunk_index", 0) or 0),
    )
    result: dict[str, dict] = {}
    current_section: str | None = None
    current_title: str | None = None
    for p in ordered:
        pl = p["payload"]
        cid = str(pl.get("chunk_id") or p.get("id") or "")
        own = pl.get("section_number") or _header_section_of(pl.get("chunk_text", ""))
        title = pl.get("section_title") or _section_title_of(pl.get("chunk_text", ""))
        if own:
            current_section = str(own)
            if title:
                current_title = title
            result[cid] = {"section": current_section, "title": current_title or "", "inherited": False}
        elif current_section is not None:
            result[cid] = {"section": current_section, "title": current_title or "", "inherited": True}
        else:
            result[cid] = {"section": None, "title": None, "inherited": False}
    return result


# --------------------------------------------------------------------------- #
# Cross-reference candidates
# --------------------------------------------------------------------------- #


def extract_crossref_candidates(pl: dict) -> list[dict]:
    """Extract REFERS_TO candidates from payload citations/references + text.

    Returns a list of ``{"target": str, "section": str|None, "relation":
    "REFERS_TO", "resolved": False, "source": "deterministic", "evidence":
    str}``.  Resolution to chunk IDs happens later
    (:func:`resolve_cross_references`).
    """
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(section: str | None, evidence: str) -> None:
        if section is None:
            return
        key = (str(section), evidence[:80])
        if key in seen:
            return
        seen.add(key)
        candidates.append(
            {
                "target": f"Section {section}",
                "section": str(section),
                "relation": "REFERS_TO",
                "resolved": False,
                "source": "deterministic",
                "evidence": evidence[:160],
            }
        )

    for cite in pl.get("citations") or []:
        if isinstance(cite, dict):
            sec = cite.get("section")
            _add(str(sec) if sec is not None else None, str(cite.get("reference") or ""))
        elif isinstance(cite, str):
            m = _REF_SECTION_RE.search(cite)
            _add(m.group(1) if m else None, cite)
    for ref in pl.get("references") or []:
        if isinstance(ref, dict):
            tgt = ref.get("target") or ref.get("reference") or ""
            m = _REF_SECTION_RE.search(str(tgt))
            _add(m.group(1) if m else None, str(tgt))
        elif isinstance(ref, str):
            m = _REF_SECTION_RE.search(ref)
            _add(m.group(1) if m else None, ref)
    # Reference-context mentions in the body text.
    text = pl.get("chunk_text") or ""
    if _REF_CONTEXT_RE.search(text):
        for m in _REF_SECTION_RE.finditer(text):
            _add(m.group(1), text[max(0, m.start() - 40) : m.end() + 20])
    return candidates


def resolve_cross_references(
    candidates: list[dict],
    section_index: dict[tuple[str, str], list[Any]],
    document_id: str,
    act_document_id: str | None,
) -> list[dict]:
    """Resolve candidates to chunk IDs using a corpus section index.

    Args:
        candidates: From :func:`extract_crossref_candidates`.
        section_index: ``{(document_id, section_number): [(chunk_id,
            chunk_index), ...]}`` — items may also be plain chunk-id strings
            (tolerant of hand-built indexes in tests).
        document_id: The chunk's own document.
        act_document_id: The corpus's primary Act document id (or None).

    Resolution priority:
      1. Same-document chunk(s) with that section (intra-document reference).
         When a section spans several chunks (the norm), the *first* chunk in
         document order is the section's anchor (its header) — a certain
         provision match, so it resolves with a confidence penalty and an
         ``anchor: true`` marker rather than being discarded as "ambiguous".
      2. The Act document's chunk with that section (cross-document) — only
         when the same-document lookup found nothing; unique target resolves,
         multiple Act chunks stay unresolved (true ambiguity).
    Zero targets => ``resolved: False`` with no ambiguity claim.
    """
    def _ids(items: list[Any]) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for item in items:
            if isinstance(item, str):
                out.append((item, 0))
            else:
                out.append((str(item[0]), int(item[1] or 0)))
        return out

    resolved: list[dict] = []
    for cand in candidates:
        sec = cand.get("section")
        if not sec:
            resolved.append(cand)
            continue
        targets = _ids(section_index.get((document_id, sec), []))
        act_targets: list[tuple[str, int]] = []
        if not targets and act_document_id:
            act_targets = _ids(section_index.get((act_document_id, sec), []))
        if targets:
            # Same-document match is certain; anchor = first chunk in order.
            anchor = min(targets, key=lambda t: t[1])
            cand["resolved"] = True
            cand["target_chunk_id"] = anchor[0]
            cand["confidence"] = 0.7 if len(targets) > 1 else 0.95
            if len(targets) > 1:
                cand["anchor"] = True
        elif len(act_targets) == 1:
            cand["resolved"] = True
            cand["target_chunk_id"] = act_targets[0][0]
            cand["confidence"] = 0.95
        elif len(act_targets) > 1:
            cand["resolved"] = False
            cand["target_chunk_id"] = None
            cand["confidence"] = 0.5
            cand["ambiguous_targets"] = len(act_targets)
        else:
            cand["resolved"] = False
            cand["target_chunk_id"] = None
            cand["confidence"] = 0.5
        resolved.append(cand)
    return resolved


# --------------------------------------------------------------------------- #
# Keywords / structural flags / legal location
# --------------------------------------------------------------------------- #


def extract_keywords(text: str) -> list[str]:
    """Deterministic retrieval keywords: title-case headwords + FSSAI terms.

    Sparse by design — only terms actually present in the chunk text.
    """
    if not text:
        return []
    words: list[str] = []
    seen: set[str] = set()
    for m in _HEADWORD_RE.finditer(text):
        w = m.group(0)
        lower = w.lower()
        if lower in seen or lower in _STOPWORDS:
            continue
        seen.add(lower)
        words.append(w)
    for m in _KEYWORD_LOW_RE.finditer(text):
        w = m.group(0)
        if w.lower() in _LEGAL_TERMS and w.lower() not in seen:
            seen.add(w.lower())
            words.append(w)
    return words[:12]


def legal_act_of(pl: dict) -> str | None:
    """Resolve the parent Act for a chunk's ``legal_location.act``.

    Priority (Phase 1 — de-FSSAI):
      1. Explicit payload ``act_name`` (multi-domain manifest stamp).
      2. An ``act``-type document's own title (an Act is its own parent).
      3. The FSS Act family default — backward compatible for the existing
         FSSAI corpus whose payloads carry no ``act_name``.

    A subordinate instrument (regulation/rule/notification) from another
    domain without an explicit ``act_name`` yields ``None`` (unknown) rather
    than guessing a parent Act — unless the document is recognisably FSS
    (title/URI carries "food safety", "fssai"), which preserves the FSSAI
    corpus default.
    """
    explicit = pl.get("act_name")
    if explicit:
        cleaned = str(explicit).strip()
        return cleaned or None
    dtype = (pl.get("document_type") or "").lower()
    if dtype == "act":
        title = str(pl.get("document_title") or "").strip()
        if title:
            return re.sub(r"^(?:the|an|a)\s+", "", title, flags=re.IGNORECASE).strip() or None
    if dtype in _FSS_ACT_FAMILY and _looks_like_fss_document(pl):
        return FSS_ACT_NAME
    return None


def _looks_like_fss_document(pl: dict) -> bool:
    """Whether a document is recognisably part of the FSS Act family.

    Checks the title, document_id and URI for FSS markers — the FSSAI corpus
    is stamped with "Food Safety and Standards" titles, so this keeps the
    legacy default without mislabelling other domains' instruments.
    """
    haystack = " ".join(
        str(pl.get(key) or "")
        for key in ("document_title", "document_id", "document_uri")
    ).lower()
    return any(marker in haystack for marker in ("food safety", "fssai", "fss act"))


def legal_location_of(pl: dict, attributed: dict) -> dict:
    """Build the ``legal_location`` block (sparse, deterministic)."""
    act = legal_act_of(pl)
    section = attributed.get("section")
    subsection = pl.get("subsection")
    schedule = _SCHEDULE_RE.search(pl.get("chunk_text") or "")
    annexure = _ANNEXURE_RE.search(pl.get("chunk_text") or "")
    loc: dict[str, Any] = {}
    for key, value in (
        ("act", act),
        ("chapter", None),
        ("section", section),
        ("subsection", subsection),
        ("regulation", _locate_regulation(pl)),
        ("rule", None),
        ("schedule", schedule.group(1) if schedule else None),
        ("annexure", annexure.group(1) if annexure else None),
    ):
        loc[key] = _val(value) if value is not None else _unknown()
    return loc


def _locate_regulation(pl: dict) -> str | None:
    """Best-effort regulation number for regulation-family chunks.

    Uses the payload ``document_title``/``document_uri`` when it carries a
    regulation reference; otherwise None (no guessing from body text).
    """
    for field in ("document_title", "document_uri"):
        text = str(pl.get(field) or "")
        m = re.search(r"\bregulations?\s*,?\s*(\d{4})", text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return None


def structural_flags_of(pl: dict, attributed: dict) -> dict:
    """Structural/enrichment-target flags (from the audit vocabulary)."""
    text = pl.get("chunk_text") or ""
    stripped = text.strip()
    return {
        "empty": not stripped,
        "short": bool(stripped) and len(text) < 100,
        "long": len(text) > 3000,
        "has_section_metadata": bool(pl.get("section_number") or attributed.get("section")),
        "section_inherited": bool(attributed.get("inherited")),
        "has_citations": bool(pl.get("citations")),
        "has_references": bool(pl.get("references")),
        "has_entities": bool(pl.get("entities")),
        "chars": len(text),
        "words": len(text.split()),
    }


# --------------------------------------------------------------------------- #
# Record assembly
# --------------------------------------------------------------------------- #


def build_deterministic_record(
    point: dict,
    attributed: dict,
    crossref_candidates: list[dict],
) -> dict:
    """Assemble the deterministic v1.0 enrichment record for one chunk.

    ``retrieval_summary`` / ``legal_concepts`` / obligations & co. are LLM
    fields — left as ``unknown``/empty here (deterministic-only mode).
    """
    pl = point.get("payload") or {}
    cid = str(pl.get("chunk_id") or point.get("id") or "")
    text = pl.get("chunk_text") or ""
    flags = structural_flags_of(pl, attributed)
    keywords = extract_keywords(text)
    loc = legal_location_of(pl, attributed)

    return {
        "enrichment_version": ENRICHMENT_VERSION,
        "chunk_id": cid,
        "original_text": text,
        "original_sha256": pl.get("content_hash") or sha256(text),
        "status": "ENRICHED",
        "legal_document": {
            "title": _val(pl.get("document_title"), "existing_payload", 0.99),
            "document_type": _val(pl.get("document_type"), "existing_payload", 0.95),
            "authority": _val(pl.get("authority"), "existing_payload", 0.9),
            "jurisdiction": _val(pl.get("jurisdiction"), "existing_payload", 0.9),
            "effective_date": _val(pl.get("effective_date"), "existing_payload", 0.95),
            "status": _unknown(),
        },
        "legal_location": loc,
        "entities": [
            {"name": e, "type": "entity", "source": "existing_payload", "confidence": 0.7}
            for e in (pl.get("entities") or [])
        ],
        "legal_concepts": [],
        "obligations": [],
        "prohibitions": [],
        "permissions": [],
        "powers": [],
        "duties": [],
        "conditions": [],
        "exceptions": [],
        "offences": [],
        "penalties": [],
        "procedures": [],
        "cross_references": crossref_candidates,
        "applicability": [],
        "temporal_information": [
            _val(str(dt), "existing_payload", 0.95) for dt in
            (pl.get("effective_date"), pl.get("enactment_date"), pl.get("amended_date"))
            if dt
        ],
        "retrieval_keywords": keywords,
        "synonyms": [],
        "question_types": [],
        "retrieval_summary": _unknown(),
        "confidence": 0.7,
        "evidence_spans": [],
        "provenance": {
            "deterministic_pass": ENRICHMENT_VERSION,
            "llm_model": None,
            "llm_prompt_version": None,
            "llm_used": False,
            "processed_at": _now_iso(),
        },
        "_structural": flags,
        "_document": {"document_id": pl.get("document_id") or "", "document_uri": pl.get("document_uri") or ""},
    }


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Document-level orchestration (memory-bounded: one document at a time)
# --------------------------------------------------------------------------- #


def enrich_document(
    document_points: Iterable[dict],
    section_index: dict[tuple[str, str], list[str]],
    act_document_id: str | None,
) -> list[dict]:
    """Enrich one document's chunks (in chunk_index order).

    Args:
        document_points: The document's point dicts.
        section_index: Corpus-wide ``{(document_id, section): [chunk_id]}``.
        act_document_id: Primary Act document id (for cross-doc resolution).

    Returns:
        List of deterministic enrichment records (one per chunk).
    """
    points = list(document_points)
    if not points:
        return []
    document_id = str((points[0].get("payload") or {}).get("document_id") or "")
    attributed = attribute_sections(points)
    records: list[dict] = []
    for p in points:
        cid = str((p.get("payload") or {}).get("chunk_id") or p.get("id") or "")
        cands = extract_crossref_candidates(p.get("payload") or {})
        cands = resolve_cross_references(cands, section_index, document_id, act_document_id)
        cands = _dedup_edges(cands, cid)
        records.append(build_deterministic_record(p, attributed.get(cid, {}), cands))
    return records


def _dedup_edges(candidates: list[dict], chunk_id: str) -> list[dict]:
    """Drop self-loops and duplicate edges from resolved candidates.

    A chunk that cites its own section (e.g. a section-32 chunk mentioning
    "section 32") would resolve to itself — a meaningless REFERS_TO loop
    that also trips the duplicate-edge validation guard.  Resolved edges are
    deduped by ``(relation, target_chunk_id)``; unresolved by
    ``(relation, section)``.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for cand in candidates:
        target = cand.get("target_chunk_id")
        if cand.get("resolved") and target == chunk_id:
            continue  # self-reference — no graph value
        key = (
            cand.get("relation", "REFERS_TO"),
            target or str(cand.get("section") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return out


def build_section_index(points: Iterable[dict]) -> dict[tuple[str, str], list[tuple[str, int]]]:
    """Build ``{(document_id, section_number): [(chunk_id, chunk_index)]}``.

    Chunk order is retained so :func:`resolve_cross_references` can anchor a
    multi-chunk section to its first (header) chunk.
    """
    index: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for p in points:
        pl = p.get("payload") or {}
        sec = pl.get("section_number")
        if not sec:
            continue
        key = (str(pl.get("document_id") or ""), str(sec))
        index.setdefault(key, []).append(
            (str(pl.get("chunk_id") or p.get("id") or ""), int(pl.get("chunk_index", 0) or 0))
        )
    return index


def find_act_document(points: Iterable[dict]) -> str | None:
    """Return the document_id of the primary FSS Act document, if any.

    Prefers the act document whose title contains \"Food Safety and
    Standards Act\"; falls back to the largest act document by chunk count.
    """
    candidates: list[tuple[int, str, str]] = []
    counts: dict[str, int] = {}
    for p in points:
        pl = p.get("payload") or {}
        if (pl.get("document_type") or "").lower() != "act":
            continue
        did = str(pl.get("document_id") or "")
        counts[did] = counts.get(did, 0) + 1
        title = str(pl.get("document_title") or "")
        candidates.append((counts[did], did, title))
    # Score: title-hit priority, then chunk count (largest act doc wins ties).
    ranked = sorted(
        candidates,
        key=lambda t: (1 if "food safety and standards act" in t[2].lower() else 0, t[0]),
        reverse=True,
    )
    return ranked[0][1] if ranked else None

"""Backfill `section_number` identity on Qdrant payloads (RANKING_CEILING_V2).

Motivation (from RANKING_CEILING_V1): only ~22.5% of payloads carry a
`section_number`, so numeric gold sections (e.g. ``water_act:s7``,
``kmc:s313``) cannot be matched at resolution time even when the chunk is
physically in the corpus.  This script stamps the field from layered,
confidence-graded sources — **it never overwrites an existing value**:

    L1  provision_id   ``FSS_..._SEC_41`` / ``CONTRACT_ACT_1872_SEC_6``
                       -> section_number (authoritative, zero risk)
    L2  KG mapping     Chunk node (chunk_id / qdrant_point_id)
                       -> LegalProvision.provision_number (authoritative)
    L3  text headers   explicit "Section N" and mid-text/line-start "N. Title"
                       (KMC/statute style) — applied ONLY when (a) the chunk
                       yields exactly one distinct candidate section, (b) the
                       number is within the family's known section range, and
                       (c) the chunk's ``document_id`` token-normalises to the
                       family's canonical Act document in the gold registry
                       (sub-instruments like rules/amendments/forms share the
                       parent act_name but are NOT the Act and are excluded).
                       The "Sec. N" and "N)" forms were validated out: they
                       matched gazette page headers ("[PART III—SEC.4] THE
                       GAZETTE OF INDIA") and subsections respectively.
    L4  any-position   ``N. <Capital>`` headers anywhere in the chunk text
        header pass   (paren-tolerant: also ``45. (I) …``), family- and
        (2026-08-13)  act-range-validated via ``app.rag.legal_sections``
                       ACT_SECTION_RANGES.  Unlike L3 it (a) tolerates
                       multi-section chunks (section-index runs, chapter
                       boundaries) — the chunk's *first* in-range header
                       becomes ``section_number`` and the full set is recorded
                       in a new ``sections_covered`` field, and (b) OVERRIDES
                       a stale ``section_number`` whose base digits are not
                       among the covered headers (e.g. ``sog:s20``'s body
                       chunk was stamped ``7`` from a leading residue,
                       ``kmc:s391`` stamped ``16`` from a cross-reference).
                       This is the layer that closes the V7 candidate gap
                       (7 units; validated offline: pool ceiling 91.9% -> 100%).
                       Same ``document_id`` canonical-Act gate as L3.

`act_name` is already 100% populated (V1 audit), so only `section_number` is
backfilled; `section_title` is added when the KG provides it.  Everything is
validated against the frozen gold registry — a missing/unreadable registry
fails L3/L4 closed (no text stamps at all).

Usage:
    python scripts/backfill_payload_identity.py                  # dry-run stats (live scroll)
    python scripts/backfill_payload_identity.py --from-cache     # dry-run on the frozen payload cache
    python scripts/backfill_payload_identity.py --apply          # write Qdrant
    python scripts/backfill_payload_identity.py --apply --rebuild-index
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backfill.identity")

#: Text-header patterns, in priority order.  Validated against a 20-point
#: sample in RANKING_CEILING_V2 development:
#:   * `Section N`  — explicit header keyword (safe).
#:   * mid-text `N. Title` — KMC/statute style ("585. Power to institute").
#:   * line-start `N. Title` — statute style.
#: The `Sec. N` and `N)` forms are deliberately EXCLUDED: `Sec.` matched
#: gazette page headers ("[PART III—SEC.4] THE GAZETTE OF INDIA") and
#: cross-references, and `N)` matched subsections — both false-positive
#: sources in the V1 stamp verification.
_PATTERNS = [
    re.compile(r"\bSection\s+(\d{1,3})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,3})\.\s+[A-Z]"),
    re.compile(r"(?m)^\s*(\d{1,3})\.\s+[A-Z]"),
]

#: Gazette page-header blocks ("[PART III—SEC.4] THE GAZETTE OF INDIA") and
#: TOC/index pages must never be stamped as section identity.  The gazette
#: header carries "SEC.4" which the old `Sec.` pattern misread as a section;
#: `GAZETTE OF INDIA` is the disambiguator.  `_TOC_RE` catches contents
#: lists ("S. NO" / "INDEX OF SECTIONS").
_GAZETTE_RE = re.compile(r"GAZETTE OF INDIA|PART [IVX]+\s*[\u2014-]\s*SEC", re.IGNORECASE)
_TOC_RE = re.compile(r"(?m)^\s*(?:CONTENTS|INDEX|S\.?\s*NO\.?)\b", re.IGNORECASE)

_SEC_IN_PROVISION_ID = re.compile(r"_SEC_(\d+)")

#: L4 header pattern (validated 2026-08-13 against the V7 gap units):
#: any-position ``N. <Capital>`` including parenthesised continuations
#: (``45. (I) The West Bengal Premises Tenancy Act, 1956 …``) that both the
#: V2 patterns and V7's line-anchored repair regexes missed.  The lookbehind
#: prevents matching page/residue numbers glued to other digits (``1980 313.``
#: is fine, ``...313...`` inside a longer number is not).
_L4_HEADER_RE = re.compile(r"(?<![A-Za-z0-9])(\d{1,4})\s*\.\s*(?:\(\s*)?[A-Z]")

#: family -> canonical Act document_ids (loaded from the gold registry; see
#: registry_document_ids for the rationale).  Module-level so the L3 gate in
#: derive_section and any verification harness share the same whitelist.
_REGISTRY_DOCIDS: dict[str, set[str]] = {}

#: Tokens never part of a document identity (drops "Act" so corpus slugs like
#: ``wb_premises_tenancy_act_1997`` match the registry's ``wb_premises_tenancy_1997``;
#: only exact token-level equality after this filter is accepted).
_DOCID_STOP = {"act", "the", "an", "a", "of"}


def norm_docid(value: str) -> str:
    """Token-normalised document id: lowercase, split on non-alphanumerics,
    drop article tokens (act/the/a/an/of).  Used by the L3 whitelist so exact
    slugs and corpus variants compare equal; anything still differing is a
    genuinely different document (e.g. srf 2017 vs 1963) and is excluded."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", str(value).lower()) if t and t not in _DOCID_STOP]
    return " ".join(tokens)


#: Payload ``instrument_id`` markers that ARE the family's canonical Act, used
#: to resolve document_ids when the gold registry's document_id does not exist
#: in the payloads (e.g. fssai: the registry carries one legacy UUID, but the
#: re-ingested corpus splits the Act, its amendments, ~20 regulations and
#: gazette notifications into 29 documents with fresh UUIDs).  A payload is
#: L3-stampable iff its ``instrument_id`` token-normalises to one of these
#: markers.  Derived from corpus evidence: ``FSS_ACT_2006`` is the consolidated
#: FSS Act 2006 document ("16. Duties and functions of Food Authority"); the
#: amendment acts / regulations / notifications are deliberately excluded —
#: they carry the parent act_name but are NOT the Act and their "section"
#: numbers are amendment/rule/notification numbers.
_INSTRUMENT_OVERRIDES: dict[str, tuple[str, ...]] = {
    "fssai": ("FSS_ACT_2006",),
}


def _load_registry_docids() -> dict[str, set[str]]:
    """Canonical-document whitelist: gold-registry document_ids (normalised)
    merged with any instrument_id overrides.  Fail-closed: no registry/override
    -> no L3 stamps for that family."""
    out: dict[str, set[str]] = {}
    try:
        for fam, docids in registry_document_ids().items():
            out.setdefault(fam, set()).update(norm_docid(d) for d in docids)
    except Exception:
        out = {}
    for fam, markers in _INSTRUMENT_OVERRIDES.items():
        out.setdefault(fam, set()).update(norm_docid(m) for m in markers)
    return out


def prime_registry_docids(payloads: dict[str, dict]) -> dict[str, set[str]]:
    """Resolve the instrument_id overrides to concrete document_ids from the
    payloads and store the merged whitelist in the module cache.

    Call this once after scrolling payloads (main() and any verification
    harness) so every caller of :func:`derive_section` shares the same gate.
    """
    whitelist = _load_registry_docids()
    for fam, markers in _INSTRUMENT_OVERRIDES.items():
        wanted = {norm_docid(m) for m in markers}
        # document_ids of payloads whose instrument_id matches a marker
        docids = {
            norm_docid(str(p.get("document_id") or ""))
            for p in payloads.values()
            if norm_docid(str(p.get("instrument_id") or "")) in wanted and p.get("document_id")
        }
        if docids:
            whitelist.setdefault(fam, set()).update(docids)
    _REGISTRY_DOCIDS.clear()
    _REGISTRY_DOCIDS.update(whitelist)
    return whitelist


def base_digits(value) -> str | None:
    m = re.match(r"\s*(\d{1,4})", str(value or "").strip())
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def registry_document_ids() -> dict[str, set[str]]:
    """family -> canonical Act document_ids from the gold provision registry.

    This is the L3 discriminator that keeps sub-instrument chunks (rules,
    regulations, amendments, draft notifications, forms — and *other Acts*
    that happen to share an act_name) from being stamped with the parent
    Act's section numbers.  A payload may only be L3-stamped when its own
    ``document_id`` is the family's canonical Act document (or when the
    registry itself carries no document_id for the family — then L3 is
    disabled for that family, never guessed).
    """
    from evaluation.benchmark import load_gold_registry

    out: dict[str, set[str]] = {}
    for pid, rec in load_gold_registry().items():
        fam = str(pid).split(":", 1)[0]
        d = rec.get("document_id")
        if d:
            out.setdefault(fam, set()).add(str(d))
    return out


def kg_mapping() -> dict[str, dict]:
    """chunk_id / qdrant_point_id -> {number, instrument_title, title}."""
    from kg.queries import LegalKGQueries

    q = LegalKGQueries()
    rows = q._execute(
        "MATCH (c:Chunk)<-[:SUPPORTED_BY]-(p:LegalProvision) "
        "OPTIONAL MATCH (i)-[:CONTAINS]->(p) "
        "RETURN c.chunk_id AS chunk_id, c.qdrant_point_id AS qp, "
        "p.provision_number AS number, p.title AS provision_title, "
        "i.title AS instrument_title"
    )
    out: dict[str, dict] = {}
    for r in rows:
        rec = {
            "number": str(r.get("number") or "").strip(),
            "instrument_title": r.get("instrument_title") or "",
            "provision_title": r.get("provision_title") or "",
        }
        if r.get("chunk_id"):
            out[str(r["chunk_id"])] = rec
        if r.get("qp"):
            out[str(r["qp"])] = rec
    return out


def family_max_sections(family_map, payloads: dict[str, dict]) -> dict[str, int]:
    """Max provision number per family — from KG instruments AND existing
    payload section_numbers (validation ceiling for the L3 regex)."""
    from kg.queries import LegalKGQueries

    maxima: dict[str, int] = {}
    try:
        q = LegalKGQueries()
        rows = q._execute(
            "MATCH (i)-[:CONTAINS]->(p:LegalProvision) RETURN i.title AS instrument_title, p.provision_number AS number"
        )
        for r in rows:
            for fam in family_map.family_s_for_act(r.get("instrument_title")):
                n = base_digits(r.get("number"))
                if n:
                    maxima[fam] = max(maxima.get(fam, 0), int(n))
    except Exception as exc:
        logger.warning("KG family maxima failed: %s", exc)
    for p in payloads.values():
        for fam in family_map.family_s_for_act(p.get("act_name") or p.get("document_title") or ""):
            n = base_digits(p.get("section_number"))
            if n:
                maxima[fam] = max(maxima.get(fam, 0), int(n))
    return maxima


def scroll_payloads(app, collections) -> tuple[dict[str, dict], dict[str, str]]:
    """Scroll every collection once -> ({point_id: payload}, {point_id: collection})."""
    from app.rag.qdrant_client import QdrantStore

    payloads: dict[str, dict] = {}
    provenance: dict[str, str] = {}
    for coll in collections:
        store = QdrantStore(collection_name=coll)
        points = store.scroll_all(batch_size=500)
        for p in points:
            pid = str(p["id"])
            payloads[pid] = p.get("payload") or {}
            provenance[pid] = coll
        logger.info("scrolled %s: %d points", coll, len(points))
    return payloads, provenance


def collections_from_config(app) -> list[str]:
    cfg = app.config
    return list(
        dict.fromkeys([
            cfg.get("RAG_QDRANT_COLLECTION", "fssai_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_ENV", "env_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_COMMERCIAL", "commercial_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_ANIMAL", "animal_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_WB_STATE", "wb_state_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_CRIMINAL", "criminal_legal_768"),
        ])
    )


# --------------------------------------------------------------------------- #
# Derivation
# --------------------------------------------------------------------------- #
def derive_section(
    point_id: str, payload: dict, kg_map: dict, maxima: dict, family_map
) -> tuple[str | None, str | None]:
    """Return (section_number, section_title) for a point, or (None, None).

    L1 provision_id -> L2 KG -> L3 validated text regex.  Never overwrites
    an existing section_number.

    Act documents only: ``section_number`` is Act identity (G8); on
    regulation/rule/notification chunks it is noise (page numbers, def-list
    numbers) and was stripped by ``scripts/strip_reg_section_noise.py`` —
    but the old noise is still encoded in ``provision_id``
    (``FSS_…_SEC_41`` on a chunk whose text is just ``41``), so L1/L2 must
    not resurrect it.
    """
    if (payload.get("document_type") or "") != "act":
        return None, None
    if payload.get("section_number"):
        return None, None

    # L1
    pid = payload.get("provision_id")
    if pid:
        m = _SEC_IN_PROVISION_ID.search(str(pid))
        if m:
            return m.group(1), None

    # L2
    kg = kg_map.get(str(point_id))
    if kg and kg.get("number"):
        return base_digits(kg["number"]), kg.get("provision_title") or None

    # L3 — family-scoped, single-distinct-candidate, range-validated, and
    # restricted to the family's canonical Act document (see
    # registry_document_ids — sub-instruments share the parent act_name and
    # would otherwise be stamped with rule/amendment numbers).
    act = payload.get("act_name") or payload.get("document_title") or ""
    fams = family_map.family_s_for_act(act)
    if not fams:
        return None, None
    doc_whitelist = _REGISTRY_DOCIDS or _load_registry_docids()
    if not _REGISTRY_DOCIDS:
        _REGISTRY_DOCIDS.update(doc_whitelist)
    payload_docid = norm_docid(str(payload.get("document_id") or ""))
    fams = [f for f in fams if doc_whitelist.get(f) and payload_docid in doc_whitelist[f]]
    if not fams:
        return None, None
    ceiling = max((maxima.get(f, 0) for f in fams), default=0)
    if ceiling <= 0:
        return None, None
    text = str(payload.get("chunk_text") or payload.get("text") or "")
    if not text.strip():
        return None, None
    if _GAZETTE_RE.search(text[:120]) or _TOC_RE.search(text):
        return None, None
    candidates: set[int] = set()
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            try:
                candidates.add(int(m.group(1)))
            except ValueError:
                continue
    if len(candidates) != 1:
        return None, None
    (sec,) = candidates
    if sec < 1 or sec > ceiling:
        return None, None
    return str(sec), None


def family_ranges(family_map) -> dict[str, tuple[int, int]]:
    """family -> (lo, hi) from ``app.rag.legal_sections.ACT_SECTION_RANGES``.

    Resolves each registered act name to its family(s) via the FamilyMap;
    families without a registry range are absent (L4 then falls back to the
    KG/payload maxima ceiling, or skips the chunk).
    """
    from app.rag.legal_sections import ACT_SECTION_RANGES

    out: dict[str, tuple[int, int]] = {}
    for act_name, (lo, hi) in ACT_SECTION_RANGES.items():
        for fam in family_map.family_s_for_act(act_name):
            cur = out.get(fam)
            if cur:
                out[fam] = (min(cur[0], lo), max(cur[1], hi))
            else:
                out[fam] = (lo, hi)
    return out


def derive_section_l4(
    point_id: str,
    payload: dict,
    family_map,
    ranges: dict[str, tuple[int, int]],
    maxima: dict[str, int],
) -> tuple[list[str], list[str]]:
    """Return (in_range_headers, covering_families) for the L4 any-position pass.

    Headers are the first-occurrence-ordered list of ``N`` from
    ``_L4_HEADER_RE`` that fall inside a family the chunk resolves to and
    inside that family's known range (registry range if present, else the
    KG/payload maxima ceiling).  Sub-instrument chunks are excluded by the
    same canonical-``document_id`` gate L3 uses — an empty whitelist for the
    family fails the chunk closed (no L4 stamps without the registry).
    """
    act = payload.get("act_name") or payload.get("document_title") or ""
    fams = family_map.family_s_for_act(act)
    if not fams:
        return [], []
    doc_whitelist = _REGISTRY_DOCIDS or _load_registry_docids()
    if not _REGISTRY_DOCIDS:
        _REGISTRY_DOCIDS.update(doc_whitelist)
    payload_docid = norm_docid(str(payload.get("document_id") or ""))
    fams = [f for f in fams if doc_whitelist.get(f) and payload_docid in doc_whitelist[f]]
    if not fams:
        return [], []
    text = str(payload.get("chunk_text") or payload.get("text") or "")
    if not text.strip() or len(text) < 15:
        return [], []
    if _GAZETTE_RE.search(text[:120]):
        return [], []
    seen: set[int] = set()
    ordered: list[int] = []
    for m in _L4_HEADER_RE.finditer(text):
        try:
            n = int(m.group(1))
        except ValueError:
            continue
        if n in seen or n < 1:
            continue
        for fam in fams:
            rng = ranges.get(fam)
            if rng:
                lo, hi = rng
            else:
                ceiling = maxima.get(fam, 0)
                if ceiling <= 0:
                    continue
                lo, hi = 1, ceiling
            if lo <= n <= hi:
                seen.add(n)
                ordered.append(n)
                break
    return [str(n) for n in ordered], fams


# --------------------------------------------------------------------------- #
# L5 — header-anchored section propagation (G7 fix, 2026-08-17)
# --------------------------------------------------------------------------- #
#
# The FSS Act document (and similar statute docs) is chunked into subsection
# fragments — ``(1) Every food business operator shall ensure…`` — that never
# repeat the section number, so the L4 ``N. Capital`` header regex cannot
# stamp them (measured: 485/722 Act-doc fragments unreachable by L4).  L5
# propagates the last **L4-verified** section header forward within a
# document, filling only unstamped hl>=2 fragments.
#
# Why header-anchored (not naive predecessor propagation): engine cross-
# reference noise stamps fragments with the *referenced* section
# (``appointed under section 30`` -> ``sec='30'``), which would corrupt a
# naive running counter.  L5 only ever moves the section from a chunk whose
# header L4 independently verified, and never overwrites an existing
# ``section_number``.


def derive_section_l5(
    payloads_by_doc: dict[str, list[dict]],
    l4_headers: dict[str, list[str]],
) -> dict[str, str]:
    """Return {point_id: section_number} for propagatable fragments (L5).

    ``payloads_by_doc`` maps document_id -> payloads **already ordered by
    chunk_index**; ``l4_headers`` maps point_id -> the section numbers the
    L4 pass verified in that chunk's text (a non-empty list marks the chunk
    as a trusted section boundary).

    Propagation rules:
      * a chunk with a non-empty ``l4_headers`` entry resets the running
        section (boundary), and keeps its own section_number (never
        overwritten here),
      * a chunk with NO section_number AND hierarchy_level >= 2 inherits the
        running section (if any),
      * everything else (already stamped, hl1 boilerplate, before the first
        boundary) is left untouched.
    """
    out: dict[str, str] = {}
    for _doc_id, payloads in payloads_by_doc.items():
        running: str | None = None
        for pl in payloads:
            pid = str(pl.get("chunk_id") or "")
            if l4_headers.get(pid):
                running = l4_headers[pid][0]
                continue
            if pl.get("section_number"):
                continue
            if running and (pl.get("hierarchy_level") or 1) >= 2:
                out[pid] = running
    return out


# --------------------------------------------------------------------------- #
# L7 — header-trust correction + amendment anchors (P2, 2026-08-18)
# --------------------------------------------------------------------------- #
#
# L5 propagates only from L4-verified headers, and L4 is gated by the
# canonical-document whitelist — so consolidated editions (LLP, Specific
# Relief) whose headers L4 does not verify, and amendment acts that have no
# ``N. Title`` headers at all, never propagate.  L7 adds two guarded
# mechanisms for act documents:
#
#   * header-trust correction — a stamped chunk whose text STARTS with
#     ``N.``/``N)``/``N `` + Capital declares its own section; the leading
#     number wins over an in-text cross-reference stamp (LLP's ``50.
#     Prosecution. … from the report under section 49`` was stamped 49).
#     Gazette page headers, amendment footnotes (``2. Subs. by s. 21, ibid.,
#     … w.e.f.``) and TOC/arrangement pages are excluded.
#   * amendment-anchor propagation — for act docs with zero L4-verified
#     headers (the FSS amendment acts), any stamped hl>=2 chunk whose text
#     names ``section N`` is a running anchor (the referenced section IS the
#     amendment's identity), with an ascending-order guard so backwards
#     cross-references (``as defined in section 2``) never reset the run.
#
# Both mechanisms never overwrite an existing stamp except the correction
# itself (a targeted override, same class as L4_override).  Criminal docs
# (space-stripped BNS OCR) are excluded — their stamps are cross-ref noise.

#: Header-like line start: ``50. Prosecution`` / ``77A. Cognizance``.
#: Deliberately DOT/PAREN-anchored only: the ``N Word`` space form matched
#: page-number fragments and title pages (``3 THE AIR (PREVENTION…``,
#: ``2 Stoppage in transit``, ``49 CHAPTER X``) that L4's range-validated
#: header analysis later overrides (verified: 29/31 L4-vs-L7 conflicts were
#: space-form, 2026-08-18).  Dotted clause numbers (``5.06 Washbasin``) and
#: amendment-schedule residue (``1. 1870 7 The Court-``) also do not match.
_HEADER_TRUST_RE = re.compile(r"^\s*(\d{1,4})([A-Z])?(?:\.\s*|\)\s*)[A-Z]")

#: Amendment-footnote markers — ``2. Subs. by s. 21, ibid., for section 69``
#: is a footnote number, NOT a section header.
_FOOTNOTE_RE = re.compile(r"\b(?:Subs\.?|Ins\.?|Omitted|ibid\.|w\.e\.f\.)\b", re.IGNORECASE)

#: In-text section references (amendment mode anchor rule).
_AMEND_SECTION_RE = re.compile(r"\bsection\s+(\d{1,3})", re.IGNORECASE)

_ARRANGEMENT_RE = re.compile(r"\bARRANGEMENT\s+OF\s+SECTIONS\b", re.IGNORECASE)


#: Collections whose act docs are excluded from L7 (space-stripped OCR —
#: BNS 2023; stamps are cross-ref/gazette noise, see COVERAGE_COMPLETENESS P3).
_L7_EXCLUDED_DOMAINS = ("CRIMINAL",)


def header_trust_number(text: str | None) -> int | None:
    """Section number declared by the chunk's own leading header, or None.

    Guards: gazette page headers (``40 THE GAZETTE OF INDIA``), amendment
    footnotes (``2. Subs. by s. 21 …``) and TOC/arrangement pages never
    count as headers.  ``None`` for anything else (prose, paren fragments,
    dotted clause numbers).
    """
    t = (text or "").lstrip()
    if not t:
        return None
    if _GAZETTE_RE.search(t[:120]) or _TOC_RE.search(t) or _ARRANGEMENT_RE.search(t):
        return None
    if _FOOTNOTE_RE.search(t):
        return None
    m = _HEADER_TRUST_RE.match(t)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def amendment_anchor(payload: dict, running: str | None) -> str | None:
    """Anchor section for amendment-mode docs, or None.

    A stamped hl>=2 chunk whose text names ``section N`` and whose stamp is
    among the named sections becomes the running anchor; a backwards
    reference (stamp < running) never resets the run — amendments proceed in
    ascending section order, so a lower number is a cross-reference, not the
    subject (``…as defined in section 2`` inside an amendment to 34).
    """
    if (payload.get("hierarchy_level") or 1) < 2:
        return None
    stamp = base_digits(payload.get("section_number"))
    if not stamp:
        return None
    refs = {int(m.group(1)) for m in _AMEND_SECTION_RE.finditer(payload.get("chunk_text") or "")}
    if not refs or int(stamp) not in refs:
        return None
    if running and int(stamp) < int(running):
        return None
    return stamp


def derive_l7(
    payloads_by_doc: dict[str, list[dict]],
    l4_headers: dict[str, list[str]],
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (corrections, fills) for the L7 pass.

    * ``corrections``: {point_id: section_number} — header-trust overrides
      of mis-stamped header chunks (the leading ``N.`` wins);
    * ``fills``: {point_id: section_number} — propagated section numbers for
      unstamped hl>=2 fragments (never overwrites).

    Act documents only; criminal (BNS) and non-act docs are skipped.  The
    caller sorts ``payloads_by_doc`` by chunk_index (L5 contract).
    """
    corrections: dict[str, str] = {}
    fills: dict[str, str] = {}
    for _doc_id, payloads in payloads_by_doc.items():
        pl0 = payloads[0]
        if pl0.get("document_type") != "act":
            continue
        if pl0.get("legal_domain") in _L7_EXCLUDED_DOMAINS:
            continue
        has_l4 = any(l4_headers.get(str(p.get("chunk_id") or "")) for p in payloads)

        # pass 1 — header-trust corrections (never touches unstamped chunks;
        # L4-verified chunks are L4's domain — L7 must not fight the
        # range-validated any-position analysis on e.g. ``39D.`` / ``76A.``
        # headers, verified 2026-08-18).
        for p in payloads:
            pid = str(p.get("chunk_id") or "")
            if not p.get("section_number"):
                continue
            if l4_headers.get(pid) or p.get("sections_covered"):
                continue
            n = header_trust_number(p.get("chunk_text") or "")
            if n is None:
                continue
            if base_digits(p.get("section_number")) != str(n):
                corrections[pid] = str(n)

        # pass 2 — propagation
        running: str | None = None
        for p in payloads:
            pid = str(p.get("chunk_id") or "")
            hl = p.get("hierarchy_level") or 1
            if l4_headers.get(pid):
                running = l4_headers[pid][0]
                continue
            n = header_trust_number(p.get("chunk_text") or "")
            if n is not None and p.get("section_number"):
                # corrected-or-already-correct header boundary — the leading
                # ``N.`` is more authoritative than an in-text cross-ref, so
                # it wins even in amendment-mode docs.
                running = str(n)
                continue
            if not has_l4:
                anchor = amendment_anchor(p, running)
                if anchor:
                    running = anchor
                    continue
            if p.get("section_number"):
                continue
            if running and hl >= 2 and pid not in corrections:
                fills[pid] = running
    return corrections, fills


# --------------------------------------------------------------------------- #
# Apply (Qdrant set_payload — payload-only, vectors untouched)
# --------------------------------------------------------------------------- #
def set_payload_batched(client, collection: str, changes: dict[str, dict], batch_size: int = 200) -> int:
    """Group ids by identical payload dict; set_payload per group in batches."""
    groups: dict[str, list[str]] = {}
    for pid, ch in changes.items():
        groups.setdefault(json.dumps(ch, sort_keys=True), []).append(pid)
    applied = 0
    for key, ids in groups.items():
        payload = changes[ids[0]]
        for i in range(0, len(ids), batch_size):
            batch = ids[i : i + batch_size]
            try:
                client.set_payload(collection_name=collection, payload=payload, points=batch)
                applied += len(batch)
            except TypeError:
                # some client versions use a different kwarg name
                client.set_payload(collection=collection, payload=payload, points=batch)
                applied += len(batch)
            except Exception as exc:
                logger.warning("set_payload batch failed (%s) — retrying per point", exc)
                for pid in batch:
                    try:
                        client.set_payload(collection_name=collection, payload=payload, points=[pid])
                        applied += 1
                    except Exception as exc2:
                        logger.warning("set_payload %s failed: %s", pid, exc2)
    return applied


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write to Qdrant (default: dry-run)")
    parser.add_argument(
        "--rebuild-index", action="store_true", help="rebuild evaluation/out/cache/payload_index.jsonl after apply"
    )
    parser.add_argument(
        "--snapshot-dir",
        default=str(PROJECT_ROOT / "evaluation" / "out" / "ceiling_v2"),
        help="dir for the pre-backfill payload snapshot",
    )
    parser.add_argument(
        "--from-cache",
        action="store_true",
        help="dry-run against the frozen payload cache instead of scrolling live Qdrant "
        "(collection provenance unknown -> 'cache'; apply still requires live scroll)",
    )
    parser.add_argument(
        "--no-l7",
        action="store_true",
        help="disable the L7 header-trust correction + amendment-anchor propagation "
        "pass (COVERAGE_COMPLETENESS P2, 2026-08-18)",
    )
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    collections = collections_from_config(app)
    snapshot_dir = Path(args.snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    with app.app_context():
        from evaluation.benchmark import load_questions
        from evaluation.resolution import FamilyMap, matches_gold

        if args.from_cache:
            if args.apply:
                return 2
            payloads: dict[str, dict] = {}
            provenance: dict[str, str] = {}
            cache_path = PROJECT_ROOT / "evaluation" / "out" / "cache" / "payload_index.jsonl"
            with open(cache_path, encoding="utf-8") as f:
                for line in f:
                    rec = json.loads(line)
                    payloads[str(rec["id"])] = rec["payload"]
                    provenance[str(rec["id"])] = "cache"
            logger.info("loaded payload cache: %d points", len(payloads))
        else:
            payloads, provenance = scroll_payloads(app, collections)
            logger.info("total points: %d", len(payloads))
        prime_registry_docids(payloads)

        family_map = FamilyMap()
        kg_map = kg_mapping()
        maxima = family_max_sections(family_map, payloads)
        logger.info("kg_map=%d family maxima=%d", len(kg_map), len(maxima))

        # --- derive changes
        changes: dict[str, dict] = {}
        layer_counts = {"L1_provision_id": 0, "L2_kg": 0, "L3_text": 0}
        for point_id, payload in payloads.items():
            if payload.get("section_number"):
                continue
            sec, title = derive_section(point_id, payload, kg_map, maxima, family_map)
            if sec is None:
                continue
            # Layer label must mirror derive_section's own precedence: L1 needs
            # a parseable provision_id, L2 needs a KG entry with a non-empty
            # provision number, else L3.  (Checking only key presence would
            # mislabel a KG edge whose provision_number is empty.)
            if payload.get("provision_id") and _SEC_IN_PROVISION_ID.search(str(payload["provision_id"])):
                layer = "L1_provision_id"
            elif (kg_entry := kg_map.get(str(point_id))) and kg_entry.get("number"):
                layer = "L2_kg"
            else:
                layer = "L3_text"
            layer_counts[layer] += 1
            entry = {"section_number": sec, "source": layer}
            if title:
                entry["section_title"] = title
            changes[point_id] = entry

        # --- L4 any-position header pass (V7-gap closure, 2026-08-13): runs on
        # ALL payloads — including ones with a stale stamp — and overrides a
        # ``section_number`` whose base digits are not among the headers the
        # chunk text actually contains (e.g. ``sog:s20``'s body chunk stamped
        # ``7``, ``kmc:s391`` stamped ``16`` from a cross-reference).
        # ``sections_covered`` records the full in-range header set for
        # multi-section / section-index chunks, which the resolution layer
        # (``evaluation/resolution.py``) now consults.
        ranges = family_ranges(family_map)

        # --- L5 header-anchored propagation (G7 fix, 2026-08-17): fills the
        # subsection fragments that L4 cannot reach (they never repeat the
        # section number).  Boundaries are L4-verified headers only; engine
        # cross-reference noise (``appointed under section 30`` -> sec=30) is
        # never used as a running-section source.
        l4_headers: dict[str, list[str]] = {}
        for point_id, payload in payloads.items():
            covered, _fams = derive_section_l4(point_id, payload, family_map, ranges, maxima)
            if covered:
                l4_headers[str(point_id)] = covered
        payloads_by_doc: dict[str, list[dict]] = {}
        for pid, pl in payloads.items():
            payloads_by_doc.setdefault(str(pl.get("document_id") or ""), []).append(dict(pl, chunk_id=pid))
        for pls in payloads_by_doc.values():
            pls.sort(key=lambda p: p.get("chunk_index") or 0)
        l5_changes = derive_section_l5(payloads_by_doc, l4_headers)
        l5_rows: list[dict] = []
        for pid, sec in l5_changes.items():
            if pid in changes:
                continue
            changes[pid] = {"section_number": sec, "source": "L5_propagation"}
            layer_counts["L5_propagation"] = layer_counts.get("L5_propagation", 0) + 1
            pl = payloads[pid]
            l5_rows.append({
                "collection": provenance.get(pid, "cache"),
                "point_id": pid,
                "family": "fssai",
                "old_section_number": pl.get("section_number") or "",
                "new_section_number": sec,
                "sections_covered": "",
                "source": "L5_propagation",
                "evidence": str(pl.get("chunk_text") or "")[:200].replace("\n", " "),
            })
        l5_csv = snapshot_dir / "repair_sections_l5.csv"
        with open(l5_csv, "w", newline="", encoding="utf-8") as f:
            import csv as _csv2

            writer = _csv2.DictWriter(
                f,
                fieldnames=[
                    "collection",
                    "point_id",
                    "family",
                    "old_section_number",
                    "new_section_number",
                    "sections_covered",
                    "source",
                    "evidence",
                ],
            )
            writer.writeheader()
            writer.writerows(l5_rows)
        logger.info("L5 repair CSV -> %s (%d rows)", l5_csv, len(l5_rows))

        # --- L7 header-trust correction + amendment anchors (P2, 2026-08-18):
        # fills the paren-fragment gap in consolidated acts (LLP/SR — headers
        # exist but L4 does not verify them) and amendment acts (zero headers;
        # the referenced section is the identity).  Corrections OVERRIDE
        # mis-stamped header chunks (leading ``N.`` wins); fills never
        # overwrite.  Skips criminal (BNS, space-stripped OCR).
        l7_rows: list[dict] = []
        if not args.no_l7:
            l7_corrections, l7_fills = derive_l7(payloads_by_doc, l4_headers)
            for pid, sec in l7_corrections.items():
                if pid in changes:
                    continue
                changes[pid] = {"section_number": sec, "source": "L7_correction"}
                layer_counts["L7_correction"] = layer_counts.get("L7_correction", 0) + 1
                pl = payloads[pid]
                l7_rows.append({
                    "collection": provenance.get(pid, "cache"),
                    "point_id": pid,
                    "family": "",
                    "old_section_number": pl.get("section_number") or "",
                    "new_section_number": sec,
                    "sections_covered": "",
                    "source": "L7_correction",
                    "evidence": str(pl.get("chunk_text") or "")[:200].replace("\n", " "),
                })
            for pid, sec in l7_fills.items():
                if pid in changes:
                    continue
                changes[pid] = {"section_number": sec, "source": "L7_propagation"}
                layer_counts["L7_propagation"] = layer_counts.get("L7_propagation", 0) + 1
                pl = payloads[pid]
                l7_rows.append({
                    "collection": provenance.get(pid, "cache"),
                    "point_id": pid,
                    "family": "",
                    "old_section_number": pl.get("section_number") or "",
                    "new_section_number": sec,
                    "sections_covered": "",
                    "source": "L7_propagation",
                    "evidence": str(pl.get("chunk_text") or "")[:200].replace("\n", " "),
                })
        l7_csv = snapshot_dir / "repair_sections_l7.csv"
        with open(l7_csv, "w", newline="", encoding="utf-8") as f:
            import csv as _csv3

            writer = _csv3.DictWriter(
                f,
                fieldnames=[
                    "collection",
                    "point_id",
                    "family",
                    "old_section_number",
                    "new_section_number",
                    "sections_covered",
                    "source",
                    "evidence",
                ],
            )
            writer.writeheader()
            writer.writerows(l7_rows)
        logger.info("L7 repair CSV -> %s (%d rows)", l7_csv, len(l7_rows))

        # --- L4 any-position header pass (V7-gap closure, 2026-08-13) — runs
        # LAST so its range-validated header analysis wins over L5/L7 stamps
        # in the same apply (converges in one run; verified 2026-08-18: 31
        # L4-vs-L7 disagreements on page-number/TOC-fragment fills).  Overrides
        # a ``section_number`` whose base digits are not among the headers the
        # chunk text actually contains (e.g. ``sog:s20``'s body chunk stamped
        # ``7``, ``kmc:s391`` stamped ``16`` from a cross-reference), and
        # records the full in-range header set in ``sections_covered`` for the
        # resolution layer.  L1/L2/L3 (authoritative: provision_id/KG/whitelist)
        # are never overridden.
        repair_rows: list[dict] = []
        for point_id, payload in payloads.items():
            src = (changes.get(point_id) or {}).get("source")
            if src in ("L1_provision_id", "L2_kg", "L3_text"):
                continue  # authoritative sources take precedence
            covered, fams = derive_section_l4(point_id, payload, family_map, ranges, maxima)
            if not covered:
                continue
            effective_old = payload.get("section_number") or (changes.get(point_id) or {}).get("section_number")
            current = base_digits(effective_old)
            if current and current in covered:
                # stamp already correct — only record the covered set if absent
                if not payload.get("sections_covered"):
                    changes[point_id] = {"sections_covered": covered, "source": "L4_covered_add"}
                    layer_counts["L4_covered_add"] = layer_counts.get("L4_covered_add", 0) + 1
                continue
            layer = "L4_override" if effective_old else "L4_text_new"
            layer_counts[layer] = layer_counts.get(layer, 0) + 1
            changes[point_id] = {
                "section_number": covered[0],
                "sections_covered": covered,
                "source": layer,
            }
            repair_rows.append({
                "collection": provenance.get(point_id, "cache"),
                "point_id": point_id,
                "family": ";".join(fams),
                "old_section_number": str(effective_old or ""),
                "new_section_number": covered[0],
                "sections_covered": ";".join(covered),
                "source": layer,
                "evidence": str(payload.get("chunk_text") or "")[:200].replace("\n", " "),
            })
        repair_csv = snapshot_dir / "repair_sections_l4.csv"
        with open(repair_csv, "w", newline="", encoding="utf-8") as f:
            import csv as _csv4

            writer = _csv4.DictWriter(
                f,
                fieldnames=[
                    "collection",
                    "point_id",
                    "family",
                    "old_section_number",
                    "new_section_number",
                    "sections_covered",
                    "source",
                    "evidence",
                ],
            )
            writer.writeheader()
            writer.writerows(repair_rows)
        logger.info("L4 repair CSV -> %s (%d rows)", repair_csv, len(repair_rows))

        before = sum(1 for p in payloads.values() if p.get("section_number"))
        after = before + len(changes)
        from collections import Counter

        # --- benchmark impact: gold-unit resolvability before/after
        questions = load_questions()
        seen = set()
        n_resolved_before = 0
        newly_resolved: list[str] = []
        for q in questions:
            for u in q.gold_units:
                if u.provision_id in seen:
                    continue
                seen.add(u.provision_id)
                hit = any(matches_gold(p, u, family_map) for p in payloads.values())
                if hit:
                    n_resolved_before += 1
                    continue
                hit_after = any(
                    matches_gold(dict(p, **changes.get(pid, {})), u, family_map) for pid, p in payloads.items()
                )
                if hit_after:
                    newly_resolved.append(u.provision_id)
        n_resolved_after = n_resolved_before + len(newly_resolved)

        # --- snapshot + optional apply
        if args.apply:
            snap = snapshot_dir / "pre_backfill_payloads.jsonl"
            with open(snap, "w", encoding="utf-8") as f:
                for pid, payload in payloads.items():
                    f.write(json.dumps({"id": pid, "payload": payload}, ensure_ascii=False) + "\n")
            logger.info("pre-backfill snapshot written: %s", snap)

            by_coll: dict[str, dict[str, dict]] = {}
            for pid, ch in changes.items():
                by_coll.setdefault(provenance.get(pid, collections[0]), {})[pid] = ch
            applied = 0
            for coll, chg in by_coll.items():
                store = __import__("app.rag.qdrant_client", fromlist=["QdrantStore"]).QdrantStore(collection_name=coll)
                n = set_payload_batched(store._get_client(), coll, chg)
                applied += n
                logger.info("collection %s: %d updates", coll, n)

            if args.rebuild_index:
                from app.rag.qdrant_client import QdrantStore
                from evaluation.resolution import build_payload_index

                index = build_payload_index(
                    lambda coll: QdrantStore(collection_name=coll),
                    collections,
                    force=True,
                )
                logger.info("payload index rebuilt: %d points", len(index))
        else:
            pass

        summary = {
            "experiment_id": "RANKING_CEILING_V2",
            "mode": "apply" if args.apply else "dry-run",
            "total_points": len(payloads),
            "section_number_before": before,
            "section_number_after": after,
            "changes": len(changes),
            "by_source": layer_counts,
            "by_collection": dict(Counter(provenance.get(pid, "?") for pid in changes)),
            "gold_units_resolvable_before": n_resolved_before,
            "gold_units_resolvable_after": n_resolved_after,
            "newly_resolvable_gold_units": sorted(newly_resolved),
            "l4_repair_rows": len(repair_rows),
            "l4_repair_csv": str(repair_csv),
            "l5_propagation_rows": len(l5_rows),
            "l5_propagation_csv": str(l5_csv),
            "l7_correction_rows": sum(1 for r in l7_rows if r["source"] == "L7_correction"),
            "l7_propagation_rows": sum(1 for r in l7_rows if r["source"] == "L7_propagation"),
            "l7_repair_csv": str(l7_csv),
            "note": "L4 any-position header pass + L5 header-anchored propagation "
            "(G7, 2026-08-17) + L7 header-trust correction / amendment "
            "anchors (P2, 2026-08-18) lift Act-doc section coverage; L7 "
            "fills the paren-fragment gap in consolidated/amendment acts.",
        }
        summary_name = "backfill_summary_apply.json" if args.apply else "backfill_summary_dryrun.json"
        (snapshot_dir / summary_name).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

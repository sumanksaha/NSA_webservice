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
    except Exception:  # noqa: BLE001 - no registry => no L3 stamps
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
            if norm_docid(str(p.get("instrument_id") or "")) in wanted
            and p.get("document_id")
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
            "MATCH (i)-[:CONTAINS]->(p:LegalProvision) "
            "RETURN i.title AS instrument_title, p.provision_number AS number"
        )
        for r in rows:
            for fam in family_map.family_s_for_act(r.get("instrument_title")):
                n = base_digits(r.get("number"))
                if n:
                    maxima[fam] = max(maxima.get(fam, 0), int(n))
    except Exception as exc:  # noqa: BLE001 - best-effort
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
    return list(dict.fromkeys([
        cfg.get("RAG_QDRANT_COLLECTION", "fssai_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_ENV", "env_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_COMMERCIAL", "commercial_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_ANIMAL", "animal_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_WB_STATE", "wb_state_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_CRIMINAL", "criminal_legal_768"),
    ]))


# --------------------------------------------------------------------------- #
# Derivation
# --------------------------------------------------------------------------- #
def derive_section(point_id: str, payload: dict, kg_map: dict, maxima: dict, family_map) -> tuple[str | None, str | None]:
    """Return (section_number, section_title) for a point, or (None, None).

    L1 provision_id -> L2 KG -> L3 validated text regex.  Never overwrites
    an existing section_number.
    """
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
    fams = [
        f for f in fams
        if doc_whitelist.get(f) and payload_docid in doc_whitelist[f]
    ]
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
            batch = ids[i:i + batch_size]
            try:
                client.set_payload(collection_name=collection, payload=payload, points=batch)
                applied += len(batch)
            except TypeError:
                # some client versions use a different kwarg name
                client.set_payload(collection=collection, payload=payload, points=batch)
                applied += len(batch)
            except Exception as exc:  # noqa: BLE001 - retry per point
                logger.warning("set_payload batch failed (%s) — retrying per point", exc)
                for pid in batch:
                    try:
                        client.set_payload(collection_name=collection, payload=payload, points=[pid])
                        applied += 1
                    except Exception as exc2:  # noqa: BLE001
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
    parser.add_argument("--rebuild-index", action="store_true",
                        help="rebuild evaluation/out/cache/payload_index.jsonl after apply")
    parser.add_argument("--snapshot-dir", default=str(PROJECT_ROOT / "evaluation" / "out" / "ceiling_v2"),
                        help="dir for the pre-backfill payload snapshot")
    parser.add_argument("--from-cache", action="store_true",
                        help="dry-run against the frozen payload cache instead of scrolling live Qdrant "
                             "(collection provenance unknown -> 'cache'; apply still requires live scroll)")
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    collections = collections_from_config(app)
    snapshot_dir = Path(args.snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    with app.app_context():
        from evaluation.resolution import FamilyMap, matches_gold
        from evaluation.benchmark import load_questions

        if args.from_cache:
            if args.apply:
                print("--apply requires a live scroll; --from-cache is dry-run only.")
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
        repair_rows: list[dict] = []
        for point_id, payload in payloads.items():
            if point_id in changes:
                continue  # L1/L2/L3 took precedence
            covered, fams = derive_section_l4(point_id, payload, family_map, ranges, maxima)
            if not covered:
                continue
            current = base_digits(payload.get("section_number"))
            if current and current in covered:
                # stamp already correct — only record the covered set if absent
                if not payload.get("sections_covered"):
                    changes[point_id] = {"sections_covered": covered, "source": "L4_covered_add"}
                    layer_counts["L4_covered_add"] = layer_counts.get("L4_covered_add", 0) + 1
                continue
            layer = "L4_override" if current else "L4_text_new"
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
                "old_section_number": payload.get("section_number") or "",
                "new_section_number": covered[0],
                "sections_covered": ";".join(covered),
                "source": layer,
                "evidence": str(payload.get("chunk_text") or "")[:200].replace("\n", " "),
            })
        repair_csv = snapshot_dir / "repair_sections_l4.csv"
        with open(repair_csv, "w", newline="", encoding="utf-8") as f:
            import csv as _csv

            writer = _csv.DictWriter(f, fieldnames=[
                "collection", "point_id", "family", "old_section_number",
                "new_section_number", "sections_covered", "source", "evidence",
            ])
            writer.writeheader()
            writer.writerows(repair_rows)
        logger.info("L4 repair CSV -> %s (%d rows)", repair_csv, len(repair_rows))

        before = sum(1 for p in payloads.values() if p.get("section_number"))
        after = before + len(changes)
        print(f"section_number coverage: {before} ({before / len(payloads):.1%}) "
              f"-> {after} ({after / len(payloads):.1%})  (+{len(changes)} points)")
        print("by source:", layer_counts)
        from collections import Counter

        print("by collection:", dict(Counter(provenance.get(pid, "?") for pid in changes)))

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
                    matches_gold(dict(p, **changes.get(pid, {})), u, family_map)
                    for pid, p in payloads.items()
                )
                if hit_after:
                    newly_resolved.append(u.provision_id)
        n_resolved_after = n_resolved_before + len(newly_resolved)
        print(f"resolvable gold units: {n_resolved_before} -> {n_resolved_after} "
              f"(+{n_resolved_after - n_resolved_before})")
        print("newly resolvable:", sorted(newly_resolved))

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
                store = __import__("app.rag.qdrant_client", fromlist=["QdrantStore"]).QdrantStore(
                    collection_name=coll)
                n = set_payload_batched(store._get_client(), coll, chg)
                applied += n
                logger.info("collection %s: %d updates", coll, n)
            print(f"applied {applied} payload updates")

            if args.rebuild_index:
                from evaluation.resolution import build_payload_index
                from app.rag.qdrant_client import QdrantStore

                index = build_payload_index(
                    lambda coll: QdrantStore(collection_name=coll),
                    collections, force=True,
                )
                logger.info("payload index rebuilt: %d points", len(index))
        else:
            print("DRY-RUN — no writes. Re-run with --apply to write to Qdrant.")

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
            "note": "L4 any-position section-header pass closes the V7 candidate gap "
                    "(7 units; validated offline 91.9% -> 100% pool ceiling).",
        }
        summary_name = "backfill_summary_apply.json" if args.apply else "backfill_summary_dryrun.json"
        (snapshot_dir / summary_name).write_text(
            json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

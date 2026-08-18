"""Corpus-driven legal Knowledge Graph rebuild (Option B — 2026-08-11).

Rebuilds the Neo4j legal KG from the REAL multi-domain corpus instead of
the pilot stubs:

- **Manifest** (``other domain/manifest.json``) — the 26 non-FSSAI documents
  with authoritative metadata (``document_id``, ``document_type``,
  ``act_name``, ``authority``, ``jurisdiction``, ``state``, dates,
  ``is_current``).
- **Qdrant** (read-only) — every indexed chunk's §5.1 payload (point id =
  ``chunk_id``, section numbers, text) for real provision structure.
- **Local DB** — the 29 FSSAI ``LegalDocument`` records + 12,819
  ``LegalChunk`` rows (the primary-domain corpus).

Design rules (from the 2026-08-11 KG readiness audit):

1. **Corpus-truthful** — provisions come from sections actually detected in
   the corpus; the audit showed pilot stubs (e.g. a fictional ``KMC_ACT_2009``
   whose §6 was *not* the real KMC Act 1980 §6) must NOT be remapped onto
   real instruments. Edges whose endpoints do not exist are dropped, with the
   reason recorded in the summary.
2. **Domain edges for EVERY provision** — the audit's D1/D9 defect (37% of
   provisions invisible to domain queries) is fixed by wiring
   ``BELONGS_TO_DOMAIN`` on all provisions, instruments, and documents.
3. **Full provenance** — every document gets a ``Document`` node; every chunk
   is linked ``HAS_CHUNK`` (fixes the missing FSS Document node); provisions
   link ``SUPPORTED_BY`` to their section's chunks and ``SOURCE_OF`` to the
   document; ``source_uri`` is the real corpus path.
4. **Temporal honesty** — ``is_current`` maps to status (``draft`` for
   flagged drafts); supersession edges are written (FSS Act → PFA 1954
   ``REPEALS``; BNS 2023 → IPC 1860 ``REPLACES``; PWM 2022 amendments →
   PWM Rules 2016 ``AMENDS``); repealed acts exist as explicit ``repealed``
   stub instruments instead of silent ``current`` nodes.
5. **Batch writes** — all writes use ``UNWIND``-based MERGE batches (500)
   so the ~27k chunks ingest in minutes, memory-bounded.
6. **Vocabulary alignment** — manifest domains map onto the KG domain
   taxonomy (``criminal`` → ``CRIMINAL``, ``wb_state`` resolved per document
   to ``MUNICIPAL``/``LAND_PREMISES``); authorities resolve to the controlled
   vocabulary or are created.

The legacy case-file graph (``Case``/``FBO``/...) is never touched —
``clear_legal_kg`` only clears legal-instrument labels.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.rag.legal_sections import sections_for_act

# --------------------------------------------------------------------------- #
# Corpus constants
# --------------------------------------------------------------------------- #

#: Manifest document_id -> canonical instrument_id (reuses pilot IDs where the
#: act genuinely matches so existing concept/cross-domain registries stay valid).
DOCUMENT_TO_INSTRUMENT: dict[str, str] = {
    "environment_protection_act_1986": "ENV_PROTECTION_ACT_1986",
    "indian_contract_act_1872": "CONTRACT_ACT_1872",
    "water_act_1974": "WATER_ACT_1974",
    "air_act_1981": "AIR_ACT_1981",
    "consumer_protection_act_2019": "CONSUMER_PROTECTION_ACT_2019",
    "kmc_act_1980": "KMC_ACT_1980",
    "wb_premises_tenancy_act_1997": "WB_PREMISES_TENANCY_ACT_1997",
    "bharatiya_nyaya_sanhita_2023": "BNS_2023",
}

#: The FSS Act instrument/document identity (primary domain).
FSS_ACT_ID = "FSS_ACT_2006"

#: Manifest domain -> KG domain (``None`` = resolved per document).
MANIFEST_DOMAIN_TO_KG: dict[str, str | None] = {
    "fssai": "FOOD_SAFETY",
    "env": "ENVIRONMENT_POLLUTION",
    "commercial": "BUSINESS_CIVIL",
    "animal": "ANIMAL_SLAUGHTER",
    "criminal": "CRIMINAL",
    "wb_state": None,
}

#: Per-document resolution for the ``wb_state`` collection (KMC Act 1980 is
#: municipal law; WB Premises Tenancy Act 1997 is land/premises law).
WB_STATE_DOMAIN_MAP: dict[str, str] = {
    "kmc_act_1980": "MUNICIPAL",
    "wb_premises_tenancy_act_1997": "LAND_PREMISES",
}

#: Manifest ``document_type`` -> Neo4j instrument label.
DOC_TYPE_TO_LABEL: dict[str, str] = {
    "act": "Act",
    "rule": "Rule",
    "regulation": "Regulation",
    "notification": "Notification",
    "circular": "Circular",
    "order": "Order",
    "guideline": "Guideline",
    "case_law": "Judgment",
}

#: Authority-name normalisation -> existing authority_id (corpus strings).
AUTHORITY_ALIASES: dict[str, str] = {
    "parliament of india": "PARLIAMENT_OF_INDIA",
    "west bengal legislature": "WB_LEGISLATURE",
    "ministry of environment forest and climate change": "MOEFCC",
    "ministry of environment forest climate change": "MOEFCC",
    "ministry of fisheries animal husbandry and dairying": "MOFAHD",
    "government of west bengal": "WB_GOVT",
    "department of animal husbandry veterinary services west bengal": "WB_FODDER_DEPT",
    "food safety and standards authority of india": "FSSAI",
    "fssai": "FSSAI",
    "fsan": "FSSAI",
    "ministry of health and family welfare": "MOHFW",
    "m-ohfw": "MOHFW",
    "mohfw": "MOHFW",
    "ministry of law and justice": "MO_LAW",
    "mo lj": "MO_LAW",
    "mo_law": "MO_LAW",
    "legislative department": "MO_LAW",
    "central pollution control board": "CPCB",
    "west bengal pollution control board": "WBPCB",
    "kolkata municipal corporation": "KMC",
    "chief medical officer of health kmc": "KMC_HEALTH",
    "courts of india": "COURTS",
    "west bengal board of revenue land records": "WB_LAND_RECORDS",
}

#: Authorities to create on demand (id -> (name, short, jurisdiction, type)).
NEW_AUTHORITIES: dict[str, tuple[str, str, str, str]] = {
    "PARLIAMENT_OF_INDIA": ("Parliament of India", "Parliament", "INDIA", "legislature"),
    "WB_LEGISLATURE": ("West Bengal Legislature", "WB Legislature", "WEST_BENGAL", "legislature"),
    "MOFAHD": ("Ministry of Fisheries, Animal Husbandry and Dairying", "MoFAH&D", "INDIA", "department"),
    "WB_GOVT": ("Government of West Bengal", "WB Government", "WEST_BENGAL", "government"),
}

#: Jurisdiction strings -> Jurisdiction node id.
JURISDICTION_ALIASES: dict[str, str] = {
    "india": "INDIA",
    "government of india": "INDIA",
    "central government": "INDIA",
    "central government of india": "INDIA",
    "west bengal": "WEST_BENGAL",
    "kolkata": "KOLKATA",
    "kolkata (municipal corporation)": "KOLKATA",
}

#: Instrument-level relationships (rel_type, target_id, evidence) — applied
#: only when BOTH endpoints exist in the rebuilt graph.
CORPUS_INSTRUMENT_RELATIONSHIPS: list[tuple[str, str, str, str]] = [
    ("ENV_PROTECTION_ACT_1986", "RELATED_TO", "FSS_ACT_2006", "Both apply to food business operations (environmental compliance)."),
    ("ENV_PROTECTION_ACT_1986", "RELATED_TO", "WATER_ACT_1974", "Water Act addresses wastewater discharge regulated under the EP Act framework."),
    ("KMC_ACT_1980", "RELATED_TO", "FSS_ACT_2006", "Both apply to food business premises (municipal licensing and food safety)."),
    ("WB_PREMISES_TENANCY_ACT_1997", "RELATED_TO", "FSS_ACT_2006", "Both apply to food business establishment premises (tenancy and food safety)."),
    ("FSS_ACT_2006", "REPEALS", "PFA_1954", "FSS Act, 2006 repealed the Prevention of Food Adulteration Act, 1954."),
    ("BNS_2023", "REPLACES", "IPC_1860", "Bharatiya Nyaya Sanhita, 2023 (Act 45 of 2023) replaced the Indian Penal Code, 1860 w.e.f. 2024-07-01."),
    ("PWM_AMENDMENT_RULES_2022_JUL", "AMENDS", "PWM_RULES_2016", "Plastic Waste Management (Amendment) Rules, 2022 (gazette 07-07-2022) amend the PWM Rules, 2016."),
    ("PWM_AMENDMENT_RULES_2022_AUG", "AMENDS", "PWM_RULES_2016", "Plastic Waste Management (Amendment) Rules, 2022 (gazette 23-08-2022) amend the PWM Rules, 2016."),
    ("PWM_DRAFT_RULES_2022", "SUPERSEDED_BY", "PWM_AMENDMENT_RULES_2022_AUG", "Draft PWM Rules, 2022 (gazette 20-01-2022) superseded by the August 2022 amendments; not current law."),
    ("PWM_DRAFT_AMENDMENT_NOTIFICATION_2021", "SUPERSEDED_BY", "PWM_AMENDMENT_RULES_2022_AUG", "Draft amendment notification (12-08-2021) superseded by the July/August 2022 amendments; not current law."),
    ("EPR_DRAFT_NOTIFICATION_2021", "SUPERSEDED_BY", "PWM_AMENDMENT_RULES_2022_AUG", "Draft EPR notification (06-10-2021); draft, not current law."),
]

#: Provision-level cross-domain edges — corpus-truthful only.  The audit
#: (2026-08-11) verified each referenced provision exists in the corpus and
#: that the evidence matches the actual section content.  Pilot edges for the
#: fictional KMC Act 2009 / WB Animal Slaughter Rules 2023 / Contract Act §73
#: were dropped because their endpoints do not exist or their content was
#: unverifiable.
CORPUS_CROSS_DOMAIN_EDGES: list[tuple[str, str, str, str]] = [
    (
        "ENV_PROTECTION_ACT_1986_SEC_5",
        "COMPLEMENTS",
        "FSS_ACT_2006_SEC_31",
        "Environment (Protection) Act, 1986 s.5 (power to give directions restricting polluting operations) "
        "complements FSS Act s.31 licensing/hygiene obligations on food businesses.",
    ),
]

#: Stub instruments created solely for legal-structure completeness
#: (repeal / made-under chains).  Content is minimal and marked ``stub``;
#: these are NOT retrieval sources.
STUB_INSTRUMENTS: dict[str, dict[str, Any]] = {
    "PFA_1954": {
        "title": "Prevention of Food Adulteration Act, 1954",
        "short_title": "PFA Act, 1954",
        "instrument_type": "act",
        "legal_domain": "FOOD_SAFETY",
        "jurisdiction": "INDIA",
        "issuing_authority": "PARLIAMENT_OF_INDIA",
        "status": "repealed",
        "effective_date": "1954-01-01",
        "enactment_date": "1954-01-01",
        "source_uri": "",
        "source_type": "stub",
        "provisions": {"1": ("Short title", "This Act may be called the Prevention of Food Adulteration Act, 1954.")},
    },
    "IPC_1860": {
        "title": "Indian Penal Code, 1860",
        "short_title": "IPC, 1860",
        "instrument_type": "act",
        "legal_domain": "CRIMINAL",
        "jurisdiction": "INDIA",
        "issuing_authority": "PARLIAMENT_OF_INDIA",
        "status": "repealed",
        "effective_date": "1862-01-01",
        "enactment_date": "1860-10-06",
        "source_uri": "",
        "source_type": "stub",
        "provisions": {"1": ("Short title", "This Act shall be called the Indian Penal Code, 1860.")},
    },
    "PCA_1960": {
        "title": "Prevention of Cruelty to Animals Act, 1960",
        "short_title": "PCA Act, 1960",
        "instrument_type": "act",
        "legal_domain": "ANIMAL_SLAUGHTER",
        "jurisdiction": "INDIA",
        "issuing_authority": "PARLIAMENT_OF_INDIA",
        "status": "current",
        "effective_date": "1960-12-26",
        "enactment_date": "1960-12-26",
        "source_uri": "",
        "source_type": "stub",
        "provisions": {"1": ("Short title", "This Act may be called the Prevention of Cruelty to Animals Act, 1960.")},
    },
}


def _normalise(text: str | None) -> str:
    """Lowercase + collapse punctuation for mapping lookups."""
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(text).strip().lower()).strip()


def _slug_id(text: str, prefix: str = "") -> str:
    """Deterministic uppercase ID from a title (for instruments without an ID map)."""
    slug = re.sub(r"[^A-Z0-9]+", "_", str(text or "").upper()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return f"{prefix}{slug}"[:90]


def _title_or_filename(title: str | None, source_uri: str | None) -> str:
    """Fall back to a humanised filename stem when the DB title is empty.

    The FSSAI ``LegalDocument`` rows carry empty ``title`` values (their
    metadata lives in ``source_uri``), so the KG needs a readable name and a
    unique slug base derived from the file name.
    """
    name = str(title or "").strip()
    if name:
        return name
    stem = Path(str(source_uri or "")).stem.strip()
    if not stem:
        return "Untitled instrument"
    # ``Compendium_Licensing_Regulations_04_08_2021`` -> human readable
    return re.sub(r"_+", " ", stem).strip()


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


class KGCorpusIngestionEngine:
    """Rebuild the legal KG from the real corpus (manifest + Qdrant + DB).

    Args:
        driver: Optional pre-built Neo4j driver (injected for tests).
        database: Neo4j database name (default from ``NEO4J_DATABASE`` env).
        manifest_path: Path to ``other domain/manifest.json``.
        qdrant_client: Optional pre-built QdrantClient (injected for tests).
        batch_size: UNWIND batch size for writes.
    """

    def __init__(
        self,
        driver: Any | None = None,
        database: str | None = None,
        manifest_path: str | Path | None = None,
        qdrant_client: Any | None = None,
        batch_size: int = 500,
    ) -> None:
        self._driver = driver
        self._database = database or os.environ.get("NEO4J_DATABASE", "neo4j")
        self._own_driver = False
        self._manifest_path = Path(manifest_path or "other domain/manifest.json")
        self._qdrant_client = qdrant_client
        self._batch_size = batch_size
        #: Cached corpus: collection -> document_id -> list[chunk dicts]
        self._qdrant_chunks: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._qdrant_loaded = False
        #: Cached FSS DB corpus (documents + chunks) — loaded in ONE app context
        self._fss_cache_data: tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]] | None = None

    # ------------------------------------------------------------------ #
    # Driver / client plumbing
    # ------------------------------------------------------------------ #

    def _get_driver(self) -> Any:
        if self._driver is None:
            from app.services.neo4j_graph import _get_driver

            self._driver = _get_driver()
            self._own_driver = True
        return self._driver

    def _execute(self, cypher: str, params: dict | None = None) -> list[dict]:
        driver = self._get_driver()
        result = driver.execute_query(cypher, parameters_=params or {}, database_=self._database)
        return [dict(r) for r in result.records]

    def _get_qdrant_client(self) -> Any:
        if self._qdrant_client is None:
            from qdrant_client import QdrantClient

            url = os.environ.get("RAG_QDRANT_URL", "")
            if not url:
                raise RuntimeError("RAG_QDRANT_URL not set — cannot read corpus chunks from Qdrant")
            self._qdrant_client = QdrantClient(url=url, api_key=os.environ.get("RAG_QDRANT_API_KEY") or None)
        return self._qdrant_client

    # ------------------------------------------------------------------ #
    # Corpus loading (read-only)
    # ------------------------------------------------------------------ #

    def load_manifest_documents(self) -> list[dict[str, Any]]:
        """Return manifest rows with ``ingest != false`` (metadata authority)."""
        data = json_load(self._manifest_path)
        rows = [r for r in data.get("documents", []) if r.get("ingest") is not False]
        return rows

    def load_qdrant_chunks(self, collections: Iterable[str] | None = None) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """Scroll every configured Qdrant collection once (read-only).

        Returns ``{collection: {document_id: [chunk_dict, ...]}}`` where each
        chunk dict carries the §5.1 payload fields the KG needs.
        """
        if self._qdrant_loaded:
            return self._qdrant_chunks
        client = self._get_qdrant_client()
        if collections is None:
            collections = [c.name for c in client.get_collections().collections]
        for coll in sorted(collections):
            by_doc: dict[str, list[dict[str, Any]]] = {}
            offset: Any = None
            pages = 0
            while True:
                recs, offset = client.scroll(
                    collection_name=coll, limit=1000, with_payload=True, with_vectors=False, offset=offset
                )
                if not recs:
                    break
                for rec in recs:
                    # Real qdrant-client returns Record objects (``.payload``);
                    # test doubles may hand us plain dicts with a ``payload`` key.
                    if isinstance(rec, dict):
                        payload = dict(rec.get("payload") or rec)
                        rec_id = rec.get("id")
                    else:
                        payload = dict(getattr(rec, "payload", None) or {})
                        rec_id = getattr(rec, "id", None)
                    if not payload.get("chunk_id") and rec_id:
                        payload["chunk_id"] = str(rec_id)
                    doc_id = str(payload.get("document_id") or "")
                    by_doc.setdefault(doc_id, []).append(payload)
                pages += 1
                if not offset:
                    break
                if pages > 20_000:
                    raise RuntimeError(f"Qdrant scroll over {coll} did not terminate")
            self._qdrant_chunks[coll] = by_doc
        self._qdrant_loaded = True
        return self._qdrant_chunks

    def _fss_corpus(self) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        """Load FSS documents + chunks in ONE app context (cached).

        ``create_app()`` is expensive (~7s) and re-emits startup warnings, so
        the two read paths share a single app context instead of each
        spinning up the factory.
        """
        if self._fss_cache_data is not None:
            return self._fss_cache_data
        from app import create_app
        from app.extensions import db
        from app.models import LegalChunk, LegalDocument

        app = create_app()
        docs: list[dict[str, Any]] = []
        chunks: dict[str, list[dict[str, Any]]] = {}
        with app.app_context():
            for doc in db.session.execute(db.select(LegalDocument)).scalars().all():
                docs.append(
                    {
                        "db_id": doc.id,
                        "title": doc.title or "",
                        "document_type": doc.document_type,
                        "source_uri": doc.source_uri or "",
                        "authority": doc.authority or "",
                        "jurisdiction": doc.jurisdiction or "",
                        "effective_date": _iso(doc.effective_date),
                        "enactment_date": _iso(doc.enactment_date),
                        "is_current": doc.is_current,
                        "qdrant_collection": doc.qdrant_collection or "fssai_legal_768",
                        "chunk_count": doc.chunk_count or 0,
                        "is_fss_act": bool(re.search(r"Food[_ ]?Safety[_ ]?and[_ ]?Standards[_ ]?Act[_ ]?2006", doc.source_uri or "")),
                    }
                )
            rows = db.session.execute(
                db.select(LegalChunk).order_by(LegalChunk.document_id, LegalChunk.chunk_index)
            ).scalars().all()
            for ch in rows:
                chunks.setdefault(ch.document_id, []).append(
                    {
                        "chunk_id": ch.id,
                        "qdrant_point_id": ch.qdrant_point_id or "",
                        "document_id": ch.document_id,
                        "chunk_index": ch.chunk_index,
                        "chunk_text": (ch.text or "")[:500],
                        "section_number": _clean_section(ch.section_number),
                    }
                )
        self._fss_cache_data = (docs, chunks)
        return self._fss_cache_data

    def load_fss_documents(self) -> list[dict[str, Any]]:
        """Read the 29 FSSAI ``LegalDocument`` rows from the local DB (cached)."""
        return self._fss_corpus()[0]

    def load_all_fss_chunks(self) -> dict[str, list[dict[str, Any]]]:
        """Load every ``LegalChunk`` row (cached, single app context).

        Returns ``{document_id: [chunk dicts]}`` for all 29 FSSAI documents.
        """
        return self._fss_corpus()[1]

    # ------------------------------------------------------------------ #
    # Mapping helpers
    # ------------------------------------------------------------------ #

    def resolve_domain(self, row: dict[str, Any]) -> str:
        """Map a manifest row to a KG ``LegalDomain`` name."""
        mdomain = str(row.get("domain") or "").strip().lower()
        kg = MANIFEST_DOMAIN_TO_KG.get(mdomain)
        if kg:
            return kg
        if mdomain == "wb_state":
            return WB_STATE_DOMAIN_MAP.get(str(row.get("document_id") or ""), "LAND_PREMISES")
        return "FOOD_SAFETY"

    def resolve_jurisdiction(self, row: dict[str, Any]) -> str:
        """Map manifest ``jurisdiction``/``state`` to a Jurisdiction node id.

        The more specific ``state`` field wins: a West Bengal instrument
        carries ``jurisdiction: "India"`` in the manifest but its law is
        state law, so it must resolve to ``WEST_BENGAL``.
        """
        jur = _normalise(row.get("jurisdiction"))
        state = _normalise(row.get("state"))
        if state in ("west bengal",):
            return "WEST_BENGAL"
        if state in ("kolkata",):
            return "KOLKATA"
        if jur in JURISDICTION_ALIASES:
            return JURISDICTION_ALIASES[jur]
        return "INDIA"

    def resolve_authority(self, name: str | None, domain: str) -> str:
        """Resolve an authority name to a controlled authority id (creating one if needed)."""
        key = _normalise(name)
        if not key:
            return "FSSAI" if domain == "FOOD_SAFETY" else "MO_LAW"
        if key in AUTHORITY_ALIASES:
            return AUTHORITY_ALIASES[key]
        # contains-fallback against known names (e.g. "...West Bengal..." variants)
        from kg.domain_manifest import AUTHORITIES

        for known in AUTHORITIES.values():
            if key and key in _normalise(known.name):
                return known.authority_id
        # Unknown authority — register a new one deterministically.
        aid = _slug_id(name, "AUTH_")
        if aid not in NEW_AUTHORITIES:
            NEW_AUTHORITIES[aid] = (str(name).strip(), str(name).strip()[:24], "INDIA", "department")
        return aid

    def resolve_instrument_id(self, row: dict[str, Any]) -> str:
        """Canonical instrument id for a manifest row.

        The manifest ``document_id`` is unique per document and is the safe
        default base — ``act_name`` must NOT be used (multiple documents of
        the same Act would collide, e.g. the PWM Rules 2016 + its 2022
        amendments all share the EP Act 1986 as ``act_name``).
        """
        doc_id = str(row.get("document_id") or "")
        if doc_id in DOCUMENT_TO_INSTRUMENT:
            return DOCUMENT_TO_INSTRUMENT[doc_id]
        if doc_id:
            return _slug_id(doc_id)
        return _slug_id(row.get("act_name") or row.get("title") or "doc")

    def instrument_status(self, row: dict[str, Any]) -> str:
        """Map manifest ``is_current`` + notes to a status."""
        if row.get("is_current") is False:
            notes = str(row.get("notes") or "").lower()
            if "draft" in notes:
                return "draft"
            return "superseded"
        return "current"

    # ------------------------------------------------------------------ #
    # Provision building
    # ------------------------------------------------------------------ #

    def build_provisions(
        self,
        instrument_id: str,
        act_name: str | None,
        chunks: list[dict[str, Any]],
        fallback_stubs: dict[str, tuple[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Group corpus chunks into provisions by (valid) section number.

        Returns provision dicts ``{provision_id, provision_number, title,
        text, instrument_id, domain, status, confidence, source, chunk_ids}``.
        Sections are validated against the act's registered range where known
        and against junk (year-like numbers).  ``fallback_stubs`` optionally
        adds verified section content for sections the chunker missed (stub
        instruments only — never for corpus instruments).
        """
        known = sections_for_act(act_name) if act_name else None
        sections: dict[str, dict[str, Any]] = {}
        for chunk in chunks:
            sn = chunk.get("section_number")
            if not sn:
                continue
            if not _valid_section(sn, known):
                continue
            entry = sections.setdefault(
                sn,
                {
                    "title": chunk.get("section_title") or _section_title_from_text(chunk.get("chunk_text", "")),
                    "text": (chunk.get("chunk_text") or "").strip(),
                    "chunk_ids": [],
                },
            )
            if not entry["text"]:
                entry["text"] = (chunk.get("chunk_text") or "").strip()
            entry["chunk_ids"].append(chunk.get("chunk_id") or "")
            if entry.get("header_chunk_id") is None and chunk.get("section_title"):
                entry["header_chunk_id"] = chunk.get("chunk_id")

        for sn, (title, text) in (fallback_stubs or {}).items():
            if sn not in sections:
                sections[sn] = {"title": title, "text": text, "chunk_ids": [], "header_chunk_id": None}

        provisions: list[dict[str, Any]] = []
        for sn in sorted(sections, key=lambda s: (len(s), s)):
            entry = sections[sn]
            provisions.append(
                {
                    "provision_id": f"{instrument_id}_SEC_{sn}",
                    "provision_number": sn,
                    "title": entry["title"] or f"Section {sn}",
                    "text": entry["text"][:2000],
                    "instrument_id": instrument_id,
                    "chunk_ids": entry["chunk_ids"],
                    "header_chunk_id": entry.get("header_chunk_id"),
                    "source": "corpus_qdrant" if entry["chunk_ids"] else "stub",
                    "confidence": 0.9 if entry["chunk_ids"] else 0.6,
                }
            )
        return provisions

    # ------------------------------------------------------------------ #
    # Write helpers (batched UNWIND MERGE)
    # ------------------------------------------------------------------ #

    def _write_rows(self, cypher: str, rows: list[dict[str, Any]], label: str = "rows") -> int:
        """Execute a batched ``UNWIND $rows`` write; returns rows written."""
        written = 0
        for i in range(0, len(rows), self._batch_size):
            batch = rows[i : i + self._batch_size]
            self._execute(cypher, {label: batch})
            written += len(batch)
        return written

    # ------------------------------------------------------------------ #
    # Build steps
    # ------------------------------------------------------------------ #

    def _build_instrument_rows(self) -> tuple[list[dict[str, Any]], list[str]]:
        """Assemble instrument rows (manifest + FSS DB + stubs)."""
        rows: list[dict[str, Any]] = []
        domains_used: list[str] = []

        # Non-FSSAI manifest documents
        for row in self.load_manifest_documents():
            instrument_id = self.resolve_instrument_id(row)
            domain = self.resolve_domain(row)
            domains_used.append(domain)
            label = DOC_TYPE_TO_LABEL.get(str(row.get("document_type") or "").lower(), "Act")
            rows.append(
                {
                    "instrument_id": instrument_id,
                    "document_id": str(row.get("document_id") or ""),
                    "title": str(row.get("title") or ""),
                    "short_title": str(row.get("act_name") or row.get("title") or instrument_id),
                    "instrument_type": str(row.get("document_type") or "").lower(),
                    "label": label,
                    "legal_domain": domain,
                    "jurisdiction": self.resolve_jurisdiction(row),
                    "authority_id": self.resolve_authority(row.get("authority"), domain),
                    "enactment_date": row.get("enactment_date"),
                    "effective_date": row.get("effective_date"),
                    "status": self.instrument_status(row),
                    "source_uri": f"other domain/{row.get('file') or ''}",
                    "source_type": "corpus_manifest",
                    "canonical_name": _normalise_name(row.get("act_name") or row.get("title") or ""),
                    "act_name": str(row.get("act_name") or ""),
                }
            )

        # FSSAI documents from the local DB
        for doc in self.load_fss_documents():
            display_title = _title_or_filename(doc.get("title"), doc.get("source_uri"))
            # Non-FSS multi-part PDFs share one filename stem ("a.pdf#<doc-id>"),
            # so the stem alone would collide and silently drop sub-documents.
            # Disambiguate with the DB primary key: deterministic and unique.
            instrument_id = (
                FSS_ACT_ID
                if doc["is_fss_act"]
                else f"{_slug_id(display_title, 'FSS_')}_{doc['db_id'][:8]}"
            )
            domain = "FOOD_SAFETY"
            domains_used.append(domain)
            label = DOC_TYPE_TO_LABEL.get(str(doc["document_type"] or "").lower(), "Act")
            rows.append(
                {
                    "instrument_id": instrument_id,
                    "document_id": doc["db_id"],
                    "title": display_title,
                    "short_title": _normalise_name(display_title),
                    "instrument_type": str(doc["document_type"] or "").lower(),
                    "label": label,
                    "legal_domain": domain,
                    "jurisdiction": "INDIA",
                    "authority_id": self.resolve_authority(doc["authority"], domain),
                    "enactment_date": doc["enactment_date"],
                    "effective_date": doc["effective_date"],
                    "status": "current" if doc["is_current"] else "draft",
                    "source_uri": doc["source_uri"],
                    "source_type": "existing_db",
                    "canonical_name": _normalise_name(display_title),
                    "act_name": "Food Safety and Standards Act, 2006" if doc["is_fss_act"] else "",
                }
            )

        # Structural stubs (repealed/parent acts — not retrieval sources)
        for sid, spec in STUB_INSTRUMENTS.items():
            rows.append(
                {
                    "instrument_id": sid,
                    "document_id": sid,
                    "title": spec["title"],
                    "short_title": spec["short_title"],
                    "instrument_type": spec["instrument_type"],
                    "label": DOC_TYPE_TO_LABEL.get(spec["instrument_type"], "Act"),
                    "legal_domain": spec["legal_domain"],
                    "jurisdiction": spec["jurisdiction"],
                    "authority_id": spec["issuing_authority"],
                    "enactment_date": spec["enactment_date"],
                    "effective_date": spec["effective_date"],
                    "status": spec["status"],
                    "source_uri": spec["source_uri"],
                    "source_type": spec["source_type"],
                    "canonical_name": _normalise_name(spec["title"]),
                    "act_name": "",
                }
            )
            domains_used.append(spec["legal_domain"])
        return rows, domains_used

    def load_vocabularies(self) -> dict[str, int]:
        """Insert controlled vocabularies (incl. corpus-extended ones)."""
        from kg.ingestion import LegalKGIngestionEngine

        pilot = LegalKGIngestionEngine(driver=self._get_driver(), database=self._database)
        return pilot.load_vocabularies()

    def write_new_authorities(self) -> int:
        """MERGE authorities created on demand during collect (``NEW_AUTHORITIES``).

        ``resolve_authority`` may register unknown authorities while rows are
        being assembled; without a node, the ``ISSUED_BY`` edge to them would
        be silently skipped.  This step materialises those nodes before
        ``write_instruments`` runs.
        """
        if not NEW_AUTHORITIES:
            return 0
        rows = [
            {
                "authority_id": aid,
                "name": name,
                "short_name": short,
                "jurisdiction": jur,
                "authority_type": kind,
            }
            for aid, (name, short, jur, kind) in NEW_AUTHORITIES.items()
        ]
        return self._write_rows(
            """
            UNWIND $rows AS r
            MERGE (a:Authority {authority_id: r.authority_id})
            ON CREATE SET
                a.name = r.name, a.short_name = r.short_name,
                a.jurisdiction = r.jurisdiction, a.authority_type = r.authority_type
            ON MATCH SET a.name = r.name, a.authority_type = r.authority_type
            """,
            rows,
        )

    def write_instruments(self, rows: list[dict[str, Any]]) -> int:
        """MERGE instrument nodes (grouped by label) + domain/jurisdiction/authority edges."""
        by_label: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_label.setdefault(r["label"], []).append(r)
        total = 0
        for label, batch in by_label.items():
            total += self._write_rows(
                f"""
                UNWIND $rows AS r
                MERGE (i:{label} {{instrument_id: r.instrument_id}})
                ON CREATE SET
                    i.title = r.title, i.short_title = r.short_title,
                    i.instrument_type = r.instrument_type, i.legal_domain = r.legal_domain,
                    i.jurisdiction = r.jurisdiction, i.issuing_authority = r.authority_id,
                    i.enactment_date = r.enactment_date, i.effective_date = r.effective_date,
                    i.status = r.status, i.version = '1.0', i.source_url = r.source_uri,
                    i.source_type = r.source_type, i.canonical_name = r.canonical_name,
                    i.last_verified = datetime()
                ON MATCH SET
                    i.title = r.title, i.short_title = r.short_title,
                    i.instrument_type = r.instrument_type, i.legal_domain = r.legal_domain,
                    i.jurisdiction = r.jurisdiction, i.issuing_authority = r.authority_id,
                    i.enactment_date = r.enactment_date, i.effective_date = r.effective_date,
                    i.status = r.status, i.source_url = r.source_uri,
                    i.source_type = r.source_type, i.canonical_name = r.canonical_name
                """,
                batch,
            )
        # Domain edges
        self._write_rows(
            """
            UNWIND $rows AS r
            MATCH (i:Act|Rule|Regulation|Notification|Circular|Order|Guideline|Judgment {instrument_id: r.instrument_id})
            MATCH (d:LegalDomain {domain_name: r.domain})
            MERGE (i)-[:BELONGS_TO_DOMAIN]->(d)
            """,
            [{"instrument_id": r["instrument_id"], "domain": r["legal_domain"]} for r in rows],
        )
        # Jurisdiction edges
        self._write_rows(
            """
            UNWIND $rows AS r
            MATCH (i:Act|Rule|Regulation|Notification|Circular|Order|Guideline|Judgment {instrument_id: r.instrument_id})
            MATCH (j:Jurisdiction {jurisdiction_id: r.jurisdiction})
            MERGE (i)-[:APPLIES_TO_JURISDICTION]->(j)
            """,
            [{"instrument_id": r["instrument_id"], "jurisdiction": r["jurisdiction"]} for r in rows],
        )
        # Authority edges
        self._write_rows(
            """
            UNWIND $rows AS r
            MATCH (i:Act|Rule|Regulation|Notification|Circular|Order|Guideline|Judgment {instrument_id: r.instrument_id})
            MATCH (a:Authority {authority_id: r.authority_id})
            MERGE (i)-[:ISSUED_BY]->(a)
            """,
            [{"instrument_id": r["instrument_id"], "authority_id": r["authority_id"]} for r in rows],
        )
        return total

    def write_documents(self, rows: list[dict[str, Any]]) -> int:
        """MERGE Document nodes (one per corpus document) + domain edges."""
        total = self._write_rows(
            """
            UNWIND $rows AS r
            MERGE (d:Document {document_id: r.document_id})
            ON CREATE SET d.title = r.title, d.document_type = r.instrument_type,
                d.legal_domain = r.legal_domain, d.source_uri = r.source_uri,
                d.source_type = r.source_type, d.qdrant_collection = coalesce(r.qdrant_collection, '')
            ON MATCH SET d.title = r.title, d.legal_domain = r.legal_domain, d.source_uri = r.source_uri
            """,
            rows,
        )
        self._write_rows(
            """
            UNWIND $rows AS r
            MATCH (d:Document {document_id: r.document_id})
            MATCH (dom:LegalDomain {domain_name: r.legal_domain})
            MERGE (d)-[:BELONGS_TO_DOMAIN]->(dom)
            """,
            [{"document_id": r["document_id"], "legal_domain": r["legal_domain"]} for r in rows],
        )
        return total

    def write_provisions(self, rows: list[dict[str, Any]]) -> int:
        """MERGE provisions + CONTAINS + BELONGS_TO_DOMAIN + SOURCE_OF edges.

        Provision status INHERITS the parent instrument's status (remediation
        P3, 2026-08-11): a provision under a ``repealed``/``draft``/
        ``superseded`` instrument must never default to ``current``.  Rows
        carry ``status`` (set in :meth:`collect` from the instrument row);
        ``coalesce(r.status, 'current')`` keeps legacy rows safe.
        """
        total = self._write_rows(
            """
            UNWIND $rows AS r
            MERGE (p:LegalProvision {provision_id: r.provision_id})
            ON CREATE SET
                p.provision_number = r.provision_number, p.title = r.title,
                p.instrument_id = r.instrument_id, p.legal_domain = r.legal_domain,
                p.status = coalesce(r.status, 'current'), p.effective_from = r.effective_from,
                p.confidence = r.confidence, p.source = r.source,
                p.provision_text = r.text, p.version = '1.0'
            ON MATCH SET
                p.title = r.title, p.legal_domain = r.legal_domain,
                p.provision_text = r.text, p.confidence = r.confidence,
                p.status = coalesce(r.status, 'current')
            """,
            rows,
        )
        self._write_rows(
            """
            UNWIND $rows AS r
            MATCH (i {instrument_id: r.instrument_id})
            WHERE i:Act OR i:Rule OR i:Regulation OR i:Notification OR i:Circular OR i:Order OR i:Guideline OR i:Judgment
            MATCH (p:LegalProvision {provision_id: r.provision_id})
            MERGE (i)-[:CONTAINS]->(p)
            """,
            [{"instrument_id": r["instrument_id"], "provision_id": r["provision_id"]} for r in rows],
        )
        # Domain edges for EVERY provision (audit D1 fix)
        self._write_rows(
            """
            UNWIND $rows AS r
            MATCH (p:LegalProvision {provision_id: r.provision_id})
            MATCH (d:LegalDomain {domain_name: r.legal_domain})
            MERGE (p)-[:BELONGS_TO_DOMAIN]->(d)
            """,
            [{"provision_id": r["provision_id"], "legal_domain": r["legal_domain"]} for r in rows],
        )
        # Source documents
        self._write_rows(
            """
            UNWIND $rows AS r
            MATCH (p:LegalProvision {provision_id: r.provision_id})
            MATCH (d:Document {document_id: r.document_id})
            MERGE (p)-[:SOURCE_OF]->(d)
            """,
            [{"provision_id": r["provision_id"], "document_id": r["document_id"]} for r in rows],
        )
        return total

    def write_chunks(self, rows: list[dict[str, Any]]) -> int:
        """MERGE Chunk nodes + HAS_CHUNK (document) edges + SUPPORTED_BY (provision) edges."""
        total = self._write_rows(
            """
            UNWIND $rows AS r
            MERGE (c:Chunk {chunk_id: r.chunk_id})
            ON CREATE SET
                c.document_id = r.document_id, c.chunk_index = r.chunk_index,
                c.chunk_text = r.chunk_text, c.qdrant_point_id = coalesce(r.qdrant_point_id, ''),
                c.section_number = r.section_number, c.legal_domain = r.legal_domain,
                c.qdrant_collection = r.qdrant_collection
            ON MATCH SET
                c.chunk_index = r.chunk_index,
                c.qdrant_point_id = coalesce(r.qdrant_point_id, c.qdrant_point_id),
                c.legal_domain = r.legal_domain
            """,
            rows,
        )
        self._write_rows(
            """
            UNWIND $rows AS r
            MATCH (d:Document {document_id: r.document_id})
            MATCH (c:Chunk {chunk_id: r.chunk_id})
            MERGE (d)-[:HAS_CHUNK]->(c)
            """,
            [{"document_id": r["document_id"], "chunk_id": r["chunk_id"]} for r in rows],
        )
        # Only chunks that carry a section number support a provision
        prov_rows = [
            {"provision_id": r["provision_id"], "chunk_id": r["chunk_id"]}
            for r in rows
            if r.get("provision_id")
        ]
        self._write_rows(
            """
            UNWIND $rows AS r
            MATCH (p:LegalProvision {provision_id: r.provision_id})
            MATCH (c:Chunk {chunk_id: r.chunk_id})
            MERGE (p)-[rel:SUPPORTED_BY]->(c)
            ON CREATE SET rel.confidence = 0.8, rel.evidence_type = 'corpus_chunk'
            """,
            prov_rows,
        )
        return total

    def write_cross_domain(self, instrument_ids: set[str], provision_ids: set[str]) -> dict[str, Any]:
        """Write corpus-truthful cross-domain + instrument relationships.

        Every edge is applied only when both endpoints exist; skipped edges
        are reported.  Returns ``{"written": N, "skipped": [..]}``.
        """
        stats: dict[str, Any] = {"written": 0, "skipped": []}

        # Provision-level cross-domain edges
        for src, rel, tgt, evidence in CORPUS_CROSS_DOMAIN_EDGES:
            if src not in provision_ids or tgt not in provision_ids:
                stats["skipped"].append(f"{src} -[{rel}]-> {tgt} (endpoint missing)")
                continue
            self._execute(
                """
                MATCH (a:LegalProvision {provision_id: $src})
                MATCH (b:LegalProvision {provision_id: $tgt})
                MERGE (a)-[r:COMPLEMENTS]->(b)
                ON CREATE SET r.evidence = $evidence, r.confidence = 0.85,
                    r.evidence_type = 'corpus_cross_domain'
                """,
                {"src": src, "tgt": tgt, "evidence": evidence},
            )
            stats["written"] += 1

        # Instrument-level relationships (supersession + related)
        for src, rel, tgt, evidence in CORPUS_INSTRUMENT_RELATIONSHIPS:
            if src not in instrument_ids or tgt not in instrument_ids:
                stats["skipped"].append(f"{src} -[{rel}]-> {tgt} (endpoint missing)")
                continue
            self._execute(
                f"""
                MATCH (a:Act|Rule|Regulation|Notification|Circular|Order|Guideline|Judgment {{instrument_id: $src}})
                MATCH (b:Act|Rule|Regulation|Notification|Circular|Order|Guideline|Judgment {{instrument_id: $tgt}})
                MERGE (a)-[r:{rel}]->(b)
                ON CREATE SET r.evidence = $evidence, r.confidence = 0.9, r.evidence_type = 'corpus_source'
                ON MATCH SET r.evidence = $evidence
                """,
                {"src": src, "tgt": tgt, "evidence": evidence},
            )
            stats["written"] += 1
        return stats

    def write_concept_edges(self, provision_ids: set[str]) -> dict[str, Any]:
        """Apply the evidence-tagged concept map where provisions exist.

        (FSS Act sections + verified corpus sections; pilot-stub references
        whose provisions no longer exist are skipped and reported.)
        """
        from kg.domain_manifest import PROVISION_CONCEPT_MAP

        stats: dict[str, Any] = {"written": 0, "skipped": []}
        for pid, mappings in PROVISION_CONCEPT_MAP.items():
            if pid not in provision_ids:
                stats["skipped"].append(pid)
                continue
            for concept_id, rel_type, evidence in mappings:
                # Targets may be LegalConcept (concept_id) OR Authority
                # (authority_id) — the concept map mixes both vocabularies.
                self._execute(
                    f"""
                    MATCH (p:LegalProvision {{provision_id: $pid}})
                    MATCH (c) WHERE c.concept_id = $cid OR c.authority_id = $cid
                    MERGE (p)-[r:{rel_type}]->(c)
                    ON CREATE SET r.evidence = $evidence, r.confidence = 0.9,
                        r.evidence_type = 'source_supported'
                    ON MATCH SET r.evidence = $evidence
                    """,
                    {"pid": pid, "cid": concept_id, "evidence": evidence},
                )
                stats["written"] += 1
        return stats

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #

    def collect(self) -> dict[str, Any]:
        """Build ALL graph rows in memory (shared by dry-run and live rebuild)."""
        stats: dict[str, Any] = {}
        instrument_rows, _ = self._build_instrument_rows()
        stats["instruments"] = len(instrument_rows)
        stats["documents"] = len(instrument_rows)

        # Provisions + chunks per instrument
        provisions: list[dict[str, Any]] = []
        chunks: list[dict[str, Any]] = []
        provision_ids: set[str] = set()
        instrument_ids: set[str] = set()

        # Qdrant chunks keyed by document_id (multi-domain + any FSS points)
        qdrant_by_doc: dict[str, list[dict[str, Any]]] = {}
        for _coll, docs in self.load_qdrant_chunks().items():
            for doc_id, pts in docs.items():
                qdrant_by_doc.setdefault(doc_id, []).extend(pts)

        # Stub provisions (repealed/parent acts)
        for sid, spec in STUB_INSTRUMENTS.items():
            instrument_ids.add(sid)
            stub_provs = self.build_provisions(sid, None, [], fallback_stubs=spec["provisions"])
            for p in stub_provs:
                p["legal_domain"] = spec["legal_domain"]
                p["effective_from"] = spec["effective_date"]
                p["status"] = spec["status"]
                p["document_id"] = sid
                provisions.append(p)
                provision_ids.add(p["provision_id"])
            stats.setdefault("stub_provisions", 0)
            stats["stub_provisions"] += len(stub_provs)

        # Multi-domain instruments: chunks from Qdrant
        for row in instrument_rows:
            iid = row["instrument_id"]
            instrument_ids.add(iid)
            doc_id = row["document_id"]
            if row.get("source_type") == "existing_db":
                continue  # FSS handled below (DB is authoritative)
            if iid in STUB_INSTRUMENTS:
                continue
            pts = qdrant_by_doc.get(doc_id, [])
            payload_chunks = [
                {
                    "chunk_id": str(p.get("chunk_id") or ""),
                    "qdrant_point_id": str(p.get("chunk_id") or ""),
                    "document_id": doc_id,
                    "chunk_index": int(p.get("chunk_index") or 0),
                    "chunk_text": (p.get("chunk_text") or "")[:500],
                    "section_number": _clean_section(p.get("section_number")),
                    "section_title": p.get("section_title"),
                }
                for p in pts
            ]
            provs = self.build_provisions(iid, row.get("act_name"), payload_chunks)
            valid_secs = {p["provision_number"] for p in provs}
            for p in provs:
                p["legal_domain"] = row["legal_domain"]
                p["effective_from"] = row["effective_date"]
                p["status"] = row["status"]
                p["document_id"] = doc_id
                provisions.append(p)
                provision_ids.add(p["provision_id"])
            chunks.extend(
                {
                    **c,
                    "legal_domain": row["legal_domain"],
                    "provision_id": (
                        f"{iid}_SEC_{c['section_number']}" if c["section_number"] in valid_secs else None
                    ),
                    "qdrant_collection": _collection_for_domain(row["legal_domain"]),
                }
                for c in payload_chunks
            )
            stats.setdefault("qdrant_chunks", 0)
            stats["qdrant_chunks"] += len(payload_chunks)

        # FSS documents: chunks from the local DB (authoritative)
        fss_chunks = self.load_all_fss_chunks()
        fss_docs = {r["instrument_id"]: r for r in instrument_rows if r.get("source_type") == "existing_db"}
        for iid, row in fss_docs.items():
            doc_db_id = row["document_id"]
            page = fss_chunks.get(doc_db_id, [])
            provs = self.build_provisions(iid, row.get("act_name") or None, page)
            valid_secs = {p["provision_number"] for p in provs}
            for p in provs:
                p["legal_domain"] = row["legal_domain"]
                p["effective_from"] = row["effective_date"]
                p["status"] = row["status"]
                p["document_id"] = doc_db_id
                provisions.append(p)
                provision_ids.add(p["provision_id"])
            chunks.extend(
                {
                    **c,
                    "legal_domain": row["legal_domain"],
                    "provision_id": (
                        f"{iid}_SEC_{c['section_number']}" if c["section_number"] in valid_secs else None
                    ),
                    "qdrant_collection": row.get("qdrant_collection", "fssai_legal_768"),
                }
                for c in page
            )

        stats["provisions"] = len(provisions)
        stats["chunks"] = len(chunks)
        stats["provisions_with_domain"] = len(provisions)  # every provision gets a domain edge

        # Provision text/title enrichment from best chunks happens inside
        # build_provisions; ensure document rows carry qdrant_collection.
        docs = [
            {
                "document_id": r["document_id"],
                "title": r["title"],
                "instrument_type": r["instrument_type"],
                "legal_domain": r["legal_domain"],
                "source_uri": r["source_uri"],
                "source_type": r["source_type"],
                "qdrant_collection": r.get("qdrant_collection", _collection_for_domain(r["legal_domain"])),
            }
            for r in instrument_rows
        ]

        return {
            "instruments": instrument_rows,
            "documents": docs,
            "provisions": provisions,
            "chunks": chunks,
            "provision_ids": provision_ids,
            "instrument_ids": instrument_ids,
            "stats": stats,
        }

    def run_rebuild(self, clear: bool = True, dry_run: bool = False) -> dict[str, Any]:
        """Rebuild the legal KG from the corpus.

        Args:
            clear: Delete existing legal-KG nodes first (idempotent re-run).
            dry_run: Assemble everything and report counts WITHOUT writing.

        Returns:
            Summary dict (counts per entity type, skipped edges, timings).
        """
        from kg.schema import setup_legal_kg_schema

        started = datetime.now(UTC)
        summary: dict[str, Any] = {"dry_run": dry_run, "started": started.isoformat(), "steps": {}}

        if dry_run:
            collected = self.collect()
            summary["stats"] = collected["stats"]
            summary["steps"] = {
                "schema": "skipped (dry-run)",
                "vocabularies": "skipped (dry-run)",
                "writes": "skipped (dry-run)",
            }
            return summary

        # Step 0: schema + (optional) clear
        schema = setup_legal_kg_schema(self._get_driver(), self._database)
        summary["steps"]["schema"] = schema
        if clear:
            from kg.schema import clear_legal_kg

            cleared = clear_legal_kg(self._get_driver(), self._database)
            summary["steps"]["clear"] = {"nodes_deleted": cleared}

        # Step 1: vocabularies
        summary["steps"]["vocabularies"] = self.load_vocabularies()

        # Step 2: assemble
        collected = self.collect()
        summary["stats"] = collected["stats"]

        # Step 3: write (materialise on-demand authorities BEFORE instruments
        # so ISSUED_BY edges never point at missing nodes)
        summary["steps"]["new_authorities"] = self.write_new_authorities()
        summary["steps"]["instruments"] = self.write_instruments(collected["instruments"])
        summary["steps"]["documents"] = self.write_documents(collected["documents"])
        summary["steps"]["provisions"] = self.write_provisions(collected["provisions"])
        summary["steps"]["chunks"] = self.write_chunks(collected["chunks"])
        summary["steps"]["cross_domain"] = self.write_cross_domain(
            collected["instrument_ids"], collected["provision_ids"]
        )
        summary["steps"]["concepts"] = self.write_concept_edges(collected["provision_ids"])

        summary["finished"] = datetime.now(UTC).isoformat()
        summary["elapsed_s"] = round((datetime.now(UTC) - started).total_seconds(), 1)
        return summary


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def json_load(path: Path) -> dict[str, Any]:
    """Load a JSON file (kept as a module function so it is easy to stub)."""
    import json

    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _normalise_name(name: str) -> str:
    text = re.sub(r"^(?:the|an|a)\s+", "", str(name or "").strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _clean_section(value: Any) -> str | None:
    """Normalise a payload/DB section number to a bare number or ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    m = re.match(r"^(\d{1,4}[A-Za-z]?)", text)
    if not m:
        return None
    return m.group(1)


def _valid_section(section: str, known: frozenset[str] | None) -> bool:
    """Section sanity: numeric, non-year-like, in the act's range when known."""
    s = str(section).strip()
    if not re.match(r"^\d{1,4}$", s):
        return False
    n = int(s)
    if n == 0:
        return False
    # Year-like false positives from cross-references ("1938", "1960", "2022")
    if 1900 <= n <= 2100:
        return False
    if known is not None:
        return s in known
    # Unknown act: cap at 1200 (max plausible section number in this corpus)
    return n <= 1200


def _section_title_from_text(text: str) -> str | None:
    match = re.match(r"^\s*(?:Section|Sec\.?|§)\s*\d{1,4}[A-Za-z]?\s*[:.\-—]?\s*(.+)$", text, re.IGNORECASE)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return None


def _collection_for_domain(domain: str) -> str:
    """Map a KG domain back to its Qdrant collection (for chunk provenance)."""
    reverse = {
        "FOOD_SAFETY": "fssai_legal_768",
        "ENVIRONMENT_POLLUTION": "env_legal_768",
        "BUSINESS_CIVIL": "commercial_legal_768",
        "ANIMAL_SLAUGHTER": "animal_legal_768",
        "MUNICIPAL": "wb_state_legal_768",
        "LAND_PREMISES": "wb_state_legal_768",
        "CRIMINAL": "criminal_legal_768",
    }
    return reverse.get(domain, "fssai_legal_768")


# End of corpus_ingestion.py

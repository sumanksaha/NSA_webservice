"""Concept linking for the legal KG (P2 remediation — 2026-08-11).

Links every *isolated* ``LegalConcept`` node (0 inbound edges — the audit's
22/36, re-measured live at 20/36) to the provisions that textually ground it,
via ``APPLIES_TO`` edges carrying an evidence fragment and confidence:

- Matching is deterministic keyword grounding: the concept's name plus a
  curated synonym set, searched word-boundary-safe over ``provision_text``.
- A provision only links when its domain is in the concept's ``domains``
  vocabulary (from the concept node) — a passing mention of "licence" in a
  criminal provision does NOT link the Licence concept.
- Concepts with ZERO textual grounding in their domains are flagged
  ``PREMATURE_TAXONOMY`` (the concept exists but the corpus does not cover it
  yet) instead of being silently left at zero.  Domain-abstraction concepts
  (``BUSINESS_CIVIL`` / ``BusinessCivil`` — duplicates naming the domain
  itself) are classified premature by design: their relationship to
  provisions is the ``BELONGS_TO_DOMAIN`` edge, not textual grounding.

The relationship type, evidence properties and confidence all conform to the
existing graph contract (``kg/validation.py`` allow-list, ``kg/queries.py``
retrieval, ``KGValidator.verify_concept``).  Idempotent (``MERGE``); no APOC;
batched ``UNWIND`` writes — runs on Aura Free.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Per-concept synonym sets used for textual grounding.  Empty tuple = no
#: textual grounding expected (domain abstraction / duplicate) — classified
#: PREMATURE_TAXONOMY by :meth:`ConceptLinker.plan`.
CONCEPT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "AnimalSlaughter": (
        "animal slaughter", "slaughter of animals", "slaughter of cattle",
        "slaughtering", "slaughter house", "slaughterhouse", "slaughter",
    ),
    "AnimalWelfare": (
        "animal welfare", "cruelty to animals", "cruelty against animals",
        "humane treatment of animals",
    ),
    "BUSINESS_CIVIL": (),  # domain-abstraction duplicate of BusinessCivil
    "BusinessCivil": (),  # domain-abstraction duplicate of BUSINESS_CIVIL
    "ConsentToOperate": (
        "consent to establish", "consent to operate", "consent for the establishment",
        "consent for establishment", "consent of the board", "consent of the state board",
        "consent of the central board", "obtain consent", "granted consent",
        "application for consent", "consent for the discharge",
    ),
    "ConsumerProtection": (
        "consumer protection", "protection of the interests of consumers",
        "consumer", "consumers", "goods and services",
    ),
    "Contract": ("contract", "contracts", "breach of contract", "agreement", "promise"),
    "Effluent": ("effluent", "effluents", "trade effluent", "sewage", "waste water", "wastewater"),
    "Hygiene": ("hygiene", "hygienic", "hygienic conditions", "hygienic practices"),
    "ImprovementNotice": ("improvement notice", "improvement notices"),
    "LandPremises": ("tenancy", "tenant", "tenants", "landlord", "eviction", "rent", "premises"),
    "Licence": ("licence", "licences", "license", "licenses", "licensing", "licensed"),
    "Meat": ("meat", "carcass", "carcasses", "flesh", "animal food"),
    "Nuisance": ("nuisance", "nuisances", "public nuisance"),
    "Premises": ("premises", "business premises", "establishment", "place of business", "shop"),
    "Registration": ("registration", "register", "registered", "enrolment", "enrollment", "register of"),
    "Sanitation": ("sanitation", "sanitary", "sanitation and"),
    "SolidWaste": ("solid waste", "solid wastes", "municipal solid waste", "waste disposal", "waste management"),
    "TradeLicence": (
        "trade licence", "trade license", "trading licence", "trading license",
        "licence for a trade", "license for a trade",
    ),
    "Vehicles": ("vehicle", "vehicles", "motor vehicle", "conveyance", "carriage"),
}


def _synonym_pattern(synonym: str) -> re.Pattern[str]:
    """Word-boundary-safe, case-insensitive pattern for a synonym phrase."""
    return re.compile(rf"\b{re.escape(synonym)}\b", re.IGNORECASE)


class ConceptLinker:
    """Ground isolated LegalConcept nodes to provisions via APPLIES_TO edges.

    Args:
        driver: Optional pre-built Neo4j driver (injected for tests).
        database: Neo4j database name (default from ``NEO4J_DATABASE`` env).
        batch_size: UNWIND batch size for edge writes.
    """

    def __init__(self, driver: Any | None = None, database: str | None = None, batch_size: int = 500) -> None:
        self._driver = driver
        self._database = database or os.environ.get("NEO4J_DATABASE", "neo4j")
        self._own_driver = False
        self.batch_size = batch_size

    # ------------------------------------------------------------------ #
    # Driver plumbing
    # ------------------------------------------------------------------ #

    def _get_driver(self) -> Any:
        if self._driver is None:
            from app.services.neo4j_graph import _get_driver

            self._driver = _get_driver()
            self._own_driver = True
        return self._driver

    def _execute(self, cypher: str, params: dict | None = None) -> list[dict]:
        result = self._get_driver().execute_query(cypher, parameters_=params or {}, database_=self._database)
        return [dict(r) for r in result.records]

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def load_concepts(self) -> list[dict[str, Any]]:
        """All LegalConcept nodes: id, name, domains vocabulary, inbound count."""
        rows = self._execute(
            """
            MATCH (c:LegalConcept)
            OPTIONAL MATCH (x)-[e]->(c) WHERE NOT (x:LegalConcept)
            RETURN c.concept_id AS concept_id, coalesce(c.name, '') AS name,
                   coalesce(c.domains, []) AS domains,
                   count(e) AS inbound
            ORDER BY c.concept_id
            """
        )
        out = []
        for r in rows:
            domains = r.get("domains") or []
            out.append(
                {
                    "concept_id": _unwrap(r.get("concept_id")),
                    "name": _unwrap(r.get("name")) or "",
                    "domains": [str(d) for d in domains],
                    "inbound": int(r.get("inbound") or 0),
                }
            )
        return out

    def load_provisions(self) -> list[dict[str, Any]]:
        """All provisions: id, text, domain, own SUPPORTED_BY chunk texts.

        ``own_chunks`` bounds the collect to 30 per provision (deterministic
        order) — the provision's own chunks are its real content, so the
        grounding search includes them alongside ``provision_text``.
        """
        rows = self._execute(
            """
            MATCH (p:LegalProvision)
            OPTIONAL MATCH (p)-[:SUPPORTED_BY]->(c:Chunk)
            WITH p, collect(DISTINCT coalesce(c.chunk_text, ''))[0..30] AS own_chunks
            RETURN p.provision_id AS provision_id,
                   coalesce(p.provision_text, '') AS provision_text,
                   coalesce(p.legal_domain, '') AS legal_domain,
                   own_chunks
            """
        )
        out = []
        for r in rows:
            chunks = r.get("own_chunks") or []
            out.append(
                {
                    "provision_id": _unwrap(r.get("provision_id")),
                    "provision_text": _unwrap(r.get("provision_text")) or "",
                    "legal_domain": _unwrap(r.get("legal_domain")) or "",
                    "own_chunks": [str(x) for x in chunks],
                }
            )
        return out

    # ------------------------------------------------------------------ #
    # Pure grounding
    # ------------------------------------------------------------------ #

    @staticmethod
    def find_grounding(text: str, synonyms: tuple[str, ...], limit: int = 5) -> list[dict[str, Any]]:
        """First *limit* synonym hits in *text* with evidence fragments.

        Returns ``[{synonym, evidence, confidence}]``.  Empty when no synonym
        matches.  ``confidence`` is 0.9 for the first (canonical) synonym
        listed, 0.75 otherwise.
        """
        text = str(text or "")
        if not text or not synonyms:
            return []
        hits: list[dict[str, Any]] = []
        for i, syn in enumerate(synonyms):
            m = _synonym_pattern(syn).search(text)
            if not m:
                continue
            start = max(0, m.start() - 60)
            end = min(len(text), m.end() + 80)
            evidence = re.sub(r"\s+", " ", text[start:end]).strip()
            hits.append(
                {
                    "synonym": syn,
                    "evidence": evidence,
                    "confidence": 0.9 if i == 0 else 0.75,
                }
            )
            if len(hits) >= limit:
                break
        return hits

    # ------------------------------------------------------------------ #
    # Plan / write
    # ------------------------------------------------------------------ #

    def plan_links(self) -> dict[str, Any]:
        """Compute, per isolated concept, the grounding matches (no writes).

        Two-level grounding:

        L1 (provision level, task-specified): search each provision's
        ``provision_text`` plus its own SUPPORTED_BY chunk texts for the
        concept's synonyms.

        L2 (document-chunk fallback, only for concepts with ZERO L1 hits):
        search the documents' ``HAS_CHUNK`` text.  Hits with a
        ``section_number`` resolve to the matching-numbered provision;
        hits without a section number resolve to the document's provisions
        only when the document has <= 3 provisions (whole-instrument orders
        like the WB Meat Order whose operative paragraphs carry no section
        numbers collapse onto their single provision node).  This stops
        corpus-covered concepts (e.g. AnimalSlaughter) from being wrongly
        flagged PREMATURE when the text lives in chunks the provision
        extractor could not attach to a section.

        Concepts still with zero grounding after L1+L2 are flagged
        ``PREMATURE_TAXONOMY`` with an individual justification.
        """
        concepts = self.load_concepts()
        provisions = self.load_provisions()
        by_domain: dict[str, list[dict[str, Any]]] = {}
        for p in provisions:
            by_domain.setdefault(p["legal_domain"], []).append(p)

        plan: dict[str, Any] = {
            "concepts_total": len(concepts),
            "isolated_before": 0,
            "linked": {},       # concept_id -> {provision_count, provisions, evidence_source}
            "premature": {},    # concept_id -> justification
            "rows": [],         # flat edge rows
        }
        for c in concepts:
            cid = c["concept_id"]
            if c["inbound"] > 0:
                continue
            plan["isolated_before"] += 1
            synonyms = CONCEPT_SYNONYMS.get(cid)
            if synonyms is None:
                plan["premature"][cid] = f"no synonym set registered for {cid}"
                continue
            if not synonyms:
                plan["premature"][cid] = "domain-abstraction/duplicate concept — relationship to provisions is BELONGS_TO_DOMAIN, no textual grounding expected"
                continue

            # L1: provision text + own chunks, domain-scoped
            domain_pool: list[dict[str, Any]] = []
            if c["domains"]:
                for d in c["domains"]:
                    domain_pool.extend(by_domain.get(d, []))
            else:
                domain_pool = provisions
            matches: list[dict[str, Any]] = []
            for p in domain_pool:
                text = " ".join([p["provision_text"], *p["own_chunks"]])
                hits = self.find_grounding(text, synonyms)
                if hits:
                    matches.append({"provision_id": p["provision_id"], "hits": hits})
            evidence_source = "provision_text" if matches else None

            # L2: document-chunk fallback when L1 found nothing
            if not matches:
                l2 = self._document_chunk_grounding(c, synonyms)
                if l2["matches"]:
                    matches = l2["matches"]
                    evidence_source = "document_chunk"

            if not matches:
                plan["premature"][cid] = (
                    f"no textual grounding in corpus (synonyms scanned: {', '.join(synonyms[:6])}"
                    + ("…" if len(synonyms) > 6 else "") + ")"
                )
                continue
            plan["linked"][cid] = {
                "concept_name": c["name"],
                "provision_count": len(matches),
                "evidence_source": evidence_source,
                "provisions": [m["provision_id"] for m in matches[:8]],
            }
            for m in matches:
                hit = m["hits"][0]
                plan["rows"].append(
                    {
                        "provision_id": m["provision_id"],
                        "concept_id": cid,
                        "evidence": hit["evidence"],
                        "confidence": hit["confidence"],
                    }
                )
        plan["edges_planned"] = len(plan["rows"])
        return plan

    def _document_chunk_grounding(
        self,
        concept: dict[str, Any],
        synonyms: tuple[str, ...],
    ) -> dict[str, Any]:
        """L2 fallback: ground a concept via its domain documents' chunk text.

        Returns ``{"matches": [{provision_id, hits}], "documents_hit": N}``.
        """
        domains = concept.get("domains") or []
        domain_filter = "AND coalesce(c.legal_domain, '') IN $domains " if domains else ""
        rows = self._execute(
            f"""
            UNWIND $syns AS syn
            MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
            WHERE toLower(coalesce(c.chunk_text, '')) CONTAINS toLower(syn)
            {domain_filter}
            RETURN d.document_id AS document_id,
                   coalesce(c.section_number, '') AS section_number,
                   substring(coalesce(c.chunk_text, ''), 0, 500) AS fragment
            """,
            {"syns": list(synonyms), "domains": domains},
        )
        if not rows:
            return {"matches": [], "documents_hit": 0}

        # Group hits per document, with section-resolvable hits first
        by_doc: dict[str, dict[str, Any]] = {}
        for r in rows:
            doc = str(_unwrap(r.get("document_id")) or "")
            sec = str(_unwrap(r.get("section_number")) or "")
            frag = str(_unwrap(r.get("fragment")) or "")
            entry = by_doc.setdefault(doc, {"section_hits": {}, "no_section_fragments": [], "any_fragment": ""})
            if sec:
                entry["section_hits"].setdefault(sec, frag)
            else:
                entry["no_section_fragments"].append(frag)
            if not entry["any_fragment"]:
                entry["any_fragment"] = frag

        # Provision lookup per document via the SOURCE_OF edge (exact)
        prov_rows = self._execute(
            """
            MATCH (p:LegalProvision)-[:SOURCE_OF]->(d:Document)
            WHERE d.document_id IN $docs
            RETURN d.document_id AS document_id,
                   p.provision_id AS provision_id,
                   coalesce(p.provision_number, '') AS provision_number
            """,
            {"docs": list(by_doc.keys())},
        )
        provs_by_doc: dict[str, list[dict[str, Any]]] = {}
        for r in prov_rows:
            doc = str(_unwrap(r.get("document_id")) or "")
            provs_by_doc.setdefault(doc, []).append(
                {"provision_id": _unwrap(r.get("provision_id")), "provision_number": _unwrap(r.get("provision_number")) or ""}
            )

        matches: list[dict[str, Any]] = []
        for doc, entry in by_doc.items():
            doc_provisions = provs_by_doc.get(doc, [])
            if not doc_provisions:
                continue
            provision_by_number = {p["provision_number"]: p for p in doc_provisions}
            # Section-resolvable hits -> matching-numbered provision
            for sec, frag in entry["section_hits"].items():
                p = provision_by_number.get(sec)
                if p:
                    matches.append(
                        {
                            "provision_id": p["provision_id"],
                            "hits": [{"synonym": sec, "evidence": frag, "confidence": 0.75}],
                        }
                    )
            # No-section hits -> whole-instrument orders (<= 3 provisions)
            if entry["no_section_fragments"] and len(doc_provisions) <= 3:
                frag = entry["no_section_fragments"][0]
                for p in doc_provisions:
                    if p["provision_id"] in {m["provision_id"] for m in matches}:
                        continue
                    matches.append(
                        {
                            "provision_id": p["provision_id"],
                            "hits": [{"synonym": "document chunk", "evidence": frag, "confidence": 0.7}],
                        }
                    )
        return {"matches": matches, "documents_hit": len(by_doc)}

    def write_edges(self, rows: list[dict[str, Any]]) -> int:
        """MERGE ``(p)-[r:APPLIES_TO]->(c)`` with evidence, batched UNWIND."""
        if not rows:
            return 0
        written = 0
        for i in range(0, len(rows), self.batch_size):
            batch = rows[i : i + self.batch_size]
            self._execute(
                """
                UNWIND $rows AS r
                MATCH (p:LegalProvision {provision_id: r.provision_id})
                MATCH (c:LegalConcept {concept_id: r.concept_id})
                MERGE (p)-[rel:APPLIES_TO]->(c)
                ON CREATE SET rel.evidence = r.evidence,
                    rel.confidence = r.confidence,
                    rel.evidence_type = 'corpus_concept_link'
                ON MATCH SET rel.evidence = r.evidence,
                    rel.confidence = r.confidence
                """,
                {"rows": batch},
            )
            written += len(batch)
        return written

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #

    def link(self, dry_run: bool = False) -> dict[str, Any]:
        """Plan and (optionally) write the concept-link edges.

        Returns a summary with before/after isolated counts, per-concept
        linked/premature breakdown and verification numbers.  ``dry_run``
        performs NO writes.
        """
        started = datetime.now(UTC)
        plan = self.plan_links()
        summary: dict[str, Any] = {
            "dry_run": dry_run,
            "concepts_total": plan["concepts_total"],
            "isolated_before": plan["isolated_before"],
            "concepts_linked": len(plan["linked"]),
            "concepts_premature": len(plan["premature"]),
            "edges_planned": plan["edges_planned"],
            "premature": plan["premature"],
            "linked_detail": plan["linked"],
        }
        if dry_run:
            summary["edges_written"] = 0
        else:
            summary["edges_written"] = self.write_edges(plan["rows"])
        summary["isolated_after"] = self._count_isolated()
        summary["elapsed_s"] = round((datetime.now(UTC) - started).total_seconds(), 1)
        return summary

    def _count_isolated(self) -> int:
        rows = self._execute(
            """
            MATCH (c:LegalConcept)
            OPTIONAL MATCH (x)-[e]->(c) WHERE NOT (x:LegalConcept)
            WITH c, count(e) AS inbound
            WHERE inbound = 0
            RETURN count(c) AS n
            """
        )
        return int(rows[0]["n"]) if rows else 0


def _unwrap(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "to_native"):
        return value.to_native()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return value


# End of concept_linking.py

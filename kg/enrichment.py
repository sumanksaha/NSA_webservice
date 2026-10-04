"""Corpus-scale semantic enrichment for the legal Knowledge Graph (2026-08-11).

Auto-tags every provision with typed legal semantics using deterministic,
rule-based pattern matching (no LLM, no external calls):

- ``IMPOSES_DUTY``        -> Obligation / Duty concepts   (\"shall\", \"must\", \"duty to\")
- ``PROHIBITS``           -> Prohibition concept         (\"shall not\", \"no person shall\", \"prohibited\")
- ``CREATES_OFFENCE``     -> Offence concept             (\"offence\", \"punishable\", \"shall be guilty\")
- ``PRESCRIBES_PENALTY``  -> Penalty concept             (\"imprisonment\", \"fine\", \"penalty of\")
- ``GRANTS_POWER_TO``     -> Power concept               (\"may\", \"power to\", \"empowered\")
- ``GRANTS_PERMISSION``   -> Permission concept          (\"permit\", \"licence\", \"may, subject to\" )
- ``PRESCRIBES``          -> Procedure concept           (\"procedure\", \"in the prescribed manner\")

Design rules:

1. **Evidence-backed** — every edge carries the matched sentence fragment as
   ``evidence`` and a rule-specific ``confidence``, mirroring the audit's
   D3 requirement (no bare relationship types).
2. **Deterministic** — pure regex + priority ordering; re-runs are idempotent
   (``MERGE``).  No LLM cost, no randomness.
3. **Precedence-aware** — \"shall not\" must win over \"shall\" (PROHIBITS beats
   IMPOSES_DUTY); \"punishable with imprisonment\" wins over bare \"offence\".
4. **Targets the typed concept vocabulary** — edges land on the controlled
   ``LegalConcept`` nodes.  The enricher is standalone, so it calls
   :meth:`LegalSemanticEnricher.ensure_concepts` to MERGE any vocabulary node
   it is about to write to.  Without it, ``write_edges``' ``MATCH`` on a
   concept silently matches nothing and the whole rule group vanishes: the
   2026-08-11 run reported ``edges_written: 2526`` while 297 of those
   (DEFINES/EXEMPTS/DECLARES) were dropped on the floor.
5. **Honest counters** — ``edges_written`` counts rows Cypher actually
   materialised (``RETURN count(*)`` after the ``MERGE``), not rows handed to
   the driver.  A rule group that cannot land must show up as a shortfall,
   never as a success.
6. **Graceful** — provisions with < 40 chars of text (OCR-limited) are skipped
   (no junk edges from noise); Neo4j absence degrades to an empty report.

The enricher reads provisions + writes edges through the same batched
``UNWIND`` MERGE pattern as :class:`kg.corpus_ingestion.KGCorpusIngestionEngine`
so it runs in minutes on the full 1,861-provision corpus.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from kg.domain_manifest import CONCEPTS

logger = logging.getLogger(__name__)

#: Minimum provision text length to tag (OCR noise guard).
MIN_TEXT_CHARS = 40

#: Pattern of the generic "shall" duty rule — identified here so the enricher
#: can skip a "shall" that is already part of a prohibition phrase.
_GENERIC_SHALL_PATTERN = r"\bshall\b"

#: Pattern of the penalty-schedule rupee rule — identified here so the
#: enricher can skip rupee mentions that are really a *fee* (see
#: :func:`_rupees_match_not_fee`), not a penalty.
_RUPEES_RULE_PATTERN = r"\b(?:rupees|Rs\.?|/-)(?![A-Za-z])"

#: Fee/charge/tax context words.  When one appears within :data:`_FEE_WINDOW`
#: characters of a rupee mention, the mention is a fee (registration fee,
#: processing charge, tax, rent) — NOT a penalty — and the penalty-schedule
#: rule must not fire.  Confirmed false positive (2026-08-11):
#: INDIAN_PARTNERSHIP_ACT_1932_SEC_14 was tagged PRESCRIBES_PENALTY on the
#: evidence "levy a non-refundable processing fee of Rs. 1,000/-".
#:
#: Only unambiguous monetary markers are listed — ``deposit`` is deliberately
#: absent ("or deposit of any matter" in the KMC fine rows is a verb, not a
#: fee, and must not suppress a genuine penalty cell).
#: ``charges?`` is tightened to ``charges?\s+(?:for|payable|levied|collected)``
#: so "in charge of" (responsibility) does not read as a fee — ``of`` is
#: deliberately excluded ("person in charge of the premises..." would
#: otherwise suppress a genuine fine cell).
_FEE_CONTEXT_RE = re.compile(
    r"\b(?:fees?|charges?\s+(?:for|payable|levied|collected)|tax(?:es)?|rent(?:al)?|refund(?:able)?|remuneration|honorarium|non-refundable|compounding)\b",
    re.IGNORECASE,
)

#: Window (chars) either side of a rupee mention checked for fee context.
_FEE_WINDOW = 60

#: Rule list — ordered by precedence (first match wins per category set).
#: Each rule: (rel_type, concept_id, confidence, regex).
#: ``concept_id`` targets a LegalConcept node in the controlled vocabulary.
#:
#: P1-remediation relaxation (2026-08-11): the penalty/offence patterns are
#: glue-tolerant — ``punishable\s*with`` (no leading \b) still matches the
#: BNS extraction artifact ``shallbepunishedwithimprisonment...`` (words
#: concatenated without spaces), and ``\s*`` between words covers both
#: normal and glued forms.  The ``rupees|Rs.|/-`` rule catches penalty-
#: schedule rows (KMC/WB amendment fine tables: ``Section 498 ... 500/-``);
#: ``prohibition of/against/on`` catches the noun form the old ``prohibited``
#: rule missed.
SEMANTIC_RULES: list[tuple[str, str, float, re.Pattern[str]]] = [
    # --- Prohibitions (highest precedence: \"shall not\" must beat \"shall\") ---
    ("PROHIBITS", "Prohibition", 0.9, re.compile(r"\bshall\s+not\b", re.IGNORECASE)),
    ("PROHIBITS", "Prohibition", 0.85, re.compile(r"\bno\s+person\s+shall\b", re.IGNORECASE)),
    ("PROHIBITS", "Prohibition", 0.85, re.compile(r"\bprohibited\b", re.IGNORECASE)),
    ("PROHIBITS", "Prohibition", 0.8, re.compile(r"\bit\s+shall\s+be\s+unlawful\b", re.IGNORECASE)),
    ("PROHIBITS", "Prohibition", 0.8, re.compile(r"\bmust\s+not\b", re.IGNORECASE)),
    ("PROHIBITS", "Prohibition", 0.72, re.compile(r"\bprohibition\s+(?:against|of|on)\b", re.IGNORECASE)),
    # --- Offences + penalties (before generic duty so \"shall be punishable\" wins) ---
    (
        "PRESCRIBES_PENALTY",
        "Penalty",
        0.95,
        re.compile(r"(?:punishable|punished)\s*with\s*(?:imprisonment|fine|both)", re.IGNORECASE),
    ),
    (
        "PRESCRIBES_PENALTY",
        "Penalty",
        0.9,
        re.compile(r"imprisonment\s*(?:for|of|which\s*may\s*extend)", re.IGNORECASE),
    ),
    (
        "PRESCRIBES_PENALTY",
        "Penalty",
        0.85,
        re.compile(r"fine\s*(?:which\s*may\s*extend|not\s*exceeding)", re.IGNORECASE),
    ),
    ("PRESCRIBES_PENALTY", "Penalty", 0.85, re.compile(r"penalty\s*(?:of|for)", re.IGNORECASE)),
    ("PRESCRIBES_PENALTY", "Penalty", 0.72, re.compile(_RUPEES_RULE_PATTERN, re.IGNORECASE)),
    ("CREATES_OFFENCE", "Offence", 0.9, re.compile(r"shall\s*be\s*guilty\s*of\s*an\s*offence", re.IGNORECASE)),
    ("CREATES_OFFENCE", "Offence", 0.85, re.compile(r"\bcommits?\s+an\s+offence\b", re.IGNORECASE)),
    ("CREATES_OFFENCE", "Offence", 0.8, re.compile(r"(?:constitutes?|amounts?\s*to)\s*an\s*offence", re.IGNORECASE)),
    ("CREATES_OFFENCE", "Offence", 0.75, re.compile(r"\boffence\s*(?:punishable|committed)", re.IGNORECASE)),
    ("CREATES_OFFENCE", "Offence", 0.7, re.compile(r"\boffence\b", re.IGNORECASE)),
    # --- Powers (\"may\" grants authority) ---
    ("GRANTS_POWER_TO", "Power", 0.8, re.compile(r"\bpower\s+to\b", re.IGNORECASE)),
    ("GRANTS_POWER_TO", "Power", 0.8, re.compile(r"\bempowered\b", re.IGNORECASE)),
    ("GRANTS_POWER_TO", "Power", 0.8, re.compile(r"\bauthori[sz]ed\s+to\b", re.IGNORECASE)),
    ("GRANTS_POWER_TO", "Power", 0.6, re.compile(r"\bmay\b", re.IGNORECASE)),
    # --- Permissions ---
    (
        "GRANTS_PERMISSION",
        "Permission",
        0.8,
        re.compile(r"\bmay\s+be\s+(?:granted|permitted|allowed)\b", re.IGNORECASE),
    ),
    ("GRANTS_PERMISSION", "Permission", 0.75, re.compile(r"\bpermission\s+(?:may|shall)\s+be\b", re.IGNORECASE)),
    ("GRANTS_PERMISSION", "Permission", 0.7, re.compile(r"\blicen[cs]e\b", re.IGNORECASE)),
    # --- Duties / obligations ---
    # The generic "shall" rule is the weakest duty signal (bare "shall" is
    # common boilerplate) — confidence 0.7 flags it honestly; "duty to" /
    # "obliged to" carry the higher-confidence duty semantics.
    ("IMPOSES_DUTY", "Obligation", 0.7, re.compile(_GENERIC_SHALL_PATTERN, re.IGNORECASE)),
    ("IMPOSES_DUTY", "Obligation", 0.8, re.compile(r"\bduty\s+to\b", re.IGNORECASE)),
    ("IMPOSES_DUTY", "Duty", 0.8, re.compile(r"\bit\s+shall\s+be\s+the\s+duty\b", re.IGNORECASE)),
    ("IMPOSES_DUTY", "Obligation", 0.7, re.compile(r"\bobliged\s+to\b", re.IGNORECASE)),
    # --- Procedures ---
    ("PRESCRIBES", "Procedure", 0.8, re.compile(r"\bin\s+the\s+prescribed\s+manner\b", re.IGNORECASE)),
    ("PRESCRIBES", "Procedure", 0.75, re.compile(r"\bprocedure\b", re.IGNORECASE)),
    ("PRESCRIBES", "Procedure", 0.7, re.compile(r"\bshall\s+be\s+in\s+writing\b", re.IGNORECASE)),
    # --- Definitions (added 2026-10-03) ---
    # A definitional provision is substantive law but has no duty/penalty
    # shape, so before DEFINES existed it produced no edge and was reported
    # `unclassified` (~796 provisions, mostly Sale of Goods / BNS s.2 style
    # "X is said to be ..." clauses). These rules must stay ABOVE the generic
    # "shall" duty rule: a definition often contains "shall" incidentally.
    ("DEFINES", "Definition", 0.85, re.compile(r"\bis\s+said\s+to\s+be\b", re.IGNORECASE)),
    ("DEFINES", "Definition", 0.85, re.compile(r"\bmeans\b(?!\s+to\s+mean)", re.IGNORECASE)),
    (
        "DEFINES",
        "Definition",
        0.8,
        re.compile(r"\b(?:shall|may)\s+(?:be\s+called|mean|be\s+deemed\s+to\s+mean)\b", re.IGNORECASE),
    ),
    ("DEFINES", "Definition", 0.8, re.compile(r"\b(?:refers\s+to|includes)\b", re.IGNORECASE)),
    (
        "DEFINES",
        "Definition",
        0.75,
        re.compile(r"\bin\s+this\s+Act,?\s+unless\s+the\s+context\s+otherwise\b", re.IGNORECASE),
    ),
    # --- Exemptions (added 2026-10-03) ---
    ("EXEMPTS", "Exemption", 0.85, re.compile(r"\bshall\s+not\s+apply\b", re.IGNORECASE)),
    ("EXEMPTS", "Exemption", 0.8, re.compile(r"\bshall\s+be\s+exempt\b", re.IGNORECASE)),
    ("EXEMPTS", "Exemption", 0.72, re.compile(r"\bsave\s+as\s+provided\b", re.IGNORECASE)),
    ("EXEMPTS", "Exemption", 0.7, re.compile(r"\bnotwithstanding\b", re.IGNORECASE)),
    # --- Declarative / deeming rules (added 2026-10-03) ---
    # Statements of what the law IS, with no deontic force: the Sale of Goods
    # Act conditions/warranty rules ("an agreement to sell becomes a sale
    # when..."), deeming rules, and status-conferring provisions. Without
    # these they matched no category and piled up as `unclassified`.
    (
        "DECLARES",
        "DeclarativeRule",
        0.8,
        re.compile(r"\b(?:shall|is|are)\s+(?:be\s+)?deemed\s+(?:to\s+be|an?|that)\b", re.IGNORECASE),
    ),
    (
        "DECLARES",
        "DeclarativeRule",
        0.78,
        re.compile(r"\bbecomes?\s+(?:a|an|the)\b.{0,60}\bwhen\b", re.IGNORECASE | re.DOTALL),
    ),
    ("DECLARES", "DeclarativeRule", 0.78, re.compile(r"\boperates\s+as\s+(?:a|an|the)\b", re.IGNORECASE)),
    (
        "DECLARES",
        "DeclarativeRule",
        0.75,
        re.compile(r"\b(?:is|are)\s+(?:a|an)\s+contract\s+of\s+(?:sale|lease|work)\b", re.IGNORECASE),
    ),
    # Construction / qualification clauses (added 2026-10-03).  These state
    # how a rule is to be read or what it is subject to — substantive law with
    # no deontic force, so they belong to DECLARES rather than to a duty,
    # permission or prohibition category.
    ("DECLARES", "DeclarativeRule", 0.8, re.compile(r"\bshall\s+be\s+(?:construed|interpreted)\b", re.IGNORECASE)),
    (
        "DECLARES",
        "DeclarativeRule",
        0.75,
        re.compile(r"\bin\s+the\s+absence\s+of\s+(?:a\s+)?(?:contract|agreement)\b", re.IGNORECASE),
    ),
    (
        "DECLARES",
        "DeclarativeRule",
        0.72,
        re.compile(r"\b(?:shall|is)\s+be\s+subject\s+to\s+the\s+(?:provisions|conditions)\b", re.IGNORECASE),
    ),
    (
        "DECLARES",
        "DeclarativeRule",
        0.7,
        re.compile(r"\bfor\s+the\s+purposes\s+of\s+this\s+(?:Act|section)\b", re.IGNORECASE),
    ),
    (
        "DECLARES",
        "DeclarativeRule",
        0.75,
        re.compile(r"\b(?:rules?|provisions?)\s+(?:contained\s+in\s+)?sections?\b.{0,40}\bare\b", re.IGNORECASE),
    ),
    (
        "DECLARES",
        "DeclarativeRule",
        0.72,
        re.compile(r"\bdepends\s+(?:in\s+each\s+case\s+)?on\s+the\s+construction\b", re.IGNORECASE),
    ),
]

#: Minimum confidence for an edge to be written (keeps weak \"may\" tagging from
#: drowning the graph with noise).
MIN_CONFIDENCE = 0.7

#: Deterministic NOT_APPLICABLE classifiers (reason -> pattern).  Used to tag
#: provisions that produced no semantic edge because they are genuinely
#: non-substantive (definitions, repeal/amendment machinery, gazette
#: boilerplate, cross-reference fragments) — P1 remediation deliverable 1e:
#: NOT_APPLICABLE provisions are explicitly tagged, not left silently
#: unclassified.  Order matters (first reason wins).  Best-effort: a
#: provision matching none of these AND producing no edge is marked
#: ``unclassified`` and reported individually.
NOT_APPLICABLE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Definitions that did NOT match a DEFINES rule (e.g. a bare "Definitions"
    # heading) are genuinely non-substantive; broadened 2026-10-03 to cover
    # the constructions the DEFINES rules do not claim.
    (
        "definition",
        re.compile(r"\b(?:means|defined|definition|is said to be|shall be called)\b", re.IGNORECASE),
    ),
    (
        "amendment_machinery",
        re.compile(
            r"\b(?:omitted,?\s+by|shall be omitted|repealed,?\s+by|rep\.?\s+by|substituted,?\s+by|"
            r"renumbered|inserted,?\s+by|as amended by)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "gazette_machinery",
        re.compile(
            r"\b(?:the gazette of india|gazette of india|published by authority|notification no\.?|"
            r"dated the \d{1,2}(?:st|nd|rd|th)?\s+[a-z]+,?\s+\d{4})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cross_reference_fragment",
        re.compile(
            r"^(?:of|in|under|for|to|by|with|on|from|as|and|or|that|which|whose)\s|^\([A-Za-z]\)\s", re.IGNORECASE
        ),
    ),
    # Financial-statement format rows (Companies Act Schedule III style:
    # "(Rupees in ......) Particulars Note No. Figures...") — a form/table
    # layout, not a penalty or a duty.  Found via the SEC_349 false positive.
    # re.DOTALL so a line break between "Rupees in" and "Particulars" still
    # matches (OCR/layout tables often wrap).
    (
        "financial_format_row",
        re.compile(r"\brupees?\s+in\b.{0,80}?particulars", re.IGNORECASE | re.DOTALL),
    ),
    # --- Structural fragments that are not provisions at all (2026-10-03) ---
    # Measured over the residual 588 `unclassified` provisions, the largest
    # single group is front/back-matter, not law: the arrangement-of-sections
    # list ("1 21. Specific goods ... 22. Specific goods ..."), the
    # Contract Act's explanatory Illustrations, and bare chapter headings.
    # These carry no operative text and must not be presented as provisions.
    (
        "arrangement_of_sections",
        re.compile(r"\d{1,3}\.\s+[A-Z][^.\n]{3,70}\.\s+\d{1,3}\.\s+[A-Z]"),
    ),
    # Must be the Contract Act's *heading* form ("Illustrations" alone on a
    # line), not any sentence that happens to open with the word — otherwise
    # "Illustration (a) ... shall not ..." would be filed as front-matter and
    # lose its operative rule.
    ("illustration", re.compile(r"^\s*illustrations?\s*[\r\n]", re.IGNORECASE)),
    ("chapter_heading", re.compile(r"^[\s(\[]*chapter\s+[IVXL]+\b", re.IGNORECASE)),
    # A run of bare section/subsection numbers with no operative prose
    # ("section 70, section 71, section 74 ...", "251, 259, 260, 261,").
    # Whitespace is optional throughout because the BNS PDFs extract with
    # inter-word spaces stripped ("sections9,49,50,52,54,...").  The literal
    # word "section"/"clause" is required, so a numeric money or quantity
    # sentence is never swallowed.
    (
        "citation_list",
        re.compile(
            r"\b(?:sub-?sections?|sections?|clauses?)\s*\d{1,4}(?:\s*\([a-z0-9]+\))*\s*[,;]"
            r"(?:\s*(?:and\s*)?(?:sub-?sections?|sections?|clauses?)?\s*\d{1,4}\s*[,;]){3,}",
            re.IGNORECASE,
        ),
    ),
]


class LegalSemanticEnricher:
    """Tag provisions with typed legal semantics (duty/offence/penalty/...).

    Args:
        driver: Optional pre-built Neo4j driver (injected for tests).
        database: Neo4j database name (default from ``NEO4J_DATABASE`` env).
        min_confidence: Minimum rule confidence to write an edge.
        batch_size: UNWIND batch size for edge writes.
    """

    def __init__(
        self,
        driver: Any | None = None,
        database: str | None = None,
        min_confidence: float = MIN_CONFIDENCE,
        batch_size: int = 500,
    ) -> None:
        self._driver = driver
        self._database = database or os.environ.get("NEO4J_DATABASE", "neo4j")
        self._own_driver = False
        self.min_confidence = min_confidence
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
    # Tagging (pure, deterministic)
    # ------------------------------------------------------------------ #

    @staticmethod
    def tag_text(text: str, min_confidence: float = MIN_CONFIDENCE) -> list[dict[str, Any]]:
        """Tag *text* with semantic edges.

        Returns ``[{rel_type, concept_id, evidence, confidence}]``.  Precedence
        is enforced by rule order: the FIRST matching rule per category wins
        (e.g. ``PROHIBITS`` before ``IMPOSES_DUTY``, ``PRESCRIBES_PENALTY``
        before ``CREATES_OFFENCE``).  Deduplicates identical rel+concept pairs
        keeping the strongest match.  ``min_confidence`` gates which rules run
        (defaults to the module threshold).

        Precedence is token-scoped, not just rule-ordered: a generic "shall"
        that is already part of a prohibition phrase ("no person shall",
        "shall not", "it shall be unlawful") does NOT also produce an
        ``IMPOSES_DUTY`` edge — the first unconsumed "shall" is used, so a
        genuine duty elsewhere in the provision still tags.
        """
        text = str(text or "")
        if len(text.strip()) < MIN_TEXT_CHARS:
            return []
        tags: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        #: Spans of prohibition matches — the generic "shall" duty rule must
        #: not re-tag a "shall" already inside one of these phrases.
        prohibited_spans: list[tuple[int, int]] = []
        for rel_type, concept_id, confidence, pattern in SEMANTIC_RULES:
            if confidence < min_confidence:
                continue
            if rel_type == "IMPOSES_DUTY" and pattern.pattern == _GENERIC_SHALL_PATTERN:
                m = _first_match_outside_spans(pattern, text, prohibited_spans)
            elif rel_type == "PRESCRIBES_PENALTY" and pattern.pattern == _RUPEES_RULE_PATTERN:
                m = _rupees_match_not_fee(text, pattern)
            else:
                m = pattern.search(text)
            if not m:
                continue
            key = (rel_type, concept_id)
            if key in seen:
                continue  # first (highest-precedence) match wins
            seen.add(key)
            if rel_type == "PROHIBITS":
                prohibited_spans.append((m.start(), m.end()))
            # Evidence = the sentence fragment around the match (~140 chars)
            start = max(0, m.start() - 60)
            end = min(len(text), m.end() + 80)
            evidence = re.sub(r"\s+", " ", text[start:end]).strip()
            tags.append({
                "rel_type": rel_type,
                "concept_id": concept_id,
                "evidence": evidence,
                "confidence": confidence,
            })
        return tags

    # ------------------------------------------------------------------ #
    # Neo4j read/write
    # ------------------------------------------------------------------ #

    def load_provisions(self, limit: int | None = None, domain: str | None = None) -> list[dict[str, Any]]:
        """Read provisions (id, number, title, text, domain) from the graph."""
        limit_clause = f" LIMIT {int(limit)}" if limit else ""
        filter_clause = ""
        params: dict[str, Any] = {}
        if domain:
            # Fold the filter into a WHERE clause (no duplicate MATCH on d).
            # The coalesce keeps provisions whose domain lives only in the
            # node property (p.legal_domain) rather than a BELONGS_TO_DOMAIN
            # edge.
            filter_clause = "WHERE coalesce(d.domain_name, p.legal_domain, '') = $domain "
            params["domain"] = domain
        rows = self._execute(
            f"""
            MATCH (p:LegalProvision)
            OPTIONAL MATCH (i)-[:CONTAINS]->(p)
            OPTIONAL MATCH (p)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain)
            {filter_clause}
            RETURN p.provision_id AS provision_id,
                   p.provision_number AS provision_number,
                   p.title AS title,
                   coalesce(p.provision_text, '') AS provision_text,
                   coalesce(d.domain_name, p.legal_domain, '') AS legal_domain,
                   coalesce(i.instrument_id, '') AS instrument_id
            ORDER BY p.provision_id
            {limit_clause}
            """,
            params,
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append({
                "provision_id": _unwrap(r.get("provision_id")),
                "provision_number": _unwrap(r.get("provision_number")),
                "title": _unwrap(r.get("title")) or "",
                "provision_text": _unwrap(r.get("provision_text")) or "",
                "legal_domain": _unwrap(r.get("legal_domain")) or "",
                "instrument_id": _unwrap(r.get("instrument_id")) or "",
            })
        return out

    def ensure_concepts(self, concept_ids: Iterable[str] | None = None) -> int:
        """MERGE the ``LegalConcept`` nodes this enricher writes edges to.

        ``kg.ingestion.load_vocabularies`` is the only other writer of the
        controlled vocabulary and it runs inside ``run_rebuild`` — so running
        this CLI against a graph whose vocabulary is stale (or never loaded)
        makes every edge to an unknown concept a no-op.  MERGE on the
        ``concept_id`` uniqueness constraint keeps this idempotent; an
        existing node is only refreshed with its manifest metadata.
        """
        wanted = set(concept_ids or (r[1] for r in SEMANTIC_RULES))
        written = 0
        for concept in CONCEPTS.values():
            if concept.concept_id not in wanted:
                continue
            self._execute(
                """
                MERGE (c:LegalConcept {concept_id: $cid})
                ON MATCH SET c.name = $name, c.description = $desc,
                             c.domains = $domains
                """,
                {
                    "cid": concept.concept_id,
                    "name": concept.name,
                    "desc": concept.description,
                    "domains": list(concept.domains),
                },
            )
            written += 1
        return written

    def write_edges(self, rows: list[dict[str, Any]]) -> int:
        """MERGE semantic edges ``(p)-[r:REL]->(:LegalConcept)`` with evidence.

        Rows are grouped by ``rel_type`` (a fixed, controlled set from
        :data:`SEMANTIC_RULES`) and written with a static f-string Cypher per
        type — no APOC needed, works on Aura Free.

        Returns the number of rows Cypher actually materialised.  That is
        deliberately ``< len(rows)`` whenever a ``MATCH`` finds no target: an
        unmatched row means the concept vocabulary is stale, and reporting the
        shortfall is the whole point (see the module docstring, rule 5).
        """
        if not rows:
            return 0
        by_type: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_type.setdefault(row["rel_type"], []).append(row)
        written = 0
        for rel_type, batch_rows in by_type.items():
            for i in range(0, len(batch_rows), self.batch_size):
                batch = batch_rows[i : i + self.batch_size]
                res = self._execute(
                    f"""
                    UNWIND $rows AS r
                    MATCH (p:LegalProvision {{provision_id: r.provision_id}})
                    MATCH (c:LegalConcept {{concept_id: r.concept_id}})
                    MERGE (p)-[rel:{rel_type}]->(c)
                    ON CREATE SET rel.evidence = r.evidence,
                        rel.confidence = r.confidence,
                        rel.evidence_type = 'corpus_semantic'
                    ON MATCH SET rel.evidence = r.evidence,
                        rel.confidence = r.confidence
                    RETURN count(*) AS written
                    """,
                    {"rows": batch},
                )
                written += _count_of(res)
        if written < len(rows):
            logger.warning(
                "enrichment: %d/%d rows did not materialise — a target "
                "provision or concept node is missing from the graph",
                len(rows) - written,
                len(rows),
            )
        return written

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #

    def enrich(
        self,
        limit: int | None = None,
        domain: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Tag provisions and write edges (or report what would be written).

        Every provision also gets an explicit ``semantic_class`` (P1
        remediation deliverable 1e): ``tagged`` when at least one semantic
        edge fires, ``not_applicable:<reason>`` for genuinely non-substantive
        provisions (definitions / repeal machinery / gazette boilerplate /
        cross-reference fragments — see :data:`NOT_APPLICABLE_PATTERNS`),
        ``skipped_short_text`` under the OCR guard, else ``unclassified``.
        Nothing is left silently unclassified.

        Returns a summary dict with per-rel-type edge counts, class
        breakdown, skip reasons, and timings.  ``dry_run`` performs NO
        writes.
        """
        started = datetime.now(UTC)
        provisions = self.load_provisions(limit=limit, domain=domain)
        summary: dict[str, Any] = {
            "dry_run": dry_run,
            "provisions_loaded": len(provisions),
            "skipped_short_text": 0,
            "not_applicable": 0,
            "unclassified": 0,
            "rel_type_totals": {},
            "class_breakdown": {},
        }

        rows: list[dict[str, Any]] = []
        class_rows: list[dict[str, Any]] = []
        for p in provisions:
            pid = p["provision_id"]
            text_len = len(p["provision_text"].strip())
            if text_len < MIN_TEXT_CHARS:
                summary["skipped_short_text"] += 1
                class_rows.append({"provision_id": pid, "semantic_class": "skipped_short_text"})
                continue
            tags = self.tag_text(p["provision_text"], min_confidence=self.min_confidence)
            if tags:
                class_rows.append({"provision_id": pid, "semantic_class": "tagged"})
            else:
                reason = _not_applicable_reason(p["provision_text"])
                if reason:
                    summary["not_applicable"] += 1
                    class_rows.append({"provision_id": pid, "semantic_class": f"not_applicable:{reason}"})
                else:
                    summary["unclassified"] += 1
                    class_rows.append({"provision_id": pid, "semantic_class": "unclassified"})
            for tag in tags:
                row = {
                    "provision_id": pid,
                    "rel_type": tag["rel_type"],
                    "concept_id": tag["concept_id"],
                    "evidence": tag["evidence"],
                    "confidence": tag["confidence"],
                }
                rows.append(row)
                summary["rel_type_totals"][tag["rel_type"]] = summary["rel_type_totals"].get(tag["rel_type"], 0) + 1
        summary["edges_planned"] = len(rows)
        for cr in class_rows:
            summary["class_breakdown"][cr["semantic_class"]] = (
                summary["class_breakdown"].get(cr["semantic_class"], 0) + 1
            )

        if not dry_run:
            # Standalone guarantee: the concept nodes these rows point at must
            # exist before any edge can materialise (see module docstring).
            summary["concepts_ensured"] = self.ensure_concepts()
            summary["edges_written"] = self.write_edges(rows)
            summary["classes_written"] = self._write_semantic_classes(class_rows)
        else:
            summary["concepts_ensured"] = 0
            summary["edges_written"] = 0
            summary["classes_written"] = 0
        summary["edges_dropped"] = len(rows) - summary["edges_written"]

        summary["elapsed_s"] = round((datetime.now(UTC) - started).total_seconds(), 1)
        return summary

    def _write_semantic_classes(self, rows: list[dict[str, Any]]) -> int:
        """MERGE ``p.semantic_class`` for every provision (batched UNWIND)."""
        if not rows:
            return 0
        written = 0
        for i in range(0, len(rows), self.batch_size):
            batch = rows[i : i + self.batch_size]
            self._execute(
                """
                UNWIND $rows AS r
                MATCH (p:LegalProvision {provision_id: r.provision_id})
                SET p.semantic_class = r.semantic_class
                """,
                {"rows": batch},
            )
            written += len(batch)
        return written


def _not_applicable_reason(text: str) -> str | None:
    """Best-effort reason when *text* is genuinely non-substantive, else None."""
    t = str(text or "")
    if not t.strip():
        return None
    for reason, pattern in NOT_APPLICABLE_PATTERNS:
        if pattern.search(t):
            return reason
    return None


def _unwrap(value: Any) -> Any:
    """Coerce Neo4j driver values (Node/Date) to plain Python primitives."""
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


def _count_of(rows: Any) -> int:
    """Total a ``RETURN count(...) AS written`` result (driver records or rows)."""
    if rows is None:
        return 0
    if isinstance(rows, int):
        return rows
    total = 0
    for row in rows:
        value = row.get("written") if hasattr(row, "get") else getattr(row, "written", None)
        total += int(value or 0)
    return total


def _rupees_match_not_fee(text: str, pattern: re.Pattern[str] | None = None) -> re.Match[str] | None:
    """First rupee/``/-`` mention that is NOT inside a fee/charge/tax context.

    The penalty-schedule rule fires on rupee amounts (KMC/WB amendment fine
    tables: ``Section 498 ... 500/-``), but a rupee mention that has a fee-
    context word (``fee``/``charge``/``tax``/``rent``/...) within
    :data:`_FEE_WINDOW` characters on either side is a *fee*, not a penalty
    — e.g. "levy a non-refundable processing fee of Rs. 1,000/-" — and is
    skipped.  Genuine fine rows ("fine of Rs.", "penalty of Rs.", bare
    ``500/-`` schedule cells) have no fee-context word and still fire.

    Args:
        text: Provision text to scan.
        pattern: The pre-compiled ``_RUPEES_RULE_PATTERN`` from the caller
            (avoids re-compiling per provision).
    """
    rule = pattern if pattern is not None else re.compile(_RUPEES_RULE_PATTERN, re.IGNORECASE)
    for m in rule.finditer(text):
        before = text[max(0, m.start() - _FEE_WINDOW) : m.start()]
        after = text[m.end() : m.end() + _FEE_WINDOW]
        if _FEE_CONTEXT_RE.search(before) or _FEE_CONTEXT_RE.search(after):
            continue
        # Financial-statement format headers ("Rupees in ____ ) Particulars")
        # are table boilerplate, not penalties.  Confirmed false positive
        # (2026-08-11): COMPANIES_ACT_2013_SEC_349 (Schedule III profit-and-
        # loss format) was tagged PRESCRIBES_PENALTY on "(Rupees in .....)".
        # NOTE: the ``^\s*in\b`` check would also suppress a hypothetical
        # genuine "rupees in default" row — safe because the rupee rule is the
        # LOWEST-precedence penalty rule: when a higher-confidence penalty
        # phrase ("penalty of" / "fine which may extend" / "punishable with")
        # matches, this rule never fires anyway.  Do not reorder the rules
        # above it.
        if re.search(r"^\s*in\b", after, re.IGNORECASE) or re.search(r"\bparticulars\b", after, re.IGNORECASE):
            continue
        return m
    return None


def _first_match_outside_spans(
    pattern: re.Pattern[str],
    text: str,
    spans: list[tuple[int, int]],
) -> re.Match[str] | None:
    """First *pattern* match in *text* whose start is outside every *span*.

    Used by the generic "shall" rule so it skips "shall" tokens that belong
    to higher-precedence prohibition phrases but still tags a genuine "shall"
    duty elsewhere in the provision.
    """
    for m in pattern.finditer(text):
        if not any(start <= m.start() < end for start, end in spans):
            return m
    return None


# End of enrichment.py

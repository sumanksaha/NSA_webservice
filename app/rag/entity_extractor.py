"""Legal entity extractor (Agent A, §3.4 — closes the last R6 gap).

Extracts legal entities — **judge/person names**, **organizations**
(companies, authorities, ministries), **case numbers/citations**, and
**statutory provisions** — from legal document text, following the scope's
three-tier strategy:

1. **Rule-based first** (always available, no dependencies): regex patterns
   tuned for Indian legal prose (``Justice X``, ``X Pvt. Ltd.``,
   ``Criminal Appeal No. 1234 of 2004``, ``Section 55 of the FSS Act, 2006``).
2. **spaCy NER fallback** (when installed): maps ``PERSON`` → ``person``,
   ``ORG`` → ``organization``, ``LAW`` → ``statute``.  Lazily loaded,
   graceful when absent.
3. **LLM fallback** (when spaCy is NOT installed): a prompt asking for a
   JSON entity list, parsed best-effort.  Activated by injecting an
   ``llm`` client (e.g. :class:`GroundedLLMClient`) or setting
   ``RAG_ENTITY_LLM=true``.  Never fires by default (offline-safe).

Entity payload shape mirrors the ``citations``/``references`` dual pattern
from ``RAG_AGENT_A_SCOPE.md`` §5.1/§5.2:

- Qdrant payload (``Chunk.entities``) → plain entity names, e.g.
  ``["Justice S. Ravindra Bhat", "FSSAI", "Section 55"]``
- ``LegalChunk.entities`` JSON column (structured, §5.2) →
  ``[{"name": ..., "type": "person|organization|case|statute", "confidence": 0.85}]``

Per-field confidence reuses ``app.metadata_extractor.confidence.score_field``
(§2.2 R2 reuse) — regex base 0.85, NER base 0.70, LLM base 0.80.

The extractor is injectable (mock-injection pattern) and imports nothing
heavy at import time; the spaCy / LLM backends resolve lazily.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Recognised entity types (§3.4).
VALID_ENTITY_TYPES = frozenset({"person", "organization", "case", "statute"})

#: spaCy label -> entity type mapping (spaCy has no case-number label).
_SPACY_LABEL_MAP = {
    "PERSON": "person",
    "ORG": "organization",
    "LAW": "statute",
}

#: Hard cap on entities returned per call — keeps payloads bounded.
MAX_ENTITIES = 100

# --------------------------------------------------------------------------- #
# Rule-based patterns (tier 1 — always available)
# --------------------------------------------------------------------------- #

_PERSON_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "Hon'ble Justice S. Ravindra Bhat", "Mr. Justice Sharma", "Dr. A. K. Rao"
    re.compile(
        r"(?:Hon'?ble\s+)?(?:Justice|Shri|Smt\.?|Mr\.?|Mrs\.?|Ms\.?|Dr\.?)\s+"
        r"([A-Z][A-Za-z]*(?:\.?\s+[A-Z][A-Za-z]*\.?)+)"
    ),
)

_ORG_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "Hindustan Unilever Pvt. Ltd.", "Nestle India Limited", "Tata Chemicals Ltd."
    re.compile(
        r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,5})\s+"
        r"(?:Pvt\.?\s*Ltd\.?|Private\s+Limited|Limited|Ltd\.?|LLP|Corporation|Corp\.?|Inc\.?)"
    ),
    # "Food Safety and Standards Authority of India", "Ministry of Health and Family Welfare"
    # (title-case words may be adjacent OR separated by lowercase connectors
    # like "and"/"of"; an optional trailing "of <Country>" — e.g.
    # "Authority of India" — is captured too)
    re.compile(
        r"([A-Z][a-zA-Z]+(?:\s+(?:(?:of|and|for|the|in|on|to|with|by|at)\s+)?[A-Z][a-zA-Z]+){0,5})\s+"
        r"(?:Authority|Commission|Board|Ministry|Association|Council|Institute|Department|Regulator)"
        r"(?:\s+of\s+(?:the\s+)?[A-Z][a-zA-Z]+)?\b"
    ),
)

_CASE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "Criminal Appeal No. 1234 of 2004", "W.P. (C) No. 123/2006", "Civil Suit No. 45 of 2010",
    # "Criminal Appeal 1234 of 2004" (optional "No." and optional court designation "(C)")
    re.compile(
        r"\b(?:(?:Criminal|Civil|Writ|Special|Regular|First|Second|Company|Misc\.?)\s+)?"
        r"(?:Appeal|Petition|Suit|Application|Case|W\.?P\.?)\s*"
        r"(?:\([A-Z]\)\s*)?(?:No\.?|No)?\s*\d+(?:/\d{2,4})?(?:\s+of\s+\d{4})?\b"
    ),
    # "AIR 2004 SC 1234", "2004 (2) SCC 567"
    re.compile(r"\bAIR\s+\d{4}\s+[A-Z]{2,4}\s+\d+\b"),
    re.compile(r"\b\d{4}\s*\(\s*\d+\s*\)\s*SCC\s+\d+\b"),
)

_STATUTE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "Section 55 of the Food Safety and Standards Act, 2006" / "Rule 12 of the Rules"
    re.compile(
        r"\b(?:Section|Sec\.?|§|Rule|Regulation|Clause|Schedule|Article)\s+"
        r"(\d+[A-Za-z]?(?:\([^)]*\))*)"
        r"(?:\s+of\s+(?:the\s+)?([A-Z][A-Za-z\s,]+?(?:Act|Rules?|Regulations?|Code)))?"
    ),
)


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass
class LegalEntity:
    """One extracted legal entity (structured §5.2 shape)."""

    name: str
    entity_type: str  # person | organization | case | statute
    confidence: float = 0.0
    method: str = ""  # regex | ner | llm

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.entity_type,
            "confidence": round(float(self.confidence), 4),
            "method": self.method,
        }


@dataclass
class EntityExtraction:
    """Extraction result — deduped entities in document order."""

    entities: list[LegalEntity] = field(default_factory=list)
    methods_used: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.entities)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "count": len(self.entities),
            "methods_used": list(self.methods_used),
        }

    def payload_names(self) -> list[str]:
        """§5.1 payload shape — plain entity names, deduped, in order."""
        return [e.name for e in self.entities]


class LegalEntityExtractor:
    """Extract legal entities from text (rule-based → spaCy → LLM fallback).

    Args:
        llm: Optional injectable LLM client with ``call(system_prompt,
            user_prompt) -> response`` (e.g. :class:`GroundedLLMClient`).
            Used only when spaCy is unavailable.  When ``None``, the LLM
            fallback is gated on the ``RAG_ENTITY_LLM=true`` env flag.
        spacy_loader: Optional injectable ``load() -> nlp`` callable (tests).
        ner: Optional pre-built spaCy-style NER extractor with
            ``extract_entities(text) -> {label: [(text, confidence)]}``
            (tests); the real spaCy backend is built lazily.
    """

    def __init__(
        self,
        llm: Any | None = None,
        spacy_loader: Any | None = None,
        ner: Any | None = None,
    ) -> None:
        self._llm = llm
        self._spacy_loader = spacy_loader
        self._ner = ner

    # ------------------------------------------------------------------ #
    # Lazy backends
    # ------------------------------------------------------------------ #

    def _get_ner(self) -> Any | None:
        """Build the spaCy NER backend lazily (None when spaCy absent)."""
        if self._ner is not None:
            return self._ner
        try:
            import spacy  # noqa: F401 - optional backend

            from app.metadata_extractor.ner import NERExtractor

            self._ner = NERExtractor()
        except ImportError:
            self._ner = None
        return self._ner

    def _get_llm(self) -> Any | None:
        """Resolve the LLM fallback (injected > env-gated default)."""
        if self._llm is False:  # explicitly disabled / failed to build
            return None
        if self._llm is not None:
            return self._llm
        if os.environ.get("RAG_ENTITY_LLM", "").lower() != "true":
            self._llm = False
            return None
        try:
            from app.rag.generation.llm_client import GroundedLLMClient

            self._llm = GroundedLLMClient()
        except Exception as exc:  # noqa: BLE001 - fallback is best-effort
            logger.warning("LegalEntityExtractor LLM fallback unavailable: %s", exc)
            self._llm = False
        return self._llm if self._llm is not False else None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def extract(self, text: str) -> EntityExtraction:
        """Run the extraction chain and merge, dedupe, cap the results.

        Tier 1 rule-based extraction always runs.  Tier 2 spaCy NER is added
        when available.  Tier 3 LLM runs ONLY when spaCy is unavailable
        (per §3.4: "If spaCy not installed, use LLM for entity extraction").
        Best-effort: any backend failure yields fewer entities, never raises.
        """
        if not text or not text.strip():
            return EntityExtraction()

        merged: list[LegalEntity] = []
        methods: list[str] = []

        merged.extend(self._rule_based(text))
        methods.append("regex")

        ner = self._get_ner()
        if ner is not None:
            merged.extend(self._spacy_entities(ner, text))
            methods.append("ner")
        else:
            llm = self._get_llm()
            if llm is not None:
                merged.extend(self._llm_entities(llm, text))
                methods.append("llm")

        return EntityExtraction(
            entities=_dedupe(merged)[:MAX_ENTITIES],
            methods_used=methods,
        )

    def payload_entities(self, text: str) -> list[str]:
        """§5.1 ``entities`` payload — plain entity names (smoke shape)."""
        return self.extract(text).payload_names()

    def structured_entities(self, text: str) -> list[dict[str, Any]]:
        """§5.2 ``LegalChunk.entities`` JSON shape ``[{name,type,confidence}]``."""
        return [e.to_dict() for e in self.extract(text).entities]

    def enrich_chunk(self, chunk: Any) -> Any:
        """Set ``chunk.entities`` from the chunk's own text; return the chunk.

        Mirrors :meth:`CitationAdapter.enrich_chunk` — the payload-shape list
        of plain names (the ``Chunk`` dataclass field).
        """
        text = str(getattr(chunk, "chunk_text", "") or "")
        if text and hasattr(chunk, "entities"):
            chunk.entities = self.payload_entities(text)
        return chunk

    def enrich_document(self, document: dict[str, Any], text: str | None = None) -> dict[str, Any]:
        """Merge extracted entities into ``document``, filling ONLY missing keys.

        Caller-provided values always win.  Sets the ``entities`` key
        (structured list — the document-level summary) and an
        ``entity_extraction`` cache key for the ``LegalDocument.metadata_json``
        cache.
        """
        merged = dict(document)
        if text:
            extraction = self.extract(text)
        else:
            extraction = EntityExtraction()
        if extraction.entities:
            merged.setdefault("entities", [e.to_dict() for e in extraction.entities])
        merged.setdefault("entity_extraction", extraction.to_dict())
        return merged

    # ------------------------------------------------------------------ #
    # Tier 1 — rule-based
    # ------------------------------------------------------------------ #

    def _rule_based(self, text: str) -> list[LegalEntity]:
        entities: list[LegalEntity] = []
        for pattern, entity_type in (
            (_PERSON_PATTERNS, "person"),
            (_ORG_PATTERNS, "organization"),
            (_CASE_PATTERNS, "case"),
            (_STATUTE_PATTERNS, "statute"),
        ):
            for regex in pattern:
                for match in regex.finditer(text):
                    # The statute pattern's full match already includes the
                    # provision + optional "of the <Act>" — use it verbatim.
                    name = match.group(0).strip()
                    if not name:
                        continue
                    confidence = self._score(name, entity_type, len(text))
                    entities.append(
                        LegalEntity(name=name, entity_type=entity_type, confidence=confidence, method="regex")
                    )
        return entities

    # ------------------------------------------------------------------ #
    # Tier 2 — spaCy NER
    # ------------------------------------------------------------------ #

    def _spacy_entities(self, ner: Any, text: str) -> list[LegalEntity]:
        try:
            grouped = ner.extract_entities(text) or {}
        except Exception as exc:  # noqa: BLE001 - best-effort NER
            logger.warning("LegalEntityExtractor spaCy NER failed: %s", exc)
            return []
        entities: list[LegalEntity] = []
        for label, items in grouped.items():
            entity_type = _SPACY_LABEL_MAP.get(str(label).upper())
            if entity_type is None:
                continue
            for name, confidence in items:
                name = str(name or "").strip()
                if not name or not _looks_like_entity(name):
                    continue
                entities.append(
                    LegalEntity(
                        name=name,
                        entity_type=entity_type,
                        confidence=float(confidence or 0.0),
                        method="ner",
                    )
                )
        return entities

    # ------------------------------------------------------------------ #
    # Tier 3 — LLM fallback (only when spaCy unavailable)
    # ------------------------------------------------------------------ #

    def _llm_entities(self, llm: Any, text: str) -> list[LegalEntity]:
        system_prompt = (
            "You extract legal entities from Indian legal documents. "
            "Return ONLY a JSON array of objects, each with keys "
            '"name" (the entity text), "type" (one of: person, organization, '
            'case, statute), and "confidence" (0.0-1.0). '
            "Extract: judge/person names, companies and authorities, case "
            "numbers/citations, and statutory provisions (e.g. Section 55 of "
            "an Act). No prose, no markdown."
        )
        user_prompt = f"Extract legal entities from this text:\n\n{text[:8_000]}"
        try:
            response = llm.call(system_prompt, user_prompt, max_tokens=600)
        except Exception as exc:  # noqa: BLE001 - best-effort LLM
            logger.warning("LegalEntityExtractor LLM failed: %s", exc)
            return []
        payload = str(getattr(response, "text", "") or "")
        if not payload:
            return []
        return self._parse_llm_json(payload)

    def _parse_llm_json(self, payload: str) -> list[LegalEntity]:
        """Parse the LLM's JSON array best-effort (strip fences)."""
        cleaned = payload.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Tolerate a single leading/trailing element or braces wrappers.
            match = re.search(r"\[.*\]", cleaned, re.DOTALL)
            if not match:
                return []
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                logger.warning("LegalEntityExtractor: unparseable LLM entity JSON")
                return []
        if not isinstance(data, list):
            return []
        entities: list[LegalEntity] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            entity_type = str(item.get("type") or "").strip().lower()
            if not name or entity_type not in VALID_ENTITY_TYPES:
                continue
            try:
                confidence = float(item.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            entities.append(
                LegalEntity(name=name, entity_type=entity_type, confidence=confidence, method="llm")
            )
        return entities

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _score(name: str, entity_type: str, text_length: int) -> float:
        """Reuse the R2 confidence scorer (§2.2) for regex entities."""
        from app.metadata_extractor.confidence import score_field

        result = score_field(
            name,
            "regex",
            [(name,)],  # single candidate — consensus boosts do not apply
            field_name=entity_type,
            text_length=text_length,
        )
        return float(getattr(result, "score", 0.85) or 0.85)


def _dedupe(entities: list[LegalEntity]) -> list[LegalEntity]:
    """De-duplicate by (type, lowercased name), keeping first occurrence."""
    seen: set[tuple[str, str]] = set()
    result: list[LegalEntity] = []
    for entity in entities:
        key = (entity.entity_type, entity.name.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(entity)
    return result


def _looks_like_entity(name: str) -> bool:
    """Reject all-lowercase / numeric fragments from NER (legal entities are proper nouns)."""
    if len(name) < 2:
        return False
    return any(ch.isupper() for ch in name) or any(ch.isdigit() for ch in name)


def _plain_entity_names(value: Any) -> list[str]:
    """Coerce a doc-level ``entities`` value to a payload list of plain names.

    Accepts both the §5.1 payload shape (already-plain names, e.g.
    ``["Section 55"]``) and the structured §5.2 shape (dicts with a
    ``name`` key, e.g. ``[{"name": "Section 55", "type": "statute"}]``)
    — so a document dict enriched via :meth:`LegalEntityExtractor.enrich_document`
    flows into ``Chunk.entities`` without leaking dicts into the
    ``list[string]`` payload.
    """
    names: list[str] = []
    for item in value or []:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("name")
        else:
            continue
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


# End of entity_extractor.py

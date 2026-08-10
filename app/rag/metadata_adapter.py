"""Metadata extractor adapter (Agent A, Phase 2 — Day 6, §4).

Adapts the R2 ``LegalMetadataEngine`` (``app/metadata_extractor``) output
into the §5.1 Qdrant payload fields: ``document_title``, ``document_type``
(enum-normalized), ``authority``, ``jurisdiction``, ``state``,
``effective_date`` / ``enactment_date`` (ISO-normalized for Qdrant date
filters), and ``is_current`` (derived from amendment status).

``enrich_document`` merges the extracted metadata into a document-metadata
dict used by the chunker — **never clobbering** explicitly-provided values —
so the Day 4 ingestion pipeline can produce richer payloads without losing
caller-supplied fields.

The engine is injectable (mock-injection pattern) and imported lazily so the
module boots without the extractor stack.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

#: §5.1 document_type enum.
VALID_DOCUMENT_TYPES = frozenset(
    {"act", "rule", "regulation", "notification", "circular", "case_law"}
)

#: Map raw metadata document-type values onto the §5.1 enum.
_DOC_TYPE_ALIASES = {
    "act": "act",
    "rule": "rule",
    "rules": "rule",
    "regulation": "regulation",
    "regulations": "regulation",
    "notification": "notification",
    "notifications": "notification",
    "circular": "circular",
    "circulars": "circular",
    "case law": "case_law",
    "case_law": "case_law",
    "judgment": "case_law",
    "judgement": "case_law",
}

#: Amendment statuses that mean the document is no longer current.
_NON_CURRENT_STATUSES = frozenset({"repealed", "superseded", "withdrawn", "rescinded"})


@dataclass
class MetadataExtraction:
    """Extracted document metadata, adapted to the §5.1 payload surface."""

    document_title: str = ""
    document_type: str = ""  # §5.1 enum ("" when unclassifiable)
    authority: str = ""
    jurisdiction: str = ""
    state: str = ""
    effective_date: str | None = None  # ISO-8601
    enactment_date: str | None = None  # ISO-8601
    is_current: bool = True
    version: str = ""
    amendment_status: str = ""
    overall_confidence: float = 0.0
    #: Flat raw values + per-field scores (for the metadata_json cache).
    fields: dict[str, str] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_title": self.document_title,
            "document_type": self.document_type,
            "authority": self.authority,
            "jurisdiction": self.jurisdiction,
            "state": self.state,
            "effective_date": self.effective_date,
            "enactment_date": self.enactment_date,
            "is_current": self.is_current,
            "version": self.version,
            "amendment_status": self.amendment_status,
            "overall_confidence": round(self.overall_confidence, 4),
            "fields": dict(self.fields),
            "scores": dict(self.scores),
        }


class MetadataAdapter:
    """Map :class:`LegalMetadataEngine` output onto §5.1 payload fields.

    Args:
        engine: Optional pre-built ``LegalMetadataEngine`` (injected for
            tests; the real one is built lazily).
    """

    def __init__(self, engine: Any | None = None) -> None:
        self._engine = engine

    # ------------------------------------------------------------------ #
    # Lazy accessor
    # ------------------------------------------------------------------ #

    def _get_engine(self) -> Any:
        if self._engine is None:
            from app.metadata_extractor.engine import LegalMetadataEngine

            self._engine = LegalMetadataEngine()
        return self._engine

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def extract(self, text: str) -> MetadataExtraction:
        """Run the metadata engine and adapt its output."""
        meta = self._get_engine().extract(text)
        return self._adapt(meta)

    def enrich_document(self, document: dict[str, Any], text: str | None = None) -> dict[str, Any]:
        """Merge extracted metadata into ``document``, filling ONLY missing keys.

        Caller-provided values always win.  Sets both the chunker-facing keys
        (``title`` / ``type``) and the payload-facing keys
        (``document_title`` / ``document_type`` / ``authority`` /
        ``jurisdiction`` / ``state`` / dates / ``is_current`` / ``version``).
        """
        merged = dict(document)
        if text:
            extraction = self.extract(text)
        else:
            extraction = MetadataExtraction()
        # title / document_title
        if extraction.document_title:
            merged.setdefault("title", extraction.document_title)
            merged.setdefault("document_title", extraction.document_title)
        # type / document_type
        if extraction.document_type:
            merged.setdefault("type", extraction.document_type)
            merged.setdefault("document_type", extraction.document_type)
        for key, value in (
            ("authority", extraction.authority),
            ("jurisdiction", extraction.jurisdiction),
            ("state", extraction.state),
            ("effective_date", extraction.effective_date),
            ("enactment_date", extraction.enactment_date),
            ("version", extraction.version),
        ):
            if value:
                merged.setdefault(key, value)
        if extraction.amendment_status:
            merged.setdefault("amendment_status", extraction.amendment_status)
        merged.setdefault("is_current", extraction.is_current)
        # Keep the full extraction available for the metadata_json cache.
        merged.setdefault("metadata_extraction", extraction.to_dict())
        return merged

    # ------------------------------------------------------------------ #
    # Adaptation internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _adapt(meta: Any) -> MetadataExtraction:
        """Adapt a ``LegalMetadata`` (or compatible) object."""

        def value(field_name: str) -> str:
            f = getattr(meta, field_name, None)
            return str(getattr(f, "value", "") or "")

        def score(field_name: str) -> float:
            f = getattr(meta, field_name, None)
            try:
                return float(getattr(f, "score", 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        amendment_status = value("amendment_status")
        document_type = MetadataAdapter.normalize_document_type(value("document_type"))
        return MetadataExtraction(
            document_title=value("title"),
            document_type=document_type,
            authority=value("authority"),
            jurisdiction=value("jurisdiction"),
            state=value("state"),
            effective_date=MetadataAdapter.normalize_date(value("effective_date")),
            enactment_date=MetadataAdapter.normalize_date(value("date")),
            is_current=MetadataAdapter.is_current_from_status(amendment_status),
            version=value("version"),
            amendment_status=amendment_status,
            overall_confidence=float(getattr(meta, "overall_confidence", 0.0) or 0.0),
            fields={
                k: value(k)
                for k in (
                    "title", "version", "date", "authority", "gazette_number",
                    "notification_number", "language", "jurisdiction", "state",
                    "country", "document_type", "amendment_status", "effective_date",
                )
            },
            scores={
                k: score(k)
                for k in (
                    "title", "version", "date", "authority", "jurisdiction",
                    "state", "document_type", "amendment_status", "effective_date",
                )
            },
        )

    @staticmethod
    def normalize_document_type(value: str) -> str:
        """Map a raw document-type value onto the §5.1 enum (\"\" when unknown)."""
        key = str(value or "").strip().lower()
        return _DOC_TYPE_ALIASES.get(key, "")

    @staticmethod
    def normalize_date(value: Any) -> str | None:
        """Coerce common legal date formats to ISO-8601 (passes ISO through).

        Handles ``YYYY-MM-DD``, ``DD/MM/YYYY``, ``24 August 2006``, and
        ``1st Day of January, 2006`` forms; returns the raw string when it
        cannot be parsed.
        """
        if value is None or str(value).strip() == "":
            return None
        s = str(value).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return s
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
        if m:
            day, month, year = m.groups()
            try:
                return datetime(int(year), int(month), int(day)).date().isoformat()
            except ValueError:
                return s  # e.g. 31/13/2020 — invalid, keep raw
        m = re.fullmatch(
            r"(\d{1,2})(?:st|nd|rd|th)?(?:\s+Day\s+of)?\s+([A-Za-z]+),?\s+(\d{4})",
            s,
        )
        if m:
            day, month, year = m.groups()
            try:
                return datetime.strptime(f"{day} {month} {year}", "%d %B %Y").date().isoformat()
            except ValueError:
                return s
        return s

    @staticmethod
    def is_current_from_status(amendment_status: str) -> bool:
        """A repealed/superseded document is not current; everything else is."""
        return str(amendment_status or "").strip().lower() not in _NON_CURRENT_STATUSES


# End of metadata_adapter.py

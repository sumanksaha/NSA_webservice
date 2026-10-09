"""Ingestion-time provision extraction adapter (ADR-0009 §2.3).

Runs the extractor over a document's freshly-chunked text and attaches
``provision_spans`` / ``provision_confidence`` / ``provision_modality`` to the
chunk payloads before they are embedded and upserted.

Fail-closed: any exception degrades to a no-op (returns ``0``) with a warning —
ingestion must never block on the optional Tier-2 stack or on a malformed
document.  ``chunk_id``s and chunk text are never modified.
"""

from __future__ import annotations

import logging
from typing import Any

from app.rag.provision_extractor.disambiguator import Disambiguator
from app.rag.provision_extractor.isolator import attach_provision_spans, isolate_chunks

logger = logging.getLogger(__name__)


class ProvisionExtractorAdapter:
    """Annotate a document's chunks with extracted provision spans.

    Args:
        disambiguator: Optional pre-built :class:`Disambiguator` (injected for
            tests); built lazily from config otherwise.

    """

    def __init__(self, disambiguator: Disambiguator | None = None) -> None:
        self._disambiguator = disambiguator

    def extract(
        self,
        chunks: list[Any],
        *,
        act_name: str = "",
        document_title: str = "",
        document_id: str = "",
    ) -> int:
        """Annotate *chunks* in place; return the number of records extracted."""
        if not chunks:
            return 0
        try:
            records = isolate_chunks(
                chunks,
                act_name=act_name,
                document_title=document_title,
                document_id=document_id,
                disambiguator=self._disambiguator,
            )
            attach_provision_spans(chunks, records)
            return len(records)
        except Exception as exc:  # fail-closed: never block ingestion
            logger.warning("provision extraction failed for %r: %s", document_id or document_title, exc)
            return 0

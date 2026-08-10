"""Document classification adapter (Agent A, Phase 2 — Day 9, §4).

Classifies a legal document's §5.1 payload fields — ``document_type`` (the
enum: ``act``/``rule``/``regulation``/``notification``/``circular``/``case_law``)
and ``authority`` — by reusing the R2 field extractors
:class:`~app.metadata_extractor.extractors.base.DocumentTypeExtractor` and
:class:`~app.metadata_extractor.extractors.base.AuthorityExtractor`.

The classifier complements :class:`MetadataAdapter` (Day 6): the adapter maps
the full ``LegalMetadataEngine`` output, while the classifier is a focused,
cheap document-classification entry point (title/type/authority smoke test,
§6.3) built directly on the two named extractors.

- ``classify(text)`` → a :class:`DocumentClassification` with the raw label,
  the §5.1-normalized ``document_type`` (via ``MetadataAdapter.normalize_document_type``
  + classifier-specific alias extensions), authority, and per-field
  confidence.
- ``enrich_document(document, text)`` merges classification into a
  document-metadata dict, **never clobbering** caller-provided values
  (mirrors the Day 6 adapter's semantics) — wired OPT-IN into
  ``IngestionPipeline`` like the other Phase 2 adapters.

Both extractors are injectable (mock-injection pattern) and imported lazily
so the module boots without the metadata-extractor stack.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.rag.metadata_adapter import MetadataAdapter

logger = logging.getLogger(__name__)

#: Classifier-specific label → §5.1 enum mappings for raw labels the R2
#: ``DocumentTypeExtractor`` can emit that the shared adapter's alias map
#: does not cover.  A gazette notification IS a notification; ``Bill`` and
#: ``Policy`` have no §5.1 enum value and map to ``""``.
_TYPE_ALIAS_EXTENSIONS = {
    "gazette notification": "notification",
    "bill": "",
    "policy": "",
}


@dataclass
class DocumentClassification:
    """Classification result for one legal document."""

    document_type: str = ""  # §5.1 enum ("" when unclassifiable)
    document_type_label: str = ""  # raw extractor label, e.g. "Act"
    authority: str = ""
    document_type_confidence: float = 0.0
    authority_confidence: float = 0.0
    overall_confidence: float = 0.0
    method: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_type": self.document_type,
            "document_type_label": self.document_type_label,
            "authority": self.authority,
            "document_type_confidence": round(self.document_type_confidence, 4),
            "authority_confidence": round(self.authority_confidence, 4),
            "overall_confidence": round(self.overall_confidence, 4),
            "method": self.method,
            "fields": dict(self.fields),
            "scores": dict(self.scores),
        }

    def to_payload(self) -> dict[str, Any]:
        """§5.1 payload fields (filterable ``document_type`` + ``authority``)."""
        return {
            "document_type": self.document_type,
            "authority": self.authority,
        }


class DocumentClassifier:
    """Classify a document's §5.1 ``document_type`` + ``authority``.

    Args:
        type_extractor: Optional ``DocumentTypeExtractor`` (injected for
            tests; the real one is built lazily).
        authority_extractor: Optional ``AuthorityExtractor`` (injected for
            tests; the real one is built lazily).
    """

    def __init__(
        self,
        type_extractor: Any | None = None,
        authority_extractor: Any | None = None,
    ) -> None:
        self._type_extractor = type_extractor
        self._authority_extractor = authority_extractor

    # ------------------------------------------------------------------ #
    # Lazy accessors
    # ------------------------------------------------------------------ #

    def _get_type_extractor(self) -> Any:
        if self._type_extractor is None:
            from app.metadata_extractor.extractors.base import DocumentTypeExtractor

            self._type_extractor = DocumentTypeExtractor()
        return self._type_extractor

    def _get_authority_extractor(self) -> Any:
        if self._authority_extractor is None:
            from app.metadata_extractor.extractors.base import AuthorityExtractor

            self._authority_extractor = AuthorityExtractor()
        return self._authority_extractor

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def classify(self, text: str) -> DocumentClassification:
        """Run the R2 extractors and adapt the best candidates.

        The ``document_type`` label is normalized to the §5.1 enum (raw label
        preserved on ``document_type_label``); ``authority`` passes through.
        Extraction is best-effort — extractor failures yield empty fields
        rather than raising (mirrors the R2 engine's per-field isolation).
        """
        if not text or not text.strip():
            # No signal -> no classification (mirrors the pipeline's empty-
            # document short-circuit rather than trusting a heuristic default).
            return DocumentClassification()

        type_label, type_conf, type_method = self._best(self._get_type_extractor(), text)
        authority, auth_conf, auth_method = self._best(self._get_authority_extractor(), text)

        normalized = self.normalize_type(type_label)
        field_confs = [c for c in (type_conf, auth_conf) if c > 0]
        overall = sum(field_confs) / len(field_confs) if field_confs else 0.0
        method = type_method or auth_method

        return DocumentClassification(
            document_type=normalized,
            document_type_label=type_label,
            authority=authority,
            document_type_confidence=type_conf,
            authority_confidence=auth_conf,
            overall_confidence=overall,
            method=method,
            fields={"document_type": type_label, "authority": authority},
            scores={"document_type": type_conf, "authority": auth_conf},
        )

    def payload(self, text: str) -> dict[str, Any]:
        """§5.1 payload dict for ``text`` (smoke-test shape, §6.3)."""
        return self.classify(text).to_payload()

    def enrich_document(self, document: dict[str, Any], text: str | None = None) -> dict[str, Any]:
        """Merge classification into ``document``, filling ONLY missing keys.

        Caller-provided values always win.  Sets the chunker-facing ``type``
        key and the payload-facing ``document_type`` / ``authority`` keys,
        plus ``document_type_label`` and a ``document_classification`` cache
        key (for the ``LegalDocument.metadata_json`` cache).
        """
        merged = dict(document)
        if text:
            classification = self.classify(text)
        else:
            classification = DocumentClassification()
        if classification.document_type:
            merged.setdefault("type", classification.document_type)
            merged.setdefault("document_type", classification.document_type)
        if classification.document_type_label:
            merged.setdefault("document_type_label", classification.document_type_label)
        if classification.authority:
            merged.setdefault("authority", classification.authority)
        merged.setdefault("document_classification", classification.to_dict())
        return merged

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def normalize_type(raw: str) -> str:
        """Map a raw document-type label onto the §5.1 enum.

        Reuses ``MetadataAdapter.normalize_document_type`` first, then applies
        the classifier's alias extensions (e.g. ``"Gazette Notification"`` →
        ``"notification"``).  Unknown labels map to ``""``.
        """
        normalized = MetadataAdapter.normalize_document_type(raw)
        if normalized:
            return normalized
        return _TYPE_ALIAS_EXTENSIONS.get(str(raw or "").strip().lower(), "")

    @staticmethod
    def _best(extractor: Any, text: str) -> tuple[str, float, str]:
        """Return ``(value, confidence, method)`` of the top candidate.

        Candidates come back confidence-sorted (the R2 ``_deduplicate``
        helper); the first one is the best.  Best-effort: an empty result or
        a failing extractor yields ``("", 0.0, "")``.
        """
        try:
            candidates = extractor.extract(text) or []
        except Exception as exc:  # noqa: BLE001 - best-effort classification
            logger.warning("DocumentClassifier extractor failed: %s", exc)
            candidates = []
        if not candidates:
            return "", 0.0, ""
        value, conf, method, _detail = candidates[0]
        return str(value or ""), float(conf or 0.0), str(method or "")


# End of document_classifier.py

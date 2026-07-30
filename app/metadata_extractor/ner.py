"""Named Entity Recognition integration using spaCy.

Serves as a complementary extraction method — when regex patterns are
uncertain or miss fields, NER provides alternative candidates with
its own confidence scores.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# spaCy model to load — try small first, fall back to medium
_PREFERRED_MODEL = "en_core_web_sm"
_ALTERNATE_MODEL = "en_core_web_md"


class NERExtractor:
    """Thin wrapper around spaCy NER for legal document metadata.

    Lazy-loads the spaCy model on first use (avoids slow startup).
    """

    def __init__(self) -> None:
        self._nlp = None
        self._model_name: str | None = None

    @property
    def available(self) -> bool:
        """Whether a spaCy model is loaded and available."""
        return self._nlp is not None

    def extract_entities(self, text: str) -> dict[str, list[tuple[str, float]]]:
        """Run NER on text and return entities grouped by label.

        Returns:
            Dict mapping label (e.g. ``ORG``, ``DATE``, ``GPE``, ``LAW``)
            to a list of ``(text, confidence)`` tuples.

        """
        nlp = self._get_nlp()
        if nlp is None:
            return {}

        try:
            doc = nlp(text[:100_000])  # limit input to avoid OOM
        except Exception as exc:
            logger.warning("spaCy NER failed: %s", exc)
            return {}

        results: dict[str, list[tuple[str, float]]] = {}
        for ent in doc.ents:
            label = ent.label_
            # spaCy doesn't expose per-entity confidence, use heuristic
            confidence = min(0.85, len(ent.text) / 100.0) if ent.text else 0.5
            results.setdefault(label, []).append((ent.text.strip(), confidence))

        return results

    def _get_nlp(self):
        """Lazy-load the spaCy model."""
        if self._nlp is not None:
            return self._nlp

        for model in (_PREFERRED_MODEL, _ALTERNATE_MODEL):
            try:
                import spacy

                self._nlp = spacy.load(model)
                self._model_name = model
                logger.info("Loaded spaCy model: %s", model)
                return self._nlp
            except OSError:
                logger.debug("spaCy model '%s' not found", model)
            except ImportError:
                logger.debug("spaCy not installed")
                return None

        logger.warning("No spaCy model available — install with: python -m spacy download en_core_web_sm")
        return None

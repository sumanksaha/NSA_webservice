"""Boundary disambiguation — Tier 1 rules (P0) with a Tier 2 ML fallback.

The disambiguator answers one question per candidate: *is this a real
provision boundary?*  Two implementations share one interface:

* **rules** (default): a deterministic weighted sum of the features from
  :mod:`~app.rag.provision_extractor.features`, squashed through a logistic.
  Zero third-party dependencies — this is the runtime fallback and the
  baseline the ML tier must beat.
* **hybrid**: a lazily-loaded ``scikit-learn`` model artifact
  (``models/provision_boundaries.joblib``).  If ``scikit-learn``, ``joblib``,
  or the artifact is absent/corrupt, the mode logs a warning and **silently
  falls back to rules** — ingestion must never block on the optional ML stack
  (ADR-0009 fail-closed requirement).

The decision records its tier, so provenance (``tier1_regex`` / ``tier2_ml``)
flows into every emitted :class:`~app.rag.provision_extractor.models.ProvisionRecord`.
"""

from __future__ import annotations

import logging
import math
import os

from app.rag.provision_extractor.features import feature_vector
from app.rag.provision_extractor.models import TIER_ML, TIER_RULES

logger = logging.getLogger(__name__)

#: Default artifact path (ADR-0009 / provision-extraction-plan §2.3).
DEFAULT_MODEL_PATH = "models/provision_boundaries.joblib"

#: Default Tier-2 decision threshold.  Deliberately lower than the rules
#: threshold: the learned model is under-confident on rare header shapes, and
#: the deterministic hard vetoes keep the noise floor at zero regardless.
#: Threshold sweep (2026-10-01, real corpus): 0.15/0.25 beat rules on recall and
#: gold resolution, 0.70 did not; 0.25 is the conservative operating point.
DEFAULT_ML_THRESHOLD = 0.25

#: Rules weights — hand-tuned for precision on silver data.  Treat as tunable:
#: the training script re-fits an equivalent model and the eval gate decides
#: whether hybrid replaces rules.
_RULE_WEIGHTS: dict[str, float] = {
    "is_line_start": 0.30,
    "in_act_range": 0.50,
    "source_engine_main": 1.00,
    "source_engine_word": 0.60,
    "source_dotted_clause": 0.60,
    "source_l4_header": 0.10,
    "title_score": 0.50,
    "has_emdash_title": 0.40,
    "is_monotonic": 0.30,
    "is_first_occurrence": 0.60,
    "page_number_only": -2.00,
    "is_year_like": -3.00,
    "crossref_density": -0.40,
}

#: Strong penalties that encode the fail-closed gates as weighted terms.
_OUT_OF_RANGE_PENALTY = -2.50  # in_act_range == -1 (known act, invalid number)
_UNKNOWN_ACT_PENALTY = -2.50  # in_act_range == 0  (unknown act — never guess)
_L4_LINE_START_PENALTY = -0.20  # mid-line L4 with no em-dash title
#: A mid-line ``Section N`` mention is almost always a cross-reference in running
#: text, not a provision header (the E2 failure mode).  Only a line-start word
#: form earns the positive source weight.
_WORD_MIDLINE_PENALTY = -0.50


def _sigmoid(score: float) -> float:
    if score >= 0:
        z = math.exp(-score)
        return 1.0 / (1.0 + z)
    z = math.exp(score)
    return z / (1.0 + z)


def rules_probability(features: dict[str, float]) -> float:
    """P(boundary) under the deterministic Tier-1 feature weights."""
    in_range = features.get("in_act_range", 0.0)
    score = 0.0
    if in_range > 0:
        score += _RULE_WEIGHTS["in_act_range"]
    elif in_range < 0:
        score += _OUT_OF_RANGE_PENALTY
    else:
        score += _UNKNOWN_ACT_PENALTY

    if features.get("source_l4_header"):
        score += _RULE_WEIGHTS["source_l4_header"]
        if not features.get("has_emdash_title") and not features.get("is_line_start"):
            score += _L4_LINE_START_PENALTY
    score += _RULE_WEIGHTS["source_engine_main"] * features.get("source_engine_main", 0.0)
    if features.get("source_engine_word"):
        score += _RULE_WEIGHTS["source_engine_word"] if features.get("is_line_start") else _WORD_MIDLINE_PENALTY
    score += _RULE_WEIGHTS["source_dotted_clause"] * features.get("source_dotted_clause", 0.0)
    score += _RULE_WEIGHTS["is_line_start"] * features.get("is_line_start", 0.0)
    score += _RULE_WEIGHTS["is_first_occurrence"] * features.get("is_first_occurrence", 0.0)
    score += _RULE_WEIGHTS["title_score"] * features.get("title_score", 0.0)
    score += _RULE_WEIGHTS["has_emdash_title"] * features.get("has_emdash_title", 0.0)
    score += _RULE_WEIGHTS["is_monotonic"] * features.get("is_monotonic", 0.0)
    score += _RULE_WEIGHTS["page_number_only"] * features.get("page_number_only", 0.0)
    score += _RULE_WEIGHTS["is_year_like"] * features.get("is_year_like", 0.0)
    score += _RULE_WEIGHTS["crossref_density"] * min(features.get("crossref_density", 0.0), 3.0)
    return _sigmoid(score)


class BoundaryDecision:
    """Outcome of one disambiguation: accept/reject + tier + probability."""

    __slots__ = ("accepted", "probability", "reason", "tier")

    def __init__(self, accepted: bool, probability: float, tier: str, reason: str = "") -> None:
        self.accepted = accepted
        self.probability = probability
        self.tier = tier
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"BoundaryDecision(accepted={self.accepted}, probability={self.probability:.4f}, "
            f"tier={self.tier!r}, reason={self.reason!r})"
        )


def hard_veto(features: dict[str, float]) -> str | None:
    """Deterministic fail-closed gate applied in EVERY tier (ADR-0009).

    A year token, an out-of-range number, or an unknown act must never be
    accepted just because a learned model is confident — the ML only
    disambiguates *within* the admissible set.  Returns a rejection reason or
    ``None`` when the candidate is admissible.
    """
    if features.get("is_year_like"):
        return "year_like"
    if float(features.get("in_act_range", 0.0)) <= 0.0:
        return "out_of_range_or_unknown_act"
    return None


class Disambiguator:
    """Accept/reject provision boundary candidates.

    Args:
        mode: ``"rules"``, ``"hybrid"`` or ``"llm_fallback"``.  Unknown values
            degrade to ``"rules"``.
        model_path: Path to the joblib artifact (hybrid mode only).
        min_confidence: Acceptance threshold; falls back to the
            ``PROVISION_EXTRACTOR_MIN_CONFIDENCE`` config value.

    """

    def __init__(
        self,
        mode: str = "rules",
        model_path: str | None = None,
        min_confidence: float | None = None,
        ml_threshold: float | None = None,
    ) -> None:
        self._mode = mode if mode in ("rules", "hybrid", "llm_fallback") else "rules"
        if model_path is None:
            try:
                from app.shared.config import cfg

                model_path = cfg.provision_extractor_model_path
            except Exception:  # pragma: no cover - config seam unavailable
                model_path = DEFAULT_MODEL_PATH
        self._model_path = model_path or DEFAULT_MODEL_PATH
        if min_confidence is None:
            try:
                from app.shared.config import cfg

                min_confidence = cfg.provision_extractor_min_confidence
            except Exception:  # pragma: no cover - config seam unavailable
                min_confidence = 0.70
        self._min_confidence = float(min_confidence)
        if ml_threshold is None:
            try:
                from app.shared.config import cfg

                ml_threshold = cfg.provision_extractor_ml_threshold
            except Exception:  # pragma: no cover - config seam unavailable
                ml_threshold = DEFAULT_ML_THRESHOLD
        self._ml_threshold = float(ml_threshold)
        self._model = self._load_model()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def uses_model(self) -> bool:
        """True when a Tier-2 artifact loaded successfully."""
        return self._model is not None

    # ------------------------------------------------------------------ #
    # Model loading (lazy, fail-closed)
    # ------------------------------------------------------------------ #

    def _load_model(self) -> object | None:
        """Load the Tier-2 artifact lazily; return ``None`` on any failure."""
        if self._mode != "hybrid":
            return None
        try:
            import joblib
            import sklearn  # noqa: F401  # lazy optional dependency
        except Exception as exc:  # ImportError, or any import-time failure
            logger.warning("Tier 2 ML unavailable (%s); using rules fallback", exc)
            return None
        if not self._model_path or not os.path.exists(self._model_path):
            logger.warning("Tier 2 model artifact %r missing; using rules fallback", self._model_path)
            return None
        try:
            return joblib.load(self._model_path)
        except Exception as exc:
            logger.warning("Tier 2 model artifact %r failed to load (%s); using rules fallback", self._model_path, exc)
            return None

    # ------------------------------------------------------------------ #
    # Prediction
    # ------------------------------------------------------------------ #

    def predict(self, features: dict[str, float]) -> BoundaryDecision:
        """Decide whether *features* describe an accepted provision boundary."""
        veto = hard_veto(features)
        if veto is not None:
            return BoundaryDecision(False, 0.0, TIER_RULES, reason=veto)

        if self._model is not None:
            try:
                vector = feature_vector(features)
                probability = float(self._model.predict_proba([vector])[0][1])
                return BoundaryDecision(probability >= self._ml_threshold, probability, TIER_ML)
            except Exception as exc:
                logger.warning("Tier 2 predict failed (%s); rules fallback", exc)

        probability = rules_probability(features)
        return BoundaryDecision(probability >= self._min_confidence, probability, TIER_RULES)

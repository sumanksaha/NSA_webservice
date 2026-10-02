"""Tiered statutory provision extraction engine (ADR-0009).

Pipeline::

    candidates → features → disambiguator → isolator → ProvisionRecord

P0 ships the Tier-1 deterministic fast path (rules-mode disambiguator).  The
Tier-2 scikit-learn artifact and the Tier-3 LLM fallback plug into the same
:class:`Disambiguator` interface without changing callers.

Public API:

* :class:`ProvisionRecord`, :class:`DeonticModality` — the data contract.
* :func:`generate_candidates` — grammar-aware boundary proposals.
* :func:`extract_features`, :func:`feature_vector` — Tier-2 feature contract.
* :class:`Disambiguator` — rules / hybrid accept-reject decisions.
* :func:`isolate_chunks` — end-to-end extraction for one document.
* :func:`family_for_title`, :func:`provision_id` — gold-family id construction.
"""

from __future__ import annotations

from app.rag.provision_extractor.adapter import ProvisionExtractorAdapter
from app.rag.provision_extractor.candidates import BoundaryCandidate, generate_candidates
from app.rag.provision_extractor.disambiguator import BoundaryDecision, Disambiguator, hard_veto, rules_probability
from app.rag.provision_extractor.features import FEATURE_NAMES, extract_features, feature_vector
from app.rag.provision_extractor.isolator import attach_provision_spans, classify_modality, isolate_chunks
from app.rag.provision_extractor.models import (
    TIER_LLM,
    TIER_ML,
    TIER_RULES,
    DeonticModality,
    ProvisionRecord,
)
from app.rag.provision_extractor.registry import family_for_title, family_token, provision_id

__all__ = [
    "FEATURE_NAMES",
    "TIER_LLM",
    "TIER_ML",
    "TIER_RULES",
    "BoundaryCandidate",
    "BoundaryDecision",
    "DeonticModality",
    "Disambiguator",
    "ProvisionExtractorAdapter",
    "ProvisionRecord",
    "attach_provision_spans",
    "classify_modality",
    "extract_features",
    "family_for_title",
    "family_token",
    "feature_vector",
    "generate_candidates",
    "hard_veto",
    "isolate_chunks",
    "provision_id",
    "rules_probability",
]

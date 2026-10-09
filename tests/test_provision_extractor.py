"""Tests for the P0 rules-mode statutory provision extractor (ADR-0009).

Covers candidate generation, feature extraction, the Tier-1 rules
disambiguator (including the lazy Tier-2 fallback), the isolator end-to-end,
the family registry / gold-grammar ids, and the new ``PROVISION_EXTRACTOR_*``
config flags.  No optional ML dependency is required — that is the point.
"""

from __future__ import annotations

import builtins

import pytest

from app.rag.provision_extractor import (
    FEATURE_NAMES,
    DeonticModality,
    Disambiguator,
    extract_features,
    family_for_title,
    feature_vector,
    generate_candidates,
    isolate_chunks,
    provision_id,
    rules_probability,
)
from app.rag.provision_extractor.models import TIER_RULES

FSS_ACT = "Food Safety and Standards Act, 2006"

_CLEAN_FEATURES = {
    "is_line_start": 1.0,
    "in_act_range": 1.0,
    "source_engine_main": 1.0,
    "source_engine_word": 0.0,
    "source_dotted_clause": 0.0,
    "source_l4_header": 0.0,
    "is_dotted": 0.0,
    "number_value": 26.0,
    "is_year_like": 0.0,
    "delta_from_prev_accepted": -1.0,
    "is_monotonic": 0.0,
    "title_score": 0.33,
    "has_emdash_title": 1.0,
    "crossref_density": 0.0,
    "page_number_only": 0.0,
    "is_first_occurrence": 1.0,
    "sections_covered_agree": 0.0,
    "engine_confidence": 0.0,
}


# --------------------------------------------------------------------------- #
# Candidates
# --------------------------------------------------------------------------- #


class TestCandidates:
    def test_line_start_header_is_proposed(self):
        cands = generate_candidates("26. Responsibilities of the food business operator.")
        assert len(cands) == 1
        assert cands[0].raw_number == "26"
        assert cands[0].source_pattern == "engine_main"
        assert cands[0].grammar_type == "section"
        assert cands[0].char_offset == 0

    def test_offset_collision_prefers_engine_main_over_l4(self):
        # Both the line-start rule and the any-position L4 rule can match "26." —
        # exactly one candidate must survive, the more specific one.
        cands = generate_candidates("26. Responsibilities of the operator.")
        assert [c.source_pattern for c in cands] == ["engine_main"]

    def test_explicit_section_word_is_proposed(self):
        cands = generate_candidates("See Section 55 of the Act for penalties.")
        assert any(c.raw_number == "55" and c.source_pattern == "engine_word" for c in cands)

    def test_dotted_regulation_clause_is_proposed(self):
        cands = generate_candidates("2.4.15 Hygiene requirements shall be met.")
        assert any(c.raw_number == "2.4.15" and c.grammar_type == "dotted" for c in cands)

    def test_no_candidates_for_plain_prose(self):
        assert generate_candidates("The officer inspected the premises yesterday.") == []


# --------------------------------------------------------------------------- #
# Features
# --------------------------------------------------------------------------- #


class TestFeatures:
    def _first(self, text: str, act: str):
        candidate = generate_candidates(text)[0]
        return extract_features(text, candidate, act_name=act)

    def test_in_range_for_known_act(self):
        feats = self._first("26. Responsibilities of the operator.— shall ensure.", FSS_ACT)
        assert feats["in_act_range"] == 1.0
        assert feats["is_line_start"] == 1.0

    def test_unknown_act_is_fail_closed_neutral(self):
        feats = self._first("26. Responsibilities of the operator.— shall ensure.", "Mystery Widget Act, 2099")
        assert feats["in_act_range"] == 0.0

    def test_out_of_range_number(self):
        feats = self._first("999. Widget.— shall ensure compliance.", FSS_ACT)
        assert feats["in_act_range"] == -1.0

    def test_feature_vector_matches_stable_names(self):
        feats = self._first("26. Responsibilities of the operator.— shall ensure.", FSS_ACT)
        vector = feature_vector(feats)
        assert len(vector) == len(FEATURE_NAMES)
        assert all(isinstance(value, float) for value in vector)


# --------------------------------------------------------------------------- #
# Disambiguator — Tier 1 rules
# --------------------------------------------------------------------------- #


class TestRulesDisambiguator:
    def test_accepts_clean_line_start_boundary(self):
        decision = Disambiguator(mode="rules").predict(dict(_CLEAN_FEATURES))
        assert decision.accepted is True
        assert decision.tier == TIER_RULES
        assert decision.probability >= 0.70

    def test_rejects_unknown_act(self):
        feats = dict(_CLEAN_FEATURES, in_act_range=0.0)
        assert Disambiguator(mode="rules").predict(feats).accepted is False

    def test_rejects_out_of_range(self):
        feats = dict(_CLEAN_FEATURES, in_act_range=-1.0)
        assert Disambiguator(mode="rules").predict(feats).accepted is False

    def test_rejects_year_like_number(self):
        feats = dict(_CLEAN_FEATURES, is_year_like=1.0)
        assert Disambiguator(mode="rules").predict(feats).accepted is False

    def test_probability_is_bounded(self):
        assert rules_probability({}) == pytest.approx(rules_probability({}), abs=1e-9)
        assert 0.0 <= rules_probability({}) <= 1.0


# --------------------------------------------------------------------------- #
# Disambiguator — Tier 2 ML fallback
# --------------------------------------------------------------------------- #


class TestHybridFallback:
    def test_hybrid_without_artifact_falls_back_to_rules(self):
        engine = Disambiguator(mode="hybrid", model_path="models/does_not_exist.joblib")
        assert engine.uses_model is False
        decision = engine.predict(dict(_CLEAN_FEATURES))
        assert decision.accepted is True
        assert decision.tier == TIER_RULES

    def test_rules_fallback_when_optional_imports_fail(self, monkeypatch):
        real_import = builtins.__import__

        def _boom(name, *args, **kwargs):
            if name in ("sklearn", "joblib"):
                raise ImportError(f"{name} unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _boom)
        engine = Disambiguator(mode="hybrid", model_path="models/provision_boundaries.joblib")
        assert engine.uses_model is False
        assert engine.predict(dict(_CLEAN_FEATURES)).tier == TIER_RULES


# --------------------------------------------------------------------------- #
# Isolator — end-to-end
# --------------------------------------------------------------------------- #


_FSS_DOC = [
    {
        "chunk_id": "c0",
        "chunk_index": 0,
        "chunk_text": (
            "Food Safety and Standards Act, 2006\n\n"
            "26. Responsibilities of the food business operator.— Every food business operator "
            "shall ensure that the articles of food satisfy the requirements of this Act."
        ),
    },
    {
        "chunk_id": "c1",
        "chunk_index": 1,
        "chunk_text": (
            "31. Licensing and registration.— No person shall commence any food business except under a licence."
        ),
    },
    {
        "chunk_id": "c2",
        "chunk_index": 2,
        "chunk_text": (
            "50. Penalty for selling food not of the nature or substance or quality demanded.— "
            "Any person who sells shall be liable to a penalty.\n"
            "Provided that nothing in this section shall apply to a person who is not a "
            "food business operator."
        ),
    },
]


class TestIsolator:
    def test_emits_records_in_document_order(self):
        records = isolate_chunks(_FSS_DOC, act_name=FSS_ACT, document_title=FSS_ACT)
        assert [r.provision_id for r in records] == ["fssai:s26", "fssai:s31", "fssai:s50"]

    def test_preserves_source_chunk_ids(self):
        records = isolate_chunks(_FSS_DOC, act_name=FSS_ACT, document_title=FSS_ACT)
        assert records[0].source_chunk_ids == ["c0"]
        assert records[1].source_chunk_ids == ["c1"]
        assert records[2].source_chunk_ids == ["c2"]

    def test_classifies_modality(self):
        records = isolate_chunks(_FSS_DOC, act_name=FSS_ACT, document_title=FSS_ACT)
        by_id = {r.provision_id: r for r in records}
        assert by_id["fssai:s26"].modality == DeonticModality.OBLIGATION
        assert by_id["fssai:s31"].modality == DeonticModality.PROHIBITION
        assert by_id["fssai:s50"].modality == DeonticModality.PENALTY
        assert by_id["fssai:s50"].is_proviso is True

    def test_subsection_id_grammar(self):
        chunks = [
            {
                "chunk_id": "c0",
                "chunk_index": 0,
                "chunk_text": "Section 31(2) Licensing.— A licence shall be issued by the Authority.",
            },
        ]
        records = isolate_chunks(chunks, act_name=FSS_ACT, document_title=FSS_ACT)
        assert len(records) == 1
        assert records[0].provision_id == "fssai:s31(2)"
        assert records[0].subsection == ["2"]

    def test_empty_document_yields_no_records(self):
        assert isolate_chunks([], act_name=FSS_ACT) == []

    def test_unknown_act_fails_closed(self):
        records = isolate_chunks(_FSS_DOC, act_name="Mystery Widget Act, 2099", document_title="Mystery Widget Act")
        assert records == []


# --------------------------------------------------------------------------- #
# Registry / id grammar
# --------------------------------------------------------------------------- #


class TestRegistry:
    def test_family_for_known_instrument(self):
        assert family_for_title(FSS_ACT) == "fssai"
        assert family_for_title(None, FSS_ACT) == "fssai"

    def test_family_for_unknown_instrument_is_none(self):
        assert family_for_title("Some Entirely Unrelated Act, 1999") is None

    def test_provision_id_gold_grammar(self):
        assert provision_id("fssai", "31", []) == "fssai:s31"
        assert provision_id("fssai", "31", ["2"]) == "fssai:s31(2)"
        assert provision_id("fssai", "31", ["2", "a"]) == "fssai:s31(2)(a)"


# --------------------------------------------------------------------------- #
# Config flags
# --------------------------------------------------------------------------- #


class TestConfigFlags:
    def test_defaults(self, monkeypatch):
        from app.shared.config import cfg

        monkeypatch.delenv("PROVISION_EXTRACTOR_ENABLED", raising=False)
        monkeypatch.delenv("PROVISION_EXTRACTOR_MODE", raising=False)
        monkeypatch.delenv("PROVISION_EXTRACTOR_MIN_CONFIDENCE", raising=False)
        monkeypatch.delenv("PROVISION_EXTRACTOR_ML_THRESHOLD", raising=False)
        assert cfg.provision_extractor_enabled is True
        assert cfg.provision_extractor_mode == "rules"
        assert cfg.provision_extractor_model_path == "models/provision_boundaries.joblib"
        assert cfg.provision_extractor_min_confidence == 0.70
        assert cfg.provision_extractor_ml_threshold == 0.25
        assert cfg.provision_extractor_review_threshold == 0.30

    def test_env_override(self, monkeypatch):
        from app.shared.config import cfg

        monkeypatch.setenv("PROVISION_EXTRACTOR_MODE", "hybrid")
        monkeypatch.setenv("PROVISION_EXTRACTOR_ENABLED", "false")
        assert cfg.provision_extractor_mode == "hybrid"
        assert cfg.provision_extractor_enabled is False

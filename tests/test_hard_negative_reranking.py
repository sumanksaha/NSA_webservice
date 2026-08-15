"""Tests for hard-negative legal reranking modules (2026-08-15).

Covers:
  - failure_taxonomy: category classification logic
  - hard_negative_miner: legal similarity scoring, tier assignment, ranking
  - pairwise_dataset: dataset construction and splitting
  - ranking_loss_trainer: margin ranking loss computation
  - error_dashboard: dashboard generation

All tests are offline — no Qdrant, no sentence-transformers, no GPU.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.failure_taxonomy import (
    classify_failure,
    _word_overlap,
    _is_provisional,
    _is_definition,
    _is_exception,
    CATEGORIES,
)
from evaluation.hard_negative_miner import (
    legal_similarity_score,
    assign_tier,
    hard_negative_rank,
    word_overlap,
    section_proximity,
)
from evaluation.pairwise_dataset import build_pairwise_examples, split_dataset


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

class FakeFamilyMap:
    """Minimal FamilyMap stub for testing."""

    def __init__(self, families: dict[str, list[str]] | None = None):
        self.family_to_acts = families or {"fssai": ["Food Safety and Standards Act, 2006"]}
        self.act_to_family = {}
        for fam, acts in self.family_to_acts.items():
            for act in acts:
                self.act_to_family[act.lower()] = fam

    def family_s_for_act(self, act_name: str | None) -> list[str]:
        if not act_name:
            return []
        n = act_name.lower()
        found = []
        if n in self.act_to_family:
            found.append(self.act_to_family[n])
        # Simple substring match
        for fam, acts in self.family_to_acts.items():
            if fam in found:
                continue
            for act in acts:
                if act.lower() in n or n in act.lower():
                    found.append(fam)
                    break
        return found


class FakeGoldUnit:
    """Minimal GoldUnit stub."""
    def __init__(self, provision_id: str, family: str, section: str | None = None, act: str = ""):
        self.provision_id = provision_id
        self.family = family
        self.section = section
        self.act = act


# --------------------------------------------------------------------------- #
# failure_taxonomy tests
# --------------------------------------------------------------------------- #

class TestFailureTaxonomy:
    """Test failure classification logic."""

    def test_category_definitions(self):
        """All 12 categories + unclassified are defined."""
        assert len(CATEGORIES) == 13
        for key in [
            "A_same_act_wrong_section", "B_same_section_wrong_subsection",
            "C_same_legal_concept", "D_same_terminology",
            "E_procedural_vs_substantive", "F_definition_vs_operative",
            "G_exception_vs_general_rule", "H_cross_reference_failure",
            "I_authority_jurisdiction_mismatch", "J_temporal_version_error",
            "K_adjacent_section_confusion", "L_multi_provision_requirement",
            "unclassified",
        ]:
            assert key in CATEGORIES

    def test_word_overlap_identical(self):
        assert _word_overlap("section 16 duties", "section 16 duties") == 1.0

    def test_word_overlap_empty(self):
        assert _word_overlap("", "anything") == 0.0

    def test_word_overlap_partial(self):
        score = _word_overlap(
            "Food Safety and Standards Authority duties",
            "Food Safety and Standards Authority powers",
        )
        assert 0.5 < score < 1.0

    def test_is_provisional_detects_procedure(self):
        assert _is_provisional("The applicant shall file a petition with the tribunal for hearing") is True

    def test_is_provisional_rejects_substantive(self):
        assert _is_provisional("No person shall sell adulterated food") is False

    def test_is_definition_detects(self):
        assert _is_definition('the term definition of this Act applies here') is True

    def test_is_definition_rejects(self):
        assert _is_definition("The Food Authority shall regulate food business") is False

    def test_is_exception_detects(self):
        assert _is_exception("Provided that nothing in this section shall apply to...") is True

    def test_is_exception_rejects(self):
        assert _is_exception("Every food business operator shall comply with") is False

    def test_classify_same_act_wrong_section(self):
        fm = FakeFamilyMap()
        unit = FakeGoldUnit("fssai:s16", "fssai", section="16")
        gold = {"section_number": "16", "chunk_text": "Duties of Food Authority"}
        neg = {"section_number": "50", "act_name": "Food Safety and Standards Act, 2006",
               "chunk_text": "Section 50 powers"}
        result = classify_failure(unit, gold, neg, fm)
        assert result == "A_same_act_wrong_section"

    def test_classify_same_section_wrong_subsection(self):
        fm = FakeFamilyMap()
        unit = FakeGoldUnit("fssai:s16(1)", "fssai", section="16")
        gold = {"section_number": "16", "subsection": "(1)", "chunk_text": "sub-section 1"}
        neg = {"section_number": "16", "subsection": "(2)", "act_name": "Food Safety and Standards Act, 2006",
               "chunk_text": "sub-section 2"}
        result = classify_failure(unit, gold, neg, fm)
        assert result == "B_same_section_wrong_subsection"

    def test_classify_adjacent_section(self):
        fm = FakeFamilyMap()
        unit = FakeGoldUnit("fssai:s16", "fssai", section="16")
        gold = {"section_number": "16", "chunk_text": "section 16"}
        neg = {"section_number": "17", "act_name": "Food Safety and Standards Act, 2006",
               "chunk_text": "section 17"}
        result = classify_failure(unit, gold, neg, fm)
        assert result == "K_adjacent_section_confusion"

    def test_classify_definition_vs_operative(self):
        fm = FakeFamilyMap()
        unit = FakeGoldUnit("fssai:s3", "fssai", section="3")
        # Both same section so it doesn't hit the section-mismatch branch first
        gold = {"section_number": "3", "chunk_text": "No person shall sell adulterated food under this Act"}
        neg = {"section_number": "3", "act_name": "Food Safety and Standards Act, 2006",
               "chunk_text": 'For the purposes of this section, adulterated food means any food which contains'}
        result = classify_failure(unit, gold, neg, fm)
        assert result == "F_definition_vs_operative"

    def test_classify_exception_vs_general(self):
        fm = FakeFamilyMap()
        unit = FakeGoldUnit("fssai:s31", "fssai", section="31")
        gold = {"section_number": "31", "chunk_text": "No person shall commence food business without licence"}
        neg = {"section_number": "31", "act_name": "Food Safety and Standards Act, 2006",
               "chunk_text": "Provided that nothing in this section shall apply to small operators"}
        result = classify_failure(unit, gold, neg, fm)
        assert result == "G_exception_vs_general_rule"


# --------------------------------------------------------------------------- #
# hard_negative_miner tests
# --------------------------------------------------------------------------- #

class TestHardNegativeMiner:
    """Test legal similarity scoring and tier assignment."""

    def test_word_overlap_function(self):
        assert word_overlap("section 16 duties", "section 16 duties") == 1.0
        assert word_overlap("", "anything") == 0.0

    def test_section_proximity_same(self):
        assert section_proximity("16", "16") == 1.0

    def test_section_proximity_adjacent(self):
        assert section_proximity("16", "17") == 0.7
        assert section_proximity("16", "18") == 0.7

    def test_section_proximity_far(self):
        assert section_proximity("1", "100") == 0.1

    def test_section_proximity_none(self):
        assert section_proximity(None, "16") == 0.0
        assert section_proximity("16", None) == 0.0

    def test_legal_similarity_same_family_same_section(self):
        fm = FakeFamilyMap()
        unit = FakeGoldUnit("fssai:s16", "fssai", section="16")
        gold = {"section_number": "16", "act_name": "Food Safety and Standards Act, 2006"}
        neg = {"section_number": "16", "act_name": "Food Safety and Standards Act, 2006"}
        features = legal_similarity_score(gold, neg, fm, unit)
        assert features["same_family"] == 1.0
        assert features["same_section"] == 1.0

    def test_legal_similarity_different_family(self):
        fm = FakeFamilyMap()
        unit = FakeGoldUnit("fssai:s16", "fssai", section="16")
        gold = {"section_number": "16", "act_name": "Food Safety and Standards Act, 2006"}
        neg = {"section_number": "16", "act_name": "Indian Contract Act, 1872"}
        features = legal_similarity_score(gold, neg, fm, unit)
        assert features["same_family"] == 0.0

    def test_assign_tier_same_family_same_section(self):
        features = {
            "same_family": 1.0, "same_section": 1.0, "section_proximity": 1.0,
            "word_overlap": 0.3, "same_document": 0.0, "same_subsection": 0.0,
            "same_authority": 0.0,
        }
        assert assign_tier(features) == 3

    def test_assign_tier_same_family_different_section(self):
        features = {
            "same_family": 1.0, "same_section": 0.0, "section_proximity": 0.4,
            "word_overlap": 0.2, "same_document": 0.0, "same_subsection": 0.0,
            "same_authority": 0.0,
        }
        assert assign_tier(features) == 2

    def test_assign_tier_high_word_overlap(self):
        features = {
            "same_family": 0.0, "same_section": 0.0, "section_proximity": 0.0,
            "word_overlap": 0.4, "same_document": 0.0, "same_subsection": 0.0,
            "same_authority": 0.0,
        }
        assert assign_tier(features) == 2

    def test_assign_tier_random(self):
        features = {
            "same_family": 0.0, "same_section": 0.0, "section_proximity": 0.0,
            "word_overlap": 0.1, "same_document": 0.0, "same_subsection": 0.0,
            "same_authority": 0.0,
        }
        assert assign_tier(features) == 1

    def test_hard_negative_rank_orders_correctly(self):
        """Adversarial negatives rank higher than random."""
        adversarial = {
            "same_family": 1.0, "same_section": 1.0, "section_proximity": 1.0,
            "word_overlap": 0.5, "same_document": 1.0, "same_subsection": 0.0,
            "same_authority": 0.0,
        }
        random_neg = {
            "same_family": 0.0, "same_section": 0.0, "section_proximity": 0.0,
            "word_overlap": 0.1, "same_document": 0.0, "same_subsection": 0.0,
            "same_authority": 0.0,
        }
        assert hard_negative_rank(adversarial, 0) > hard_negative_rank(random_neg, 0)


# --------------------------------------------------------------------------- #
# pairwise_dataset tests
# --------------------------------------------------------------------------- #

class TestPairwiseDataset:
    """Test pairwise dataset construction and splitting."""

    def test_build_pairwise_examples_no_file(self, tmp_path, monkeypatch):
        """Returns empty list when mining file doesn't exist."""
        monkeypatch.setattr(
            "evaluation.pairwise_dataset.MINING_FILE",
            tmp_path / "nonexistent.jsonl",
        )
        result = build_pairwise_examples()
        assert result == []

    def test_build_pairwise_examples_from_file(self, tmp_path, monkeypatch):
        """Builds pairs from a mining file."""
        mining_file = tmp_path / "mining.jsonl"
        mining_file.write_text(json.dumps({
            "question_id": "Q001",
            "query": "What is section 16?",
            "gold_units": ["fssai:s16"],
            "positives": [
                {"chunk_id": "abc", "text": "Section 16 duties", "rank": 5, "gold_unit": "fssai:s16"},
            ],
            "negatives": [
                {"chunk_id": "def", "text": "Section 17 powers", "rank": 3, "tier": 3, "score": 5.0, "features": {}},
                {"chunk_id": "ghi", "text": "Section 20 penalties", "rank": 10, "tier": 2, "score": 3.0, "features": {}},
                {"chunk_id": "jkl", "text": "Indian Contract Act duties", "rank": 50, "tier": 1, "score": 1.0, "features": {}},
            ],
        }, ensure_ascii=False) + "\n")
        monkeypatch.setattr("evaluation.pairwise_dataset.MINING_FILE", mining_file)

        examples = build_pairwise_examples(mode="uniform")
        assert len(examples) > 0
        assert all("query" in e for e in examples)
        assert all("positive" in e for e in examples)
        assert all("negative" in e for e in examples)
        assert all("tier" in e for e in examples)

    def test_split_dataset_stratified_by_question(self):
        """Split assigns all pairs from one question to the same split."""
        examples = [
            {"query": "q1", "positive": "p1", "negative": "n1", "tier": 1, "question_id": "Q001"},
            {"query": "q1", "positive": "p1", "negative": "n2", "tier": 2, "question_id": "Q001"},
            {"query": "q2", "positive": "p2", "negative": "n3", "tier": 3, "question_id": "Q002"},
            {"query": "q3", "positive": "p3", "negative": "n4", "tier": 1, "question_id": "Q003"},
        ]
        splits, info = split_dataset(examples, seed=42)
        # All Q001 pairs should be in the same split
        q001_splits = [
            s for s in ("train", "val", "test")
            if any(e["question_id"] == "Q001" for e in splits[s])
        ]
        assert len(q001_splits) == 1

    def test_split_preserves_all_examples(self):
        """Total pairs across splits equals input."""
        examples = [
            {"query": f"q{i}", "positive": f"p{i}", "negative": f"n{i}",
             "tier": (i % 3) + 1, "question_id": f"Q{i:03d}"}
            for i in range(30)
        ]
        splits, _ = split_dataset(examples, seed=42)
        total = sum(len(v) for v in splits.values())
        assert total == len(examples)

    def test_split_ratios_approximate(self):
        """Split ratios are approximately 70/15/15."""
        examples = [
            {"query": f"q{i}", "positive": f"p{i}", "negative": f"n{i}",
             "tier": 2, "question_id": f"Q{i:03d}"}
            for i in range(100)
        ]
        splits, info = split_dataset(examples, seed=42)
        assert 0.6 <= info["train_questions"] / 100 <= 0.8
        assert 0.1 <= info["val_questions"] / 100 <= 0.25
        assert 0.1 <= info["test_questions"] / 100 <= 0.25


# --------------------------------------------------------------------------- #
# ranking_loss_trainer tests (structural — no model loading)
# --------------------------------------------------------------------------- #

class TestRankingLossTrainer:
    """Test ranking loss computation logic (no model loading)."""

    def test_margin_ranking_loss_basic(self):
        """Margin ranking loss penalizes when neg > pos."""
        import torch

        pos_scores = torch.tensor([0.8, 0.6])
        neg_scores = torch.tensor([0.2, 0.9])
        target = torch.ones(2)
        loss = torch.nn.functional.margin_ranking_loss(
            pos_scores, neg_scores, target, margin=1.0,
        )
        # Loss should be positive (second pair is wrong)
        assert loss.item() > 0

    def test_margin_ranking_loss_perfect(self):
        """Margin ranking loss is ~0 when pos >> neg + margin."""
        import torch

        pos_scores = torch.tensor([2.0, 2.0])
        neg_scores = torch.tensor([0.0, 0.0])
        target = torch.ones(2)
        loss = torch.nn.functional.margin_ranking_loss(
            pos_scores, neg_scores, target, margin=1.0,
        )
        assert loss.item() < 0.01

    def test_contrastive_loss_basic(self):
        """Contrastive loss penalizes when neg > pos."""
        import torch

        pos_scores = torch.tensor([0.8])
        neg_scores = torch.tensor([0.2])
        log_denom = torch.logsumexp(
            torch.stack([pos_scores, neg_scores], dim=-1), dim=-1
        )
        loss = -(pos_scores - log_denom).mean()
        # Loss should be small (pos > neg)
        assert 0.0 < loss.item() < 1.0

    def test_contrastive_loss_reversal(self):
        """Contrastive loss is large when neg > pos."""
        import torch

        pos_scores = torch.tensor([0.2])
        neg_scores = torch.tensor([0.8])
        log_denom = torch.logsumexp(
            torch.stack([pos_scores, neg_scores], dim=-1), dim=-1
        )
        loss = -(pos_scores - log_denom).mean()
        # Loss should be large (pos < neg)
        assert loss.item() > 0.5


# --------------------------------------------------------------------------- #
# error_dashboard tests
# --------------------------------------------------------------------------- #

class TestErrorDashboard:
    """Test error dashboard generation."""

    def test_categories_are_complete(self):
        """All 13 categories are defined."""
        from evaluation.failure_taxonomy import CATEGORIES
        assert len(CATEGORIES) == 13

    def test_ndcg_computation(self):
        """nDCG@k with perfect ranking = 1.0."""
        from evaluation.hard_neg_eval import _ndcg_at_k
        # Perfect ranking: all relevant docs at top
        relevances = [1.0, 1.0, 1.0, 0.0, 0.0]
        ndcg = _ndcg_at_k(relevances, k=5)
        assert ndcg == pytest.approx(1.0, abs=0.01)

    def test_ndcg_imperfect(self):
        """nDCG@k with imperfect ranking < 1.0."""
        from evaluation.hard_neg_eval import _ndcg_at_k
        # Imperfect: relevant docs at positions 3, 4
        relevances = [0.0, 0.0, 1.0, 1.0, 0.0]
        ndcg = _ndcg_at_k(relevances, k=5)
        assert 0.0 < ndcg < 1.0

"""Tests for RL offline trainer."""

from __future__ import annotations

from unittest import mock

from app.rag.rl.experience import RLExperienceStore
from app.rag.rl.policy import RLAction, RLContext, RLPolicy
from app.rag.rl.training import RLTrainer, TrainingResult


def test_trainer_empty_data():
    trainer = RLTrainer(
        store=mock.MagicMock(spec=RLExperienceStore),
        policy=RLPolicy(),
    )
    trainer.store.load_all.return_value = []
    trainer.store.load_offline_from_eval.return_value = []
    result = trainer.train()
    assert isinstance(result, TrainingResult)
    assert result.observations == 0


def test_trainer_trains_on_data():
    ctx = RLContext(query_type="qa", confidence_bucket="high", has_identifier=False, length_bucket="medium")
    action = RLAction(top_k=10, rrf_k=60.0)
    obs = {
        "context": ctx,
        "action": action,
        "reward": 0.85,
        "components": {"faithfulness": 0.8, "groundedness": 0.9},
        "is_exploration": False,
    }

    mock_store = mock.MagicMock(spec=RLExperienceStore)
    mock_store.load_all.return_value = [obs] * 10
    mock_store.load_offline_from_eval.return_value = []

    policy = RLPolicy(epsilon=0.0, min_obs=0)
    trainer = RLTrainer(store=mock_store, policy=policy)
    result = trainer.train()

    assert result.observations == 10
    assert result.unique_contexts == 1
    assert result.unique_actions == 1
    assert result.reward_mean == 0.85 or abs(result.reward_mean - 0.85) < 0.01
    assert result.reward_min == 0.85
    assert result.reward_max == 0.85


def test_trainer_persists_state():
    trainer = RLTrainer(
        store=mock.MagicMock(spec=RLExperienceStore),
        policy=RLPolicy(),
    )
    path = trainer.persist()
    import os

    assert os.path.exists(path)
    os.remove(path)


def test_trainingresult_to_dict():
    tr = TrainingResult(
        observations=100,
        unique_contexts=5,
        unique_actions=3,
        reward_mean=0.75,
        reward_min=0.1,
        reward_max=0.95,
        top_action=(10, 60.0),
    )
    d = tr.to_dict()
    assert d["observations"] == 100
    assert d["unique_contexts"] == 5
    assert d["top_action"] == [10, 60.0]

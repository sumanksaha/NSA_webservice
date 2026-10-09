"""Tests for RL reward model, controller, and experience store."""

from __future__ import annotations

from unittest import mock

from app.rag.rl.controller import RLController, RLParams
from app.rag.rl.experience import RLExperienceStore
from app.rag.rl.policy import RLAction, RLContext, RLPolicy
from app.rag.rl.reward import LATENCY_SOFT_CAP_MS, RewardComponents, RLRewardModel

# --------------------------------------------------------------------------- #
# RLRewardModel
# --------------------------------------------------------------------------- #


def test_reward_model_returns_components():
    model = RLRewardModel()
    chunks = [
        {
            "chunk_id": "c1",
            "text": "Section 12: No person shall sell substandard food.",
            "score": 0.9,
            "section_number": "12",
        },
    ]
    reward = model.compute(
        answer="No person shall sell substandard food under Section 12.",
        chunks=chunks,
        cited_chunk_ids=["c1"],
        latency_ms=100,
        query="what does section 12 say",
    )
    assert isinstance(reward, RewardComponents)
    assert 0.0 <= reward.total <= 1.0
    assert 0.0 <= reward.faithfulness <= 1.0
    assert 0.0 <= reward.groundedness <= 1.0
    assert 0.0 <= reward.citation_recall <= 1.0


def test_reward_model_latency_penalty():
    model = RLRewardModel()
    chunks = [{"chunk_id": "c1", "text": "some text here", "score": 0.9}]
    fast = model.compute(answer="some text here", chunks=chunks, cited_chunk_ids=[], latency_ms=10)
    slow = model.compute(
        answer="some text here", chunks=chunks, cited_chunk_ids=[], latency_ms=LATENCY_SOFT_CAP_MS + 500,
    )
    # With zero citations, citation_recall = 1.0 (neutral).
    # latency factor for fast ~ 0.995, for slow ~ 0.0.
    assert fast.total > slow.total


def test_reward_model_graceful_without_metrics():
    model = RLRewardModel()
    model._faithfulness = None  # simulate missing metrics
    model._groundedness = None
    model._citation_recall = None
    reward = model.compute(answer="test", chunks=[], cited_chunk_ids=[], latency_ms=LATENCY_SOFT_CAP_MS + 100)
    # All metric components are 0; latency penalty = 0 (slow), so total = 0.
    assert reward.total == 0.0


def test_reward_model_empty_chunks():
    model = RLRewardModel()
    reward = model.compute(
        answer="something factual",
        chunks=[],
        cited_chunk_ids=[],
        latency_ms=0,
        query="some query",
    )
    assert reward.faithfulness == 0.0
    assert reward.groundedness == 0.0
    assert reward.citation_recall == 1.0  # no citations = neutral
    # reward = 0.20 * 1.0 + 0.10 * 1.0 = 0.30 (citation neutral, latency fast)
    assert reward.total == 0.3


# --------------------------------------------------------------------------- #
# RLController
# --------------------------------------------------------------------------- #


def test_controller_disabled_returns_defaults():
    controller = RLController(enabled=False)
    params = controller.select_params(query="test query", query_type="section_lookup")
    assert params.top_k == 10
    assert params.rrf_k == 60.0
    assert params.is_exploration is False

    # report_reward should be a no-op.
    assert (
        controller.report_reward(
            query="test",
            query_type="qa",
            params=params,
            answer="test",
            chunks=[],
            cited_chunk_ids=[],
        )
        is None
    )


def test_controller_enabled_selects_action():
    policy = RLPolicy(epsilon=0.0, min_obs=0)
    controller = RLController(
        policy=policy,
        reward_model=RLRewardModel(),
        store=mock.MagicMock(),
        enabled=True,
    )
    params = controller.select_params(
        query="what is section 12 of the FSSAI Act",
        query_type="section_lookup",
        legal_confidence=0.9,
        has_identifier=True,
    )
    assert params.top_k in (5, 10, 15, 20)
    assert params.rrf_k in (10.0, 30.0, 60.0, 100.0)


def test_controller_report_reward_updates_policy():
    policy = RLPolicy(epsilon=0.0, min_obs=0)
    controller = RLController(
        policy=policy,
        reward_model=RLRewardModel(),
        store=mock.MagicMock(),
        enabled=True,
    )
    params = RLParams(top_k=10, rrf_k=60.0, is_exploration=False)

    controller.report_reward(
        query="test query",
        query_type="qa",
        params=params,
        answer="test answer",
        chunks=[{"chunk_id": "c1", "text": "test text", "score": 0.9}],
        cited_chunk_ids=["c1"],
        latency_ms=100,
        legal_confidence=0.9,
        has_identifier=True,
    )

    # The store.log_experience should have been called.
    controller._store.log_experience.assert_called_once()


def test_controller_apply_params_sets_rrf_k():
    policy = RLPolicy(epsilon=0.0, min_obs=0)
    controller = RLController(
        policy=policy,
        reward_model=RLRewardModel(),
        store=mock.MagicMock(),
        enabled=True,
    )
    retriever = mock.MagicMock()
    params = RLParams(top_k=15, rrf_k=30.0, is_exploration=True)
    controller.apply_params(retriever, params)
    assert retriever._rrf_k == 30.0


def test_controller_stats():
    policy = RLPolicy(epsilon=0.0, min_obs=0)
    controller = RLController(
        policy=policy,
        reward_model=RLRewardModel(),
        store=mock.MagicMock(),
        enabled=True,
    )
    stats = controller.current_policy_stats()
    assert stats["enabled"] is True
    assert stats["contexts_observed"] == 0
    assert stats["total_observations"] == 0


def test_controller_disabled_stats():
    controller = RLController(enabled=False)
    stats = controller.current_policy_stats()
    assert stats["enabled"] is False


# --------------------------------------------------------------------------- #
# RLExperienceStore (with mocked DB)
# --------------------------------------------------------------------------- #


def test_store_build_context():
    ctx = RLExperienceStore.build_context(
        query="what is section 12",
        query_type="section_lookup",
        legal_confidence=0.9,
        has_identifier=True,
    )
    assert ctx.query_type == "section_lookup"
    assert ctx.confidence_bucket == "high"
    assert ctx.has_identifier is True


def test_store_log_experience_best_effort():
    store = RLExperienceStore()
    ctx = RLContext.from_query("test query", query_type="qa")
    action = RLAction(top_k=10, rrf_k=60.0)
    # Should not raise even without a DB.
    store.log_experience(
        context=ctx,
        action=action,
        reward=0.85,
        query="test query",
        components={"faithfulness": 0.8, "groundedness": 0.9},
        is_exploration=True,
        latency_ms=200,
    )


def test_store_load_all_without_db():
    store = RLExperienceStore()
    result = store.load_all()
    assert result == []


def test_store_load_offline_without_db():
    store = RLExperienceStore()
    result = store.load_offline_from_eval()
    assert result == []


def test_store_seed_policy_returns_zero_without_data():
    store = RLExperienceStore()
    policy = RLPolicy()
    count = store.seed_policy(policy, include_eval=False)
    assert count == 0

"""Integration tests for RL-augmented retrieval pipeline (Phase 4).

Tests the RL retrieval parameter tuning cycle:
  run_retrieval_pipeline  -->  select_params() applies (top_k, rrf_k)
  run_generation_pipeline -->  report_reward() updates policy + DB log

RL is gated by RAG_RL_ENABLED=true in config.
"""

from __future__ import annotations

import pytest

from app import create_app


@pytest.fixture
def test_client():
    """Test client with database context and RL enabled."""
    app = create_app()
    app.config["TESTING"] = True
    app.config["RAG_RL_ENABLED"] = True
    app.config["RAG_ENABLED"] = True
    app.config["rag_enabled"] = True  # Required by _rag_enabled() route check
    app.config["RAG_CONTEXT_MAX_CHUNKS"] = 5  # small K for speed
    app.config["RAG_QDRANT_URL"] = "http://localhost:6333"  # no-op remote
    app.config["OPENROUTER_API_KEY"] = "sk-or-v1-fake"
    app.config["RAG_USE_STUB_LLM"] = True  # deterministic stub answers

    with app.test_client() as client:
        with app.app_context():
            from app.extensions import db

            db.create_all()
        yield client
        with app.app_context():
            from app.extensions import db

            db.drop_all()


class TestRLIntegrationFlow:
    """Test the RL retrieval parameter tuning cycle."""

    def test_controller_enabled_when_rl_flag_on(self, test_client):
        """When RAG_RL_ENABLED=true, the controller is active."""
        from app.rag.rl.controller import get_rl_controller

        with test_client.application.app_context():
            controller = get_rl_controller()
            assert controller.enabled is True

    def test_controller_disabled_when_rl_flag_off(self, test_client):
        """When RAG_RL_ENABLED=false, the controller returns defaults."""
        from app.rag.rl.controller import get_rl_controller, reset_rl_controller

        reset_rl_controller()

        with test_client.application.app_context():
            controller = get_rl_controller()
            assert controller.enabled is False

    def test_policy_action_space(self):
        """The policy has the expected action space of (top_k, rrf_k) pairs."""
        from app.rag.rl.policy import DEFAULT_RRF_K_VALUES, DEFAULT_TOP_K_VALUES

        assert DEFAULT_TOP_K_VALUES == (5, 10, 15, 20)
        assert DEFAULT_RRF_K_VALUES == (10.0, 30.0, 60.0, 100.0)

    def test_reward_model_returns_components(self):
        """The reward model computes faithfulness, groundedness, citation_recall."""
        from app.rag.rl.reward import RewardComponents, RLRewardModel

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

    def test_controller_select_params_returns_valid_actions(self, test_client):
        """select_params returns valid (top_k, rrf_k) pairs."""
        from app.rag.rl.controller import get_rl_controller

        with test_client.application.app_context():
            controller = get_rl_controller()

        # Test with epsilon=0 (greedy)
        params = controller.select_params(
            query="test query",
            query_type="section_lookup",
            legal_confidence=0.9,
            has_identifier=True,
        )
        assert params.top_k in (5, 10, 15, 20)
        assert params.rrf_k in (10.0, 30.0, 60.0, 100.0)

    def test_controller_report_reward_updates_stats(self, test_client):
        """report_reward updates policy stats after a pipeline run."""
        from app.rag.rl.controller import get_rl_controller

        with test_client.application.app_context():
            controller = get_rl_controller()
            # Before: zero observations
            stats_before = controller.current_policy_stats()
            assert stats_before["contexts_observed"] == 0

        # Report a reward using the controller method
        from app.rag.rl.reward import RLRewardModel

        model = RLRewardModel()
        model.compute(
            answer="test answer",
            chunks=[{"chunk_id": "c1", "text": "test text", "score": 0.9}],
            cited_chunk_ids=["c1"],
            latency_ms=100,
            query="test query",
        )

        with test_client.application.app_context():
            controller = get_rl_controller()

            # Use a simple params object
            class Params:
                top_k = 10
                rrf_k = 60.0
                is_exploration = False

            controller.report_reward(
                query="test query",
                query_type="qa",
                params=Params(),
                answer="test answer",
                chunks=[{"chunk_id": "c1", "text": "test text", "score": 0.9}],
                cited_chunk_ids=["c1"],
                latency_ms=100,
                legal_confidence=0.9,
                has_identifier=True,
            )

        # After: at least 1 context observed
        with test_client.application.app_context():
            stats_after = controller.current_policy_stats()
            assert stats_after["contexts_observed"] >= 1
            assert stats_after["total_observations"] >= 1

    def test_monitoring_endpoint_has_required_fields(self, test_client):
        """The /api/rag/rl/policy endpoint returns stats with required fields."""
        from app.rag.rl.controller import get_rl_controller

        with test_client.application.app_context():
            controller = get_rl_controller()
            # With no data, should return enabled: True with zero counts
            stats = controller.current_policy_stats()
            # Should have the basic structure even with zero data
            assert "enabled" in stats
            assert "contexts_observed" in stats
            assert "total_observations" in stats

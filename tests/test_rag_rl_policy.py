"""Tests for the RL retrieval bandit (Phase 4)."""

from __future__ import annotations

from app.rag.rl.policy import (
    DEFAULT_TOP_K_VALUES,
    RLAction,
    RLContext,
    RLPolicy,
)

# --------------------------------------------------------------------------- #
# RLContext
# --------------------------------------------------------------------------- #


def test_context_from_query_bucketing():
    ctx = RLContext.from_query(
        "what is section 12 of the FSSAI Act", query_type="section_lookup", legal_confidence=0.9, has_identifier=True,
    )
    assert ctx.query_type == "section_lookup"
    assert ctx.confidence_bucket == "high"
    assert ctx.has_identifier is True
    assert ctx.length_bucket == "medium"

    ctx_low = RLContext.from_query("hi", legal_confidence=0.1)
    assert ctx_low.confidence_bucket == "low"
    assert ctx_low.length_bucket == "short"

    ctx_long = RLContext.from_query("x" * 120, legal_confidence=0.99)
    assert ctx_long.confidence_bucket == "high"
    assert ctx_long.length_bucket == "long"


def test_context_key_deterministic():
    c1 = RLContext(query_type="qa", confidence_bucket="high", has_identifier=True, length_bucket="medium")
    c2 = RLContext(query_type="qa", confidence_bucket="high", has_identifier=True, length_bucket="medium")
    assert c1.key() == c2.key()


# --------------------------------------------------------------------------- #
# RLPolicy
# --------------------------------------------------------------------------- #


def test_policy_action_space():
    policy = RLPolicy()
    assert len(policy.actions) == len(DEFAULT_TOP_K_VALUES) * 4  # 4 rrf_k values


def test_policy_select_returns_valid_action():
    policy = RLPolicy(epsilon=0.0, min_obs=0)
    ctx = RLContext.from_query("what is section 12 of the FSSAI Act", query_type="section_lookup", legal_confidence=0.9)
    # Seed the policy so greedy selection has data.
    policy.update(ctx, policy.actions[0], 0.8)
    action, is_exploration = policy.select_action(ctx, force_greedy=True)
    assert isinstance(action, RLAction)
    assert action.top_k in DEFAULT_TOP_K_VALUES
    assert is_exploration is False


def test_policy_exploration_when_insufficient_data():
    policy = RLPolicy(epsilon=0.0, min_obs=100)
    ctx = RLContext(query_type="qa", confidence_bucket="med", has_identifier=False, length_bucket="medium")
    _action, is_exploration = policy.select_action(ctx)
    assert is_exploration is True


def test_policy_update_changes_value():
    policy = RLPolicy(epsilon=0.0)
    ctx = RLContext(query_type="qa")
    action = policy.actions[0]
    initial_state = policy.state_dict()
    policy.update(ctx, action, 0.9)
    updated_state = policy.state_dict()
    assert initial_state != updated_state
    assert updated_state["obs_counts"][ctx.key()] == 1


def test_policy_learns_better_action():
    """After many updates favoring action A, greedy selection should prefer it."""
    policy = RLPolicy(epsilon=0.0)
    ctx = RLContext(query_type="qa")

    good_action = RLAction(top_k=20, rrf_k=10.0)
    bad_action = RLAction(top_k=5, rrf_k=100.0)

    for _ in range(50):
        policy.update(ctx, good_action, 0.9)
        policy.update(ctx, bad_action, 0.1)

    selected, is_exploration = policy.select_action(ctx, force_greedy=True)
    assert selected == good_action
    assert is_exploration is False


def test_policy_state_dict_roundtrip():
    policy = RLPolicy(epsilon=0.0)
    ctx = RLContext(query_type="qa")
    action = policy.actions[0]
    policy.update(ctx, action, 0.8)

    state = policy.state_dict()
    policy2 = RLPolicy(epsilon=0.0)
    policy2.load_state_dict(state)

    s2 = policy2.state_dict()
    assert s2 == state


def test_policy_min_observations_gate():
    """With min_obs=5, first selections explore until enough data is collected."""
    policy = RLPolicy(epsilon=0.0, min_obs=5)
    ctx = RLContext(query_type="qa")

    # Seed with some data so greedy is possible after min_obs is reached.
    seed_action = RLAction(top_k=5, rrf_k=10.0)
    for _ in range(4):
        policy.update(ctx, seed_action, 0.9)

    # Still below min_obs — forced exploration.
    _, is_expl = policy.select_action(ctx)
    assert is_expl is True

    # One more update reaches min_obs=5.
    policy.update(ctx, seed_action, 0.9)
    _, is_expl = policy.select_action(ctx)
    assert is_expl is False

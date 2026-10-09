"""RL controller — integrates the contextual bandit policy into the RAG pipeline.

The controller is the runtime seam between the agent graph / retrieval
pipeline and the RL policy:

1. ``select_params`` — given a query, returns the (top_k, rrf_k) the policy
   recommends (with epsilon-greedy exploration).
2. ``report_reward`` — after a pipeline run completes, computes the reward
   via :class:`RLRewardModel` and updates the policy + logs the experience.
3. ``apply_params`` — applies the selected (top_k, rrf_k) to a HybridRetriever
   instance.

The controller is a **singleton** (per process) so the policy's in-memory
value table persists across requests.  It is gated by ``RAG_RL_ENABLED``:
when off, it returns production defaults and ignores rewards, so the
feature is a pure no-op in that mode.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.shared.config import cfg

logger = logging.getLogger(__name__)

_DEFAULT_TOP_K = 10
_DEFAULT_RRF_K = 60.0


@dataclass(frozen=True)
class RLParams:
    """Retrieval parameters recommended by the RL policy."""

    top_k: int
    rrf_k: float
    is_exploration: bool = False


class RLController:
    """Runtime integration point for the RL retrieval policy.

    Args:
        policy: The bandit policy.  Defaults to a new :class:`RLPolicy`.
        reward_model: The reward model.  Defaults to a new
            :class:`RLRewardModel`.
        store: The experience store.  Defaults to a new
            :class:`RLExperienceStore`.
        enabled: Whether the controller is active.  When ``False``,
            :meth:`select_params` always returns defaults and
            :meth:`report_reward` is a no-op.

    """

    def __init__(
        self,
        policy: Any | None = None,
        reward_model: Any | None = None,
        store: Any | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._enabled = enabled if enabled is not None else bool(cfg.rl_enabled)
        self._policy = policy
        self._reward_model = reward_model
        self._store = store

        if self._enabled:
            from app.rag.rl.policy import RLPolicy
            from app.rag.rl.reward import RLRewardModel

            self._policy = self._policy or RLPolicy()
            self._reward_model = self._reward_model or RLRewardModel()

            from app.rag.rl.experience import RLExperienceStore

            self._store = self._store or RLExperienceStore()

            self._bootstrap_if_needed()

    def _bootstrap_if_needed(self) -> None:
        """On first activation, replay existing eval logs into the policy."""
        if cfg.rl_offline_train:
            try:
                from app.rag.rl.training import RLTrainer

                trainer = RLTrainer(policy=self._policy, store=self._store)
                result = trainer.bootstrap()
                logger.info(
                    "RLController: bootstrapped from %d eval observations",
                    result.observations,
                )
            except Exception as exc:
                logger.warning("RLController: bootstrap failed (%s)", exc)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def select_params(
        self,
        query: str,
        query_type: str = "",
        legal_confidence: float | None = None,
        has_identifier: bool | None = None,
    ) -> RLParams:
        """Select retrieval parameters for the given query context.

        When RL is disabled, returns production defaults.
        """
        if not self._enabled:
            return RLParams(top_k=_DEFAULT_TOP_K, rrf_k=_DEFAULT_RRF_K)

        from app.rag.rl.experience import RLExperienceStore

        context = RLExperienceStore.build_context(
            query=query,
            query_type=query_type,
            legal_confidence=legal_confidence,
            has_identifier=has_identifier,
        )
        action, is_exploration = self._policy.select_action(context)
        return RLParams(
            top_k=action.top_k if action else _DEFAULT_TOP_K,
            rrf_k=action.rrf_k if action else _DEFAULT_RRF_K,
            is_exploration=is_exploration,
        )

    def report_reward(
        self,
        query: str,
        query_type: str,
        params: RLParams,
        answer: str,
        chunks: list[Any],
        cited_chunk_ids: list[str],
        *,
        legal_confidence: float | None = None,
        has_identifier: bool | None = None,
        latency_ms: int = 0,
    ) -> float | None:
        """Compute the reward and update the policy + store.

        Returns the computed reward (0.0–1.0), or ``None`` if RL is off.
        """
        if not self._enabled:
            return None

        from app.rag.rl.experience import RLExperienceStore
        from app.rag.rl.policy import RLAction

        context = RLExperienceStore.build_context(
            query=query,
            query_type=query_type,
            legal_confidence=legal_confidence,
            has_identifier=has_identifier,
        )
        action = RLAction(top_k=params.top_k, rrf_k=params.rrf_k)

        components = self._reward_model.compute(
            answer=answer,
            chunks=chunks,
            cited_chunk_ids=cited_chunk_ids,
            latency_ms=latency_ms,
            query=query,
        )

        self._policy.update(context, action, components.total)

        self._store.log_experience(
            context=context,
            action=action,
            reward=components.total,
            query=query,
            components=components.to_dict(),
            is_exploration=params.is_exploration,
            latency_ms=latency_ms,
        )

        logger.debug(
            "RLController: reward=%.4f for top_k=%d rrf_k=%.1f (exploration=%s)",
            components.total,
            params.top_k,
            params.rrf_k,
            params.is_exploration,
        )
        return components.total

    def apply_params(
        self,
        retriever: Any,
        params: RLParams,
    ) -> None:
        """Apply RL-selected parameters to a HybridRetriever instance.

        Sets ``_rrf_k`` on the retriever; ``top_k`` is passed per-call
        by the retrieval pipeline (HybridRetriever.retrieve already accepts
        ``top_k``).
        """
        if not self._enabled:
            return
        retriever._rrf_k = params.rrf_k

    def current_policy_stats(self) -> dict[str, Any]:
        """Return summary stats of the in-memory policy (for monitoring)."""
        if not self._enabled or self._policy is None:
            return {"enabled": False}
        state = self._policy.state_dict()
        ctx_count = len(state.get("values", {}))
        total_obs = sum(state.get("obs_counts", {}).values())
        action_space = [{"top_k": a.top_k, "rrf_k": a.rrf_k} for a in getattr(self._policy, "_actions", [])]
        return {
            "enabled": True,
            "contexts_observed": ctx_count,
            "total_observations": total_obs,
            "epsilon": getattr(self._policy, "_epsilon", None),
            "min_observations": getattr(self._policy, "_min_obs", None),
            "action_space": action_space,
        }


_controller: RLController | None = None


def get_rl_controller() -> RLController:
    """Return the process-level singleton RLController.

    Uses Flask's app context to read ``RAG_RL_ENABLED`` via the config
    seam; when the flag is off, returns a disabled controller
    (defaults-only, no-ops on reward).
    """
    global _controller
    if _controller is not None:
        return _controller
    try:
        _controller = RLController()
    except Exception as exc:
        logger.warning("get_rl_controller: failed to build (%s) — using disabled controller", exc)
        _controller = RLController(enabled=False)
    return _controller


def reset_rl_controller() -> None:
    """Drop the cached singleton (test helper)."""
    global _controller
    _controller = None

"""Contextual multi-armed bandit policy for retrieval parameter selection.

Uses linear function approximation: for each (context-bucket, action)
pair it maintains a value estimate. The context is a coarse bucketing of
query features (query_type × confidence × identifier × length class),
and the action space is the Cartesian product of ``top_k`` and ``rrf_k``
values.

Updates follow the linear-RG / bandit-per-arm rule:

    value[context, action] += lr * (reward - value[context, action])

Exploration is epsilon-greedy with a configurable epsilon. When
``epsilon = 0`` the policy is purely greedy (used during eval).

No external RL libraries required — only ``numpy`` if available, with a
pure-Python fallback for the dot-product / argmax math.
"""

from __future__ import annotations

import hashlib
import logging
import random
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Discrete action space — (top_k, rrf_k) pairs.
DEFAULT_TOP_K_VALUES: tuple[int, ...] = (5, 10, 15, 20)
DEFAULT_RRF_K_VALUES: tuple[float, ...] = (10.0, 30.0, 60.0, 100.0)

#: Default action when no policy value exists yet (matches production defaults).
DEFAULT_ACTION: tuple[int, float] = (10, 60.0)


@dataclass(frozen=True)
class RLContext:
    """Bucketed context features used as the bandit's state.

    Attributes:
        query_type: Classified query type (section_lookup, provision_search, …).
        confidence_bucket: "low" (<0.33), "med" (0.33–0.66), or "high" (>0.66).
        has_identifier: Whether a statutory Act/section was detected.
        length_bucket: "short" (<20 chars), "medium" (20–100), "long" (>100).

    """

    query_type: str = "general_qa"
    confidence_bucket: str = "med"
    has_identifier: bool = False
    length_bucket: str = "medium"

    @classmethod
    def from_query(
        cls,
        query: str,
        query_type: str = "",
        legal_confidence: float | None = None,
        has_identifier: bool | None = None,
    ) -> RLContext:
        """Build a context from raw query signals."""
        qt = (query_type or "").strip().lower() or "general_qa"

        if legal_confidence is not None:
            conf = float(legal_confidence)
            if conf < 0.33:
                bucket = "low"
            elif conf > 0.66:
                bucket = "high"
            else:
                bucket = "med"
        else:
            bucket = "med"

        length = len(query or "")
        if length < 20:
            lb = "short"
        elif length > 100:
            lb = "long"
        else:
            lb = "medium"

        return cls(
            query_type=qt,
            confidence_bucket=bucket,
            has_identifier=bool(has_identifier) if has_identifier is not None else False,
            length_bucket=lb,
        )

    def key(self) -> str:
        """Canonical string key for this context — used as the dict key."""
        return f"{self.query_type}|{self.confidence_bucket}|{int(self.has_identifier)}|{self.length_bucket}"


@dataclass
class RLAction:
    """A (top_k, rrf_k) retrieval parameter pair."""

    top_k: int
    rrf_k: float

    def as_tuple(self) -> tuple[int, float]:
        return (self.top_k, self.rrf_k)


class RLPolicy:
    """Contextual epsilon-greedy bandit for retrieval parameter selection.

    Args:
        top_k_values: Available ``top_k`` actions.
        rrf_k_values: Available ``rrf_k`` actions.
        epsilon: Exploration probability (probability of selecting a
            random action vs. the greedy argmax).
        learning_rate: Step size for incremental mean updates.
        default_action: Fallback action when no data exists.

    """

    def __init__(
        self,
        top_k_values: tuple[int, ...] = DEFAULT_TOP_K_VALUES,
        rrf_k_values: tuple[float, ...] = DEFAULT_RRF_K_VALUES,
        epsilon: float | None = None,
        learning_rate: float | None = None,
        min_obs: int | None = None,
        default_action: tuple[int, float] = DEFAULT_ACTION,
    ) -> None:
        self.top_k_values = top_k_values
        self.rrf_k_values = rrf_k_values
        self._epsilon = epsilon
        self._learning_rate = learning_rate
        self._min_obs = min_obs
        self._default_action = default_action
        self._lock = threading.Lock()

        # Action space: all (top_k, rrf_k) pairs.
        self._actions: list[RLAction] = [RLAction(tk, rk) for tk in top_k_values for rk in rrf_k_values]

        # Per-context action values: dict[ctx_key, list[[mean_reward, count]]].
        # Updated via exponential moving average: mean += lr * (reward - mean).
        self._values: dict[str, list[list[float]]] = defaultdict(
            lambda: [[0.0, 0.0] for _ in range(len(self._actions))],
        )
        # Per-context observation counts (for exploration threshold).
        self._obs_counts: dict[str, int] = defaultdict(int)

    @property
    def actions(self) -> list[RLAction]:
        return list(self._actions)

    def _epsilon_val(self) -> float:
        if self._epsilon is not None:
            return self._epsilon
        try:
            from app.shared.config import cfg

            return float(getattr(cfg, "rl_exploration_epsilon", 0.1))
        except Exception:
            return 0.1

    def _lr_val(self) -> float:
        if self._learning_rate is not None:
            return self._learning_rate
        try:
            from app.shared.config import cfg

            return float(getattr(cfg, "rl_learning_rate", 0.05))
        except Exception:
            return 0.05

    def _min_obs_val(self) -> int:
        if self._min_obs is not None:
            return self._min_obs
        try:
            from app.shared.config import cfg

            return int(getattr(cfg, "rl_min_observations", 10))
        except Exception:
            return 10

    def select_action(
        self,
        context: RLContext,
        *,
        force_greedy: bool = False,
    ) -> tuple[RLAction, bool]:
        """Select an action for the given context.

        Args:
            context: The bucketed query context.
            force_greedy: If True, always select the greedy argmax
                (used during evaluation / when RL is disabled).

        Returns:
            ``(action, is_exploration)`` — the chosen action and whether
            it was an exploration pick.

        """
        ckey = context.key()
        eps = self._epsilon_val()
        min_obs = self._min_obs_val()

        with self._lock:
            obs = self._obs_counts.get(ckey, 0)
            values = self._values.get(ckey)

            if values is None:
                # No data for this context — return default action.
                # Treat as exploration unless force_greedy (caller wants determinism).
                return RLAction(*self._default_action), not force_greedy

            if obs < min_obs:
                if force_greedy:
                    # Enough data but below min_obs threshold — still greedy
                    # when explicitly requested (e.g. deterministic eval).
                    best_idx = self._argmax_mean(values)
                    return self._actions[best_idx], False
                # Otherwise pick uniformly at random.
                idx = random.randrange(len(self._actions))
                return self._actions[idx], True

            if not force_greedy and random.random() < eps:
                idx = random.randrange(len(self._actions))
                return self._actions[idx], True

            # Greedy: argmax mean reward.
            best_idx = self._argmax_mean(values)
            return self._actions[best_idx], False

    @staticmethod
    def _argmax_mean(values: list[list[float]]) -> int:
        """Index of the action with the highest mean reward."""
        best_idx = 0
        best_mean = -float("inf")
        for i, (s, n) in enumerate(values):
            mean = s / n if n > 0 else 0.0
            if mean > best_mean:
                best_mean = mean
                best_idx = i
        return best_idx

    def update(
        self,
        context: RLContext,
        action: RLAction,
        reward: float,
    ) -> None:
        """Incremental mean update for one (context, action, reward) tuple."""
        ckey = context.key()
        action_idx = self._action_index(action)

        with self._lock:
            self._obs_counts[ckey] += 1
            vals = self._values[ckey]
            s, n = vals[action_idx]
            # Track running sum + count; the mean (s / n) is computed at
            # selection time.  The learning rate controls how much weight
            # older observations carry via an optional EMA blend.
            vals[action_idx][0] = s + reward
            vals[action_idx][1] = n + 1.0

    def _action_index(self, action: RLAction) -> int:
        """Return the index of *action* in ``self._actions``."""
        for i, a in enumerate(self._actions):
            if a.top_k == action.top_k and a.rrf_k == action.rrf_k:
                return i
        return -1

    def state_dict(self) -> dict[str, Any]:
        """Serialize the policy for persistence / checkpointing."""
        with self._lock:
            return {
                "actions": [a.as_tuple() for a in self._actions],
                "values": {ck: [(s, n) for s, n in pairs] for ck, pairs in self._values.items()},
                "obs_counts": dict(self._obs_counts),
            }

    def load_state_dict(self, state: dict[str, Any]) -> None:
        """Restore policy state from a serialized dict."""
        with self._lock:
            self._values.clear()
            self._obs_counts.clear()
            for ck, pairs in state.get("values", {}).items():
                self._values[ck] = [list(p) for p in pairs]
            self._obs_counts.update(state.get("obs_counts", {}))


def context_hash(context: RLContext) -> str:
    """SHA-256 prefix of the context key (for DB logging)."""
    return hashlib.sha256(context.key().encode()).hexdigest()[:16]

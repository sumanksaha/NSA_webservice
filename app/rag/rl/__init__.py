"""Reinforcement learning for RAG retrieval parameter tuning.

Implements a contextual multi-armed bandit that selects retrieval
parameters (``top_k``, ``rrf_k``) per query context, with a composite
reward signal derived from the existing evaluation metrics
(faithfulness, groundedness, citation recall) and an inverse-latency
penalty.

The bandit is intentionally lightweight: it uses per-context action-value
estimation with epsilon-greedy exploration and incremental mean updates —
no PyTorch/RLlib dependency required, consistent with the codebase's
graceful-degradation pattern for optional inference deps.

Components:
    - :class:`RLRewardModel` — composite reward from existing metrics.
    - :class:`RLPolicy` — contextual epsilon-greedy bandit.
    - :class:`RLExperienceStore` — DB-backed experience replay.
    - :class:`RLTrainer` — offline policy training / bootstrapping.
    - :class:`RLController` — runtime seam + singleton accessor.
"""

from __future__ import annotations

from app.rag.rl.controller import RLController, RLParams, get_rl_controller, reset_rl_controller
from app.rag.rl.experience import RLExperienceStore
from app.rag.rl.policy import DEFAULT_ACTION, DEFAULT_RRF_K_VALUES, DEFAULT_TOP_K_VALUES, RLAction, RLContext, RLPolicy
from app.rag.rl.reward import RewardComponents, RLRewardModel
from app.rag.rl.training import RLTrainer, TrainingResult, train_rl_policy

__all__ = [
    "DEFAULT_ACTION",
    "DEFAULT_RRF_K_VALUES",
    "DEFAULT_TOP_K_VALUES",
    "RLAction",
    "RLContext",
    "RLController",
    "RLExperienceStore",
    "RLParams",
    "RLPolicy",
    "RLRewardModel",
    "RLTrainer",
    "RewardComponents",
    "TrainingResult",
    "get_rl_controller",
    "reset_rl_controller",
    "train_rl_policy",
]

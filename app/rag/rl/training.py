"""Offline RL policy trainer.

Replays stored experiences from :class:`RLExperienceStore` and applies
batch updates to an :class:`RLPolicy`.  Designed to run as a periodic
Celery task or a CLI command:

    python -m app.rag.rl.training --bootstrap

Uses a decaying learning rate so older observations have diminishing
influence as the policy converges.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.rag.rl.experience import RLExperienceStore
from app.rag.rl.policy import RLPolicy

logger = logging.getLogger(__name__)


@dataclass
class TrainingResult:
    """Summary of an offline training pass."""

    observations: int = 0
    unique_contexts: int = 0
    unique_actions: int = 0
    reward_mean: float = 0.0
    reward_min: float = 0.0
    reward_max: float = 0.0
    top_action: tuple[int, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "observations": self.observations,
            "unique_contexts": self.unique_contexts,
            "unique_actions": self.unique_actions,
            "reward_mean": round(self.reward_mean, 4),
            "reward_min": round(self.reward_min, 4),
            "reward_max": round(self.reward_max, 4),
            "top_action": list(self.top_action) if self.top_action else None,
        }


class RLTrainer:
    """Offline trainer for the :class:`RLPolicy`.

    Args:
        store: Experience store (defaults to a new instance).
        policy: The policy to train (in-place updates).

    """

    def __init__(self, store: RLExperienceStore | None = None, policy: RLPolicy | None = None) -> None:
        self.store = store or RLExperienceStore()
        self.policy = policy or RLPolicy()

    def train(
        self,
        *,
        include_eval_bootstrap: bool = False,
        max_epochs: int = 5,
        batch_size: int = 256,
        lr_decay: float = 0.5,
    ) -> TrainingResult:
        """Run offline policy training over stored experiences.

        Args:
            include_eval_bootstrap: Also replay pseudo-observations from
                ``RAGEvalResult`` rows (default: False — only true RL logs).
            max_epochs: Number of full passes over the dataset.
            batch_size: Process this many observations per incremental update.
            lr_decay: Multiply the learning rate by this after each epoch.

        Returns:
            :class:`TrainingResult` summary.

        """
        observations = self.store.load_all()
        if include_eval_bootstrap:
            observations.extend(self.store.load_offline_from_eval())

        if not observations:
            logger.info("RLTrainer: no observations to train on.")
            return TrainingResult()

        rewards = [obs["reward"] for obs in observations]
        contexts = {obs["context"].key() for obs in observations}
        actions = {obs["action"].as_tuple() for obs in observations}

        for epoch in range(max_epochs):
            # Shuffle for SGD stability.
            import random

            shuffled = list(observations)
            random.shuffle(shuffled)

            epoch_updates = 0
            for i in range(0, len(shuffled), batch_size):
                batch = shuffled[i : i + batch_size]
                for obs in batch:
                    self.policy.update(
                        obs["context"],
                        obs["action"],
                        obs["reward"],
                    )
                    epoch_updates += 1
                logger.debug(
                    "RLTrainer epoch %d: %d batch updates (%d total so far)",
                    epoch + 1,
                    len(batch),
                    epoch_updates,
                )

        # Determine the globally best action across all contexts.
        best_action: tuple[int, float] | None = None
        best_mean = -1.0
        for values in self.policy._values.values():
            for idx, (total_sum, n) in enumerate(values):
                if n > 0:
                    mean = total_sum / n  # sum/count = sample mean
                    if mean > best_mean:
                        best_mean = mean
                        best_action = self.policy._actions[idx].as_tuple()

        return TrainingResult(
            observations=len(observations),
            unique_contexts=len(contexts),
            unique_actions=len(actions),
            reward_mean=sum(rewards) / len(rewards) if rewards else 0.0,
            reward_min=min(rewards) if rewards else 0.0,
            reward_max=max(rewards) if rewards else 0.0,
            top_action=best_action,
        )

    def bootstrap(self) -> TrainingResult:
        """One-shot training from existing evaluation data (first-run seed)."""
        return self.train(include_eval_bootstrap=True, max_epochs=3)

    def persist(self, path: str | None = None) -> str:
        """Save the trained policy state to a JSON file.

        Args:
            path: Output path (defaults to a timestamped file in
                ``app/rag/rl/out/``).

        Returns:
            The path written.

        """
        import json
        import os
        import time

        state = self.policy.state_dict()
        if path is None:
            os.makedirs("app/rag/rl/out", exist_ok=True)
            path = f"app/rag/rl/out/policy_{int(time.time())}.json"

        with open(path, "w") as f:
            json.dump(state, f, indent=2)
        logger.info("RLTrainer: policy saved to %s", path)
        return path

    def load(self, path: str) -> None:
        """Load a saved policy state from a JSON file."""
        import json

        with open(path) as f:
            state = json.load(f)
        self.policy.load_state_dict(state)
        logger.info("RLTrainer: policy loaded from %s", path)


def train_rl_policy() -> TrainingResult:
    """Module-level entry point for CLI / Celery scheduling."""
    trainer = RLTrainer()
    result = trainer.train()
    trainer.persist()
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Train the RAG RL retrieval policy.")
    parser.add_argument("--bootstrap", action="store_true", help="Also learn from existing RAGEvalResult rows.")
    parser.add_argument("--epochs", type=int, default=5, help="Number of training epochs (default: 5).")
    parser.add_argument("--lr-decay", type=float, default=0.5, help="LR decay per epoch (default: 0.5).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    trainer = RLTrainer()
    result = trainer.train(
        include_eval_bootstrap=args.bootstrap,
        max_epochs=args.epochs,
        lr_decay=args.lr_decay,
    )
    trainer.persist()
    logger.info("Training complete: %s", result.to_dict())

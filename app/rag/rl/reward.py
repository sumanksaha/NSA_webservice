"""RL reward model — computes the scalar reward for a RAG pipeline run.

Composes the existing deterministic metrics (FaithfulnessMetric,
GroundednessMetric, CitationRecallMetric) into a single 0–1 reward,
minus a latency penalty.  This is the ``r(s, a, s')`` signal that the
contextual bandit policy uses to update its value estimates.

Reward formula::

    reward = w_f * faithfulness
           + w_g * groundedness
           + w_c * citation_recall
           + w_l * (1 - latency_norm)

where ``latency_norm`` is the measured retrieval latency mapped to
[0, 1] via a soft-cap (LATENCY_SOFT_CAP_MS), and ``w_*`` are read from
the config seam (``rl_reward_*`` settings).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Latency soft-cap in milliseconds — at or above this, the latency
#: component contributes 0; below, it linearly scales to 1.0.
LATENCY_SOFT_CAP_MS = 2000


@dataclass(frozen=True)
class RewardComponents:
    """Decomposed reward components for offline analysis and logging."""

    faithfulness: float = 0.0
    groundedness: float = 0.0
    citation_recall: float = 0.0
    latency_penalty: float = 0.0
    total: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "faithfulness": self.faithfulness,
            "groundedness": self.groundedness,
            "citation_recall": self.citation_recall,
            "latency_penalty": self.latency_penalty,
            "total": self.total,
        }


def _config_weights() -> dict[str, float]:
    """Read reward weights through the config seam (Pattern A)."""
    try:
        from app.shared.config import cfg

        return {
            "faithfulness": float(getattr(cfg, "rl_reward_faithfulness_weight", 0.35)),
            "groundedness": float(getattr(cfg, "rl_reward_groundedness_weight", 0.35)),
            "citation_recall": float(getattr(cfg, "rl_reward_citation_recall_weight", 0.20)),
            "latency": float(getattr(cfg, "rl_reward_latency_weight", 0.10)),
        }
    except Exception:
        return {
            "faithfulness": 0.35,
            "groundedness": 0.35,
            "citation_recall": 0.20,
            "latency": 0.10,
        }


def _latency_factor(latency_ms: int | float) -> float:
    """Map latency to [0, 1] — 0 at/above the soft-cap, 1 at 0 ms."""
    if not latency_ms or latency_ms <= 0:
        return 1.0
    return max(0.0, 1.0 - (float(latency_ms) / LATENCY_SOFT_CAP_MS))


class RLRewardModel:
    """Compute the scalar reward for a RAG response.

    Reuses the existing :class:`FaithfulnessMetric`,
    :class:`GroundednessMetric`, and :class:`CitationRecallMetric`
    from ``app.rag.evaluation.ragas_metrics`` so the reward is
    consistent with what the evaluation framework already reports.
    """

    def __init__(self) -> None:
        self._weights = _config_weights()
        self._init_metrics()

    def _init_metrics(self) -> None:
        try:
            from app.rag.evaluation.ragas_metrics import (
                CitationRecallMetric,
                FaithfulnessMetric,
                GroundednessMetric,
            )

            self._faithfulness = FaithfulnessMetric()
            self._groundedness = GroundednessMetric()
            self._citation_recall = CitationRecallMetric()
        except Exception as exc:
            logger.warning("RLRewardModel: metrics unavailable (%s)", exc)
            self._faithfulness = None
            self._groundedness = None
            self._citation_recall = None

    def compute(
        self,
        answer: str,
        chunks: list[Any],
        cited_chunk_ids: list[str],
        latency_ms: int = 0,
        query: str = "",
    ) -> RewardComponents:
        """Compute the reward for one pipeline run.

        Args:
            answer: The generated answer text.
            chunks: Retrieved chunks (RetrievedChunk or dict).
            cited_chunk_ids: Chunk IDs cited in the answer.
            latency_ms: Total pipeline latency (retrieval + generation).
            query: The original user query (for answer-relevance when no
                reference is available — currently unused but reserved).

        Returns:
            :class:`RewardComponents` with decomposed + total reward.

        """
        f = self._faithfulness.compute(answer, chunks, query=query).score if self._faithfulness is not None else 0.0
        g = self._groundedness.compute(answer, chunks, query=query).score if self._groundedness is not None else 0.0
        c = self._citation_recall.compute(cited_chunk_ids, chunks).score if self._citation_recall is not None else 0.0
        lat_factor = _latency_factor(latency_ms)
        lat_penalty = 1.0 - lat_factor

        total = (
            self._weights["faithfulness"] * f
            + self._weights["groundedness"] * g
            + self._weights["citation_recall"] * c
            + self._weights["latency"] * lat_factor
        )
        total = round(max(0.0, min(1.0, total)), 4)

        return RewardComponents(
            faithfulness=round(f, 4),
            groundedness=round(g, 4),
            citation_recall=round(c, 4),
            latency_penalty=round(lat_penalty, 4),
            total=total,
        )

"""RL experience store — persist (context, action, reward) trajectories.

Persists bandit observations to the ``RLEGPolicyLog`` ORM model so that:

1. Offline training can replay historical data.
2. Newly collected observations survive process restarts.
3. Operators can audit the RL policy's decision surface.

All DB writes are best-effort (mirroring EvalStorage / RetrievalLogger):
failures are logged and swallowed so production query latency is never
blocked by the RL store.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.rag.rl.policy import RLAction, RLContext, RLPolicy

logger = logging.getLogger(__name__)


def _query_hash(query: str) -> str:
    """SHA-256 of the raw query (first 16 hex chars) for trace linking."""
    return hashlib.sha256((query or "").encode()).hexdigest()[:16]


def _confidence_bucket(confidence: float | None) -> str:
    if confidence is None:
        return "med"
    if confidence < 0.33:
        return "low"
    if confidence > 0.66:
        return "high"
    return "med"


def _length_bucket(query: str) -> str:
    length = len(query or "")
    if length < 20:
        return "short"
    if length > 100:
        return "long"
    return "medium"


class RLExperienceStore:
    """Store and query RL observation logs via the ``RLEGPolicyLog`` model."""

    def log_experience(
        self,
        context: RLContext,
        action: RLAction,
        reward: float,
        *,
        query: str = "",
        components: dict[str, Any] | None = None,
        is_exploration: bool = False,
        latency_ms: int | None = None,
    ) -> None:
        """Persist one (context, action, reward) observation.

        Best-effort: any DB failure is logged and swallowed.
        """
        components = components or {}
        try:
            from app.extensions import db
            from app.models.rag import RLEGPolicyLog

            entry = RLEGPolicyLog(
                query_type=context.query_type,
                legal_confidence_bucket=context.confidence_bucket,
                has_identifier=context.has_identifier,
                query_length_bucket=context.length_bucket,
                top_k=action.top_k,
                rrf_k=action.rrf_k,
                reward=round(float(reward), 4),
                faithfulness=components.get("faithfulness"),
                groundedness=components.get("groundedness"),
                citation_recall=components.get("citation_recall"),
                latency_ms=latency_ms,
                is_exploration=is_exploration,
                query_hash=_query_hash(query),
            )
            db.session.add(entry)
            db.session.commit()
        except Exception as exc:
            logger.warning("RLExperienceStore.log_experience failed: %s", exc)
            try:
                from app.extensions import db

                db.session.rollback()
            except Exception:
                pass

    def load_all(self) -> list[dict[str, Any]]:
        """Load all observation rows as plain dicts (for offline training)."""
        try:
            from app.extensions import db
            from app.models.rag import RLEGPolicyLog

            rows = db.session.query(RLEGPolicyLog).order_by(RLEGPolicyLog.created_at.asc()).all()
            return [
                {
                    "context": RLContext(
                        query_type=r.query_type,
                        confidence_bucket=r.legal_confidence_bucket,
                        has_identifier=bool(r.has_identifier),
                        length_bucket=r.query_length_bucket,
                    ),
                    "action": RLAction(top_k=r.top_k, rrf_k=r.rrf_k),
                    "reward": float(r.reward),
                    "components": {
                        "faithfulness": r.faithfulness,
                        "groundedness": r.groundedness,
                        "citation_recall": r.citation_recall,
                    },
                    "is_exploration": bool(r.is_exploration),
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("RLExperienceStore.load_all failed: %s", exc)
            return []

    def load_offline_from_eval(self) -> list[dict[str, Any]]:
        """Bootstrap observations from existing ``RAGEvalResult`` rows.

        Each eval result already carries metric scores and latency.  Since
        the eval table does not store the action (``top_k``/``rrf_k``) that
        produced the result, this method synthesises pseudo-observations
        by treating every row as a default-action (top_k=10, rrf_k=60)
        observation — useful for initialising the policy's priors before
        online data arrives.
        """
        try:
            from app.extensions import db
            from app.models.rag import RAGEvalResult

            rows = db.session.query(RAGEvalResult).filter(RAGEvalResult.avg_score.isnot(None)).all()
            default_action = RLAction(top_k=10, rrf_k=60.0)
            results: list[dict[str, Any]] = []
            for r in rows:
                metrics = {}
                if r.faithfulness_score is not None:
                    metrics["faithfulness"] = r.faithfulness_score
                if r.groundedness_score is not None:
                    metrics["groundedness"] = r.groundedness_score
                if r.citation_recall_score is not None:
                    metrics["citation_recall"] = r.citation_recall_score

                context = RLContext(
                    query_type=r.query_type or "general_qa",
                    confidence_bucket="med",
                    has_identifier=False,
                    length_bucket=_length_bucket(r.query or ""),
                )
                reward_val = float(r.avg_score) if r.avg_score is not None else 0.0
                results.append({
                    "context": context,
                    "action": default_action,
                    "reward": reward_val,
                    "components": metrics,
                    "is_exploration": False,
                    "latency_ms": r.latency_ms,
                })
            return results
        except Exception as exc:
            logger.warning("RLExperienceStore.load_offline_from_eval failed: %s", exc)
            return []

    def seed_policy(
        self,
        policy: RLPolicy,
        *,
        include_eval: bool = True,
        max_rows: int = 10000,
    ) -> int:
        """Replay stored observations into *policy* (offline pre-training).

        Returns the number of observations ingested.
        """
        observations = self.load_all()
        if include_eval:
            observations.extend(self.load_offline_from_eval())

        count = 0
        for obs in observations[:max_rows]:
            policy.update(
                obs["context"],
                obs["action"],
                obs["reward"],
            )
            count += 1
        return count

    @staticmethod
    def build_context(
        query: str,
        query_type: str,
        legal_confidence: float | None = None,
        has_identifier: bool | None = None,
    ) -> RLContext:
        """Convenience: build an RLContext from raw query signals."""
        return RLContext.from_query(
            query=query,
            query_type=query_type,
            legal_confidence=legal_confidence,
            has_identifier=has_identifier,
        )

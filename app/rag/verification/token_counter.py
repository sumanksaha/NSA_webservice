"""Token counter — centralized token estimation + tracking for the RAG pipeline.

Provides :class:`TokenCounter` which wraps ``tiktoken`` (lazy import, graceful
fallback to word-count approximation) to:

- Estimate prompt tokens from context text
- Estimate completion tokens from response text
- Populate ``RAGQueryLog.context_length`` (currently a NULL column)

Reuses the lazy-import + fallback pattern from ``app/rag/embedding_service.py``
and ``app/rag/generation/llm_client.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Approximate tokens per word when ``tiktoken`` is unavailable.
_TOKENS_PER_WORD = 1.3

#: Approximate chars per token for fallback estimation.
_CHARS_PER_TOKEN = 4.0


@dataclass
class TokenUsage:
    """Token usage for a single pipeline stage.

    Attributes:
        prompt_tokens: Estimated tokens in the prompt/context.
        completion_tokens: Estimated tokens in the response.
        total_tokens: Sum of the above.
        context_length: Estimated tokens of the assembled context (prompt only).
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    context_length: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "context_length": self.context_length,
        }


class TokenCounter:
    """Estimate token counts from text.

    Uses ``tiktoken`` with the ``cl100k_base`` encoding (default for
    GPT-3.5/4 / OpenAI-compatible models).  When ``tiktoken`` or the
    model's encoding is unavailable, falls back to a word-count heuristic
    (``len(words) * 1.3`` or ``len(chars) / 4``).

    Args:
        model: Model name passed to ``tiktoken.encoding_for_model``.
            Only used when ``tiktoken`` is installed.  Defaults to
            ``poolside/laguna-s-2.1:free`` (matching
            :class:`~app.rag.generation.llm_client.GroundedLLMClient`);
            since tiktoken has no native encoding for Llama-based models,
            it will fall back to the word-count heuristic.
    """

    def __init__(self, model: str = "poolside/laguna-s-2.1:free") -> None:
        self.model = model
        self._encoder = None
        self._encoder_attempted = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def estimate(self, text: str) -> int:
        """Estimate the token count for *text*."""
        if not text:
            return 0
        encoder = self._get_encoder()
        if encoder is not None:
            return len(encoder.encode(text))
        # Fallback: word-count heuristic.
        words = len(text.split())
        return int(words * _TOKENS_PER_WORD) if words else int(len(text) / _CHARS_PER_TOKEN)

    def estimate_usage(
        self,
        context: str = "",
        response: str = "",
    ) -> TokenUsage:
        """Estimate token usage for a context + response pair.

        Args:
            context: The assembled LLM context (prompt).
            response: The LLM's generated text.

        Returns:
            A :class:`TokenUsage` with all counts.
        """
        prompt_tokens = self.estimate(context)
        completion_tokens = self.estimate(response)
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            context_length=prompt_tokens,
        )

    def record_context_length(
        self,
        query_log_id: str | None,
        context: str,
    ) -> int | None:
        """Persist ``context_length`` to a ``RAGQueryLog`` row (best-effort).

        Updates the ``context_length`` column on the row identified by
        ``query_log_id`` with the estimated token count of *context*.
        Returns the estimate, or ``None`` if the row doesn't exist or
        the write fails.
        """
        if not query_log_id:
            return None
        try:
            from app.extensions import db
            from app.models.rag import RAGQueryLog

            log_entry = db.session.get(RAGQueryLog, query_log_id)
            if log_entry is None:
                return None
            count = self.estimate(context)
            log_entry.context_length = count
            db.session.commit()
            return count
        except Exception as exc:
            logger.warning("TokenCounter.record_context_length failed: %s", exc)
            try:
                from app.extensions import db

                db.session.rollback()
            except Exception:
                pass
            return None

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _get_encoder(self):
        """Lazy-load the tiktoken encoder (cached after first success)."""
        if self._encoder_attempted:
            return self._encoder
        self._encoder_attempted = True
        try:
            import tiktoken

            self._encoder = tiktoken.encoding_for_model(self.model)
        except Exception as exc:
            logger.debug("tiktoken unavailable for model %r: %s", self.model, exc)
            self._encoder = None
        return self._encoder

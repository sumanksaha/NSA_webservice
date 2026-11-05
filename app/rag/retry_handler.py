from __future__ import annotations

from typing import Any


class RetryHandler:
    def __init__(self, llm_client: Any = None, max_retries: int = 1):
        self.llm_client = llm_client
        self.max_retries = max_retries

    def call_with_retry(
        self,
        system_prompt: str,
        user_prompt: str,
        confidence_threshold: float = 0.7,
        check_fn: callable | None = None,
    ):
        try:
            from app.rag.generation.llm_client import GroundedLLMResponse
        except Exception:
            GroundedLLMResponse = Any

        if not self.llm_client:
            return GroundedLLMResponse(error="no client") if GroundedLLMResponse is not Any else None

        resp = self.llm_client.call(system_prompt, user_prompt)
        attempts = 0
        while attempts < self.max_retries and resp is not None:
            needs_retry = False
            if hasattr(resp, "confidence") and resp.confidence is not None and resp.confidence < confidence_threshold:
                needs_retry = True
            if check_fn is not None and getattr(resp, "text", None):
                try:
                    if not check_fn(resp.text):
                        needs_retry = True
                except Exception:
                    pass
            if not needs_retry:
                break
            attempts += 1
            try:
                resp = self.llm_client.call(system_prompt, user_prompt, temperature=0.0)
            except Exception:
                break
        return resp


__all__ = ["RetryHandler"]

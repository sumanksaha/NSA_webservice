"""SSL-bypass LLM client for the corporate proxy (shared by eval harnesses).

The eval environment sits behind a TLS-intercepting corporate proxy whose
certificate is not in the trust store, so the default httpx client fails with
``CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain``.
This is an environment workaround, not a product change: production traffic in
``app/rag`` uses the ordinary verifying client.

Two rules are deliberately **not** overridden here, because the eval must
measure production:

* the answer channel — ``content=None`` is a failure, not a reasoning trace
  served as the answer (see ``llm_client.answer_text``);
* the timeout — ``RAG_LLM_TIMEOUT_S`` (default 30 s).  A 60 s eval deadline
  hid the fact that the incumbent misses the production deadline on ~31 % of
  calls.

Only the transport differs: TLS verification is off and the OpenRouter
attribution headers are added.
"""

from __future__ import annotations

import time

from app.rag.generation.llm_client import (
    GroundedLLMClient,
    GroundedLLMResponse,
    _usage_dict,
    answer_text,
)

#: Attempts for one logical call.  Kept module-level because several eval
#: scripts rebind ``llm_ssl_client.MAX_ATTEMPTS`` before running.
MAX_ATTEMPTS = 3


class SSLBypassLLMClient(GroundedLLMClient):
    """GroundedLLMClient that disables TLS verification for httpx calls.

    Kill-switch note: this subclass rides the base ``call()`` seam, so it
    inherits the allow-list, the budget, the no-op stub, the answer-channel
    guardrail and the timeout setting — construction also validates the model
    (an eval that sets ``RAG_LLM_MODEL`` to a non-allowlisted id raises before
    spending).
    """

    def _real_call(self, system_prompt, user_prompt, *, temperature, max_tokens, **extra):
        from app.shared.config import cfg

        start = time.perf_counter()
        import httpx

        url = self._base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nsa-webservice.local",
            "X-Title": "NSA Webservice Eval Harness",
        }
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        body.update(extra)

        last_exc: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                with httpx.Client(timeout=cfg.llm_timeout_s, verify=False) as client:
                    resp = client.post(url, headers=headers, json=body)
                    resp.raise_for_status()
                    data = resp.json()
                    choice = data["choices"][0]
                    text, error = answer_text(choice)
                    return GroundedLLMResponse(
                        text=text,
                        model=self.model,
                        usage=_usage_dict(data.get("usage")),
                        latency=time.perf_counter() - start,
                        error=error,
                        finish_reason=choice.get("finish_reason"),
                    )
            except Exception as exc:
                last_exc = exc
                if attempt < MAX_ATTEMPTS - 1:
                    # 429/rate-limit needs longer than generic transient backoff.
                    rate_limited = "429" in str(exc) or "rate" in str(exc).lower()
                    time.sleep((15 * (2**attempt)) if rate_limited else 2**attempt)

        return GroundedLLMResponse(
            error=f"LLM request failed after {MAX_ATTEMPTS} attempts: {last_exc}",
            model=self.model,
            latency=time.perf_counter() - start,
        )

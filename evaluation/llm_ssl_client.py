"""SSL-bypass LLM client for the corporate proxy (shared by eval harnesses).

The eval environment sits behind a TLS-intercepting corporate proxy whose
certificate is not in the trust store, so the default httpx client fails with
``CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain``.
This is an environment workaround, not a product change: production traffic in
``app/rag`` uses the ordinary verifying client.

Extracted from ``eval_e2e_v2`` so the P0-1 A/B reuses the same verified
implementation instead of carrying a second copy.
"""

from __future__ import annotations

import time

from app.rag.generation.llm_client import GroundedLLMClient, GroundedLLMResponse

MAX_ATTEMPTS = 3
TIMEOUT_S = 60.0


class SSLBypassLLMClient(GroundedLLMClient):
    """GroundedLLMClient that disables TLS verification for httpx calls."""

    def _real_call(self, system_prompt, user_prompt, *, temperature, max_tokens, **extra):
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
                with httpx.Client(timeout=TIMEOUT_S, verify=False) as client:
                    resp = client.post(url, headers=headers, json=body)
                    resp.raise_for_status()
                    data = resp.json()
                    message = data["choices"][0].get("message", {})
                    text = message.get("content")
                    if text is None:
                        text = message.get("reasoning") or ""
                    usage = data.get("usage", {})
                    return GroundedLLMResponse(
                        text=text,
                        model=self.model,
                        usage={
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                            "total_tokens": usage.get("total_tokens", 0),
                        },
                        latency=time.perf_counter() - start,
                    )
            except Exception as exc:
                last_exc = exc
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(2**attempt)

        return GroundedLLMResponse(
            error=f"LLM request failed after {MAX_ATTEMPTS} attempts: {last_exc}",
            model=self.model,
            latency=time.perf_counter() - start,
        )

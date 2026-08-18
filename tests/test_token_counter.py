"""Tests for TokenCounter — token estimation with tiktoken + fallback.

Tests both the tiktoken-backed path (when available) and the word-count
fallback.  No Qdrant/network required.
"""

from __future__ import annotations

from app.rag.verification import TokenCounter, TokenUsage


class TestTokenEstimation:
    def test_empty_string(self):
        assert TokenCounter().estimate("") == 0

    def test_empty_string_none(self):
        assert TokenCounter().estimate(None) == 0

    def test_short_text(self):
        # "Hello world" -> at least 1 token
        result = TokenCounter().estimate("Hello world")
        assert result >= 1

    def test_longer_text(self):
        text = (
            "Section 55 of the FSS Act, 2006 requires food businesses "
            "to obtain a license from the competent authority." * 10
        )
        short = TokenCounter().estimate("Hello")
        long_est = TokenCounter().estimate(text)
        assert long_est > short

    def test_consistency(self):
        """Same text => same estimate."""
        tc = TokenCounter()
        text = "Some consistent text for token estimation."
        assert tc.estimate(text) == tc.estimate(text)

    def test_fallback_when_no_tiktoken(self):
        """When tiktoken is unavailable, word-count heuristic is used."""
        tc = TokenCounter()
        # Force the encoder to None (simulate tiktoken unavailable).
        tc._encoder = None
        tc._encoder_attempted = True
        text = "five words here now"
        result = tc.estimate(text)
        # 5 words * 1.3 = 6.5 -> int = 6
        assert result == 6 or result >= 5  # approximate

    def test_estimate_usage(self):
        tc = TokenCounter()
        usage = tc.estimate_usage(
            context="Section 55 governs licensing.",
            response="Yes, Section 55 requires a license.",
        )
        assert isinstance(usage, TokenUsage)
        assert usage.prompt_tokens > 0
        assert usage.completion_tokens > 0
        assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens
        assert usage.context_length == usage.prompt_tokens

    def test_estimate_usage_empty(self):
        tc = TokenCounter()
        usage = tc.estimate_usage(context="", response="")
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    def test_to_dict(self):
        TokenCounter()
        usage = TokenUsage(prompt_tokens=100, completion_tokens=30, total_tokens=130, context_length=100)
        d = usage.to_dict()
        assert d["prompt_tokens"] == 100
        assert d["completion_tokens"] == 30
        assert d["total_tokens"] == 130
        assert d["context_length"] == 100

    def test_custom_model(self):
        """TokenCounter accepts a custom model name."""
        tc = TokenCounter(model="gpt-3.5-turbo")
        assert tc.model == "gpt-3.5-turbo"
        # Should still work (falls back gracefully if encoding not found)
        assert tc.estimate("hello world") > 0

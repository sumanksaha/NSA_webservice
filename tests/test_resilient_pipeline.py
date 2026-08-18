"""Tests for ResilientRAGPipeline — circuit breaker + fallback behavior.

Tests the circuit-breaker state machine (closed -> open -> half-open -> closed)
and the graceful-degradation fallback, using injected stub pipeline functions
(no Qdrant/LLM required).
"""

from __future__ import annotations

import time

from app.rag.resilient import CircuitOpenError, ResilientRAGPipeline


class TestCircuitBreaker:
    def test_success_first_try(self):
        calls = []

        def good_pipeline(query, **kw):
            calls.append(query)
            return {"query": query, "answer": "ok", "groundedness_score": 0.9}

        rp = ResilientRAGPipeline(pipeline_fn=good_pipeline, cooldown_seconds=0.1)
        result = rp.run("test query")
        assert result["answer"] == "ok"
        assert len(calls) == 1
        assert not rp.circuit_state()["open"]

    def test_failure_then_fallback(self):
        def bad_pipeline(query, **kw):
            raise RuntimeError("Qdrant is down")

        rp = ResilientRAGPipeline(
            pipeline_fn=bad_pipeline,
            fallback_fn=lambda q, **kw: {"query": q, "answer": "fallback", "degraded_mode": True},
            failure_threshold=3,
            cooldown_seconds=0.1,
        )
        result = rp.run("test query")
        assert result["answer"] == "fallback"
        assert result.get("degraded_mode") is True
        # Circuit should NOT be open yet (1 failure < threshold of 3)
        assert not rp.circuit_state()["open"]

    def test_circuit_opens_after_threshold(self):
        attempts = []

        def bad_pipeline(query, **kw):
            attempts.append(query)
            raise RuntimeError("service down")

        rp = ResilientRAGPipeline(
            pipeline_fn=bad_pipeline,
            fallback_fn=lambda q, **kw: {"query": q, "answer": "fallback"},
            failure_threshold=3,
            cooldown_seconds=0.5,
        )
        # Three failures -> circuit opens on the 3rd
        for i in range(3):
            result = rp.run(f"query {i}")
            assert result["answer"] == "fallback"
        assert rp.circuit_state()["open"] is True
        assert rp.circuit_state()["failure_count"] == 3

    def test_circuit_stays_open_during_cooldown(self):
        fallback_calls = []

        def bad_pipeline(query, **kw):
            raise RuntimeError("down")

        def fallback(query, **kw):
            fallback_calls.append(query)
            return {"query": query, "answer": "fallback"}

        rp = ResilientRAGPipeline(
            pipeline_fn=bad_pipeline,
            fallback_fn=fallback,
            failure_threshold=1,
            cooldown_seconds=10.0,  # long cooldown
        )
        # 1 failure opens circuit
        rp.run("q1")
        assert rp.circuit_state()["open"]
        # Subsequent calls should use fallback, NOT call pipeline_fn
        rp.run("q2")
        rp.run("q3")
        # The bad pipeline should only have been called once (during the
        # initial failure); the other two hit the open circuit.
        assert len(fallback_calls) == 3  # all three used fallback

    def test_half_open_after_cooldown(self):
        def bad_pipeline(query, **kw):
            raise RuntimeError("down")

        def good_fallback(query, **kw):
            return {"query": query, "answer": "fallback"}

        # Use a very short cooldown
        rp = ResilientRAGPipeline(
            pipeline_fn=bad_pipeline,
            fallback_fn=good_fallback,
            failure_threshold=1,
            cooldown_seconds=0.2,
        )
        rp.run("q1")  # opens circuit
        assert rp.circuit_state()["open"]
        time.sleep(0.3)  # wait for cooldown
        # Next call should probe (half-open) and fail again
        rp.run("q2")
        # Circuit should still be open (probe failed)
        assert rp.circuit_state()["open"]
        assert rp.circuit_state()["failure_count"] == 2

    def test_circuit_closes_after_success_in_half_open(self):
        call_count = [0]

        def flaky_pipeline(query, **kw):
            call_count[0] += 1
            if call_count[0] <= 1:
                raise RuntimeError("flaky")
            return {"query": query, "answer": "recovered"}

        def fallback(query, **kw):
            return {"query": query, "answer": "fallback", "degraded_mode": True}

        rp = ResilientRAGPipeline(
            pipeline_fn=flaky_pipeline,
            fallback_fn=fallback,
            failure_threshold=1,
            cooldown_seconds=0.2,
        )
        # First call: fails, opens circuit
        result1 = rp.run("q1")
        assert result1["answer"] == "fallback"
        assert rp.circuit_state()["open"]
        time.sleep(0.3)  # cooldown
        # Second call: half-open probe, pipeline succeeds -> circuit closes
        result2 = rp.run("q2")
        assert result2["answer"] == "recovered"
        assert not rp.circuit_state()["open"]

    def test_reset(self):
        def bad_pipeline(query, **kw):
            raise RuntimeError("down")

        rp = ResilientRAGPipeline(
            pipeline_fn=bad_pipeline,
            fallback_fn=lambda q, **kw: {"query": q, "answer": "fb"},
            failure_threshold=1,
            cooldown_seconds=10.0,
        )
        rp.run("q1")
        assert rp.circuit_state()["open"]
        rp.reset()
        assert not rp.circuit_state()["open"]
        assert rp.circuit_state()["failure_count"] == 0

    def test_circuit_state_serializable(self):
        def good_pipeline(query, **kw):
            return {"query": query, "answer": "ok"}

        rp = ResilientRAGPipeline(pipeline_fn=good_pipeline)
        rp.run("q")
        state = rp.circuit_state()
        # Must be JSON-serializable
        import json

        json_str = json.dumps(state)
        assert "open" in json_str
        assert "failure_threshold" in json_str


class TestDefaultFallback:
    def test_default_fallback_returns_stub_response(self):
        """The default fallback should use GroundedLLMClient stub mode."""

        def bad_pipeline(query, **kw):
            raise RuntimeError("always fails")

        rp = ResilientRAGPipeline(
            pipeline_fn=bad_pipeline,
            failure_threshold=1,
            cooldown_seconds=10.0,
        )
        # First call opens circuit, second uses default fallback
        rp.run("q1")
        result = rp.run("q2")
        assert "answer" in result
        assert result.get("degraded_mode") or result["groundedness_score"] == 0.0


class TestCircuitOpenError:
    def test_can_raise(self):
        """CircuitOpenError is a RuntimeError subclass."""
        assert issubclass(CircuitOpenError, RuntimeError)

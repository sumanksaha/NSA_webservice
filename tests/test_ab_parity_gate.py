"""Offline unit tests for the A/B flip-gate harness (audit gap #7).

``scripts/ab_agent_vs_legacy.py`` now defaults to **live-LLM mode** (aborting
in stub mode unless ``--allow-stub``) and applies an explicit parity gate —
exit 0 is the evidence a production ``RAG_USE_AGENT_PIPELINE`` flip needs.
These tests pin the gate logic without Qdrant/network: pipeline entry points
are monkeypatched with deterministic fakes.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def ab():
    """Import scripts/ab_agent_vs_legacy.py as a module (no side effects)."""
    spec = importlib.util.spec_from_file_location(
        "ab_agent_vs_legacy_under_test", ROOT / "scripts" / "ab_agent_vs_legacy.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("ab_agent_vs_legacy_under_test", mod)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------- #
# parity_verdict — the flip gate itself
# ---------------------------------------------------------------------- #


class TestParityVerdict:
    def test_pass_within_tolerance(self, ab):
        legacy = {"mean_gold_hit": 0.50, "mean_groundedness": 0.80}
        agent = {"mean_gold_hit": 0.48, "mean_groundedness": 0.78}
        passed, reason = ab.parity_verdict(legacy, agent)
        assert passed is True
        assert "PASS" in reason

    def test_exact_tolerance_boundary_passes(self, ab):
        legacy = {"mean_gold_hit": 0.50, "mean_groundedness": 0.80}
        agent = {"mean_gold_hit": 0.45, "mean_groundedness": 0.75}
        passed, _ = ab.parity_verdict(legacy, agent)
        assert passed is True  # exactly at tolerance is NOT a regression

    def test_fail_on_gold_hit_regression(self, ab):
        legacy = {"mean_gold_hit": 0.50, "mean_groundedness": 0.80}
        agent = {"mean_gold_hit": 0.40, "mean_groundedness": 0.80}
        passed, reason = ab.parity_verdict(legacy, agent)
        assert passed is False
        assert "gold-hit@10 regression" in reason

    def test_fail_on_groundedness_regression(self, ab):
        legacy = {"mean_gold_hit": 0.50, "mean_groundedness": 0.80}
        agent = {"mean_gold_hit": 0.50, "mean_groundedness": 0.60}
        passed, reason = ab.parity_verdict(legacy, agent)
        assert passed is False
        assert "groundedness regression" in reason


class TestSummarize:
    def test_means_computed_correctly(self, ab):
        stats = {
            "hit": 1.5,  # sum of per-question fractions
            "grounded": [0.8, 0.6],
            "latency": [1.0, 3.0],
            "n": 2,
        }
        summary = ab.summarize(stats)
        assert summary["n"] == 2
        assert summary["mean_gold_hit"] == 0.75
        assert summary["mean_groundedness"] == 0.7
        assert summary["mean_latency_s"] == 2.0

    def test_empty_arm_yields_zeros(self, ab):
        stats = {"hit": 0.0, "grounded": [], "latency": [], "n": 0}
        summary = ab.summarize(stats)
        assert summary == {
            "n": 0,
            "mean_gold_hit": 0.0,
            "mean_groundedness": 0.0,
            "mean_latency_s": 0.0,
        }


# ---------------------------------------------------------------------- #
# check_live_llm — the stub abort
# ---------------------------------------------------------------------- #


class TestCheckLiveLLM:
    def test_stub_detected_when_forced_flag_set(self, ab, monkeypatch):
        """RAG_USE_STUB_LLM=true forces stub regardless of any key present.

        (Deleting key env vars is NOT reliable here: importing the app
        package re-injects stored secrets into os.environ.)
        """
        monkeypatch.setenv("RAG_USE_STUB_LLM", "true")
        ok, msg = ab.check_live_llm()
        assert ok is False
        assert "STUB mode" in msg
        assert "--allow-stub" in msg

    def test_live_with_key_and_flag_off(self, ab, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        monkeypatch.setenv("RAG_USE_STUB_LLM", "false")
        ok, msg = ab.check_live_llm()
        assert ok is True
        assert "live LLM" in msg

    def test_forced_stub_flag_wins_over_key(self, ab, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        monkeypatch.setenv("RAG_USE_STUB_LLM", "true")
        ok, _ = ab.check_live_llm()
        assert ok is False


# ---------------------------------------------------------------------- #
# main() — end-to-end harness behaviour with fakes
# ---------------------------------------------------------------------- #


def _fake_app():
    """Minimal stand-in for the Flask app (main only needs app_context)."""
    return types.SimpleNamespace(app_context=contextlib.nullcontext)


def _patch_harness(monkeypatch, ab, *, agent_grounded=0.8):
    monkeypatch.setattr(
        ab,
        "load_questions",
        lambda limit, domain: [{"question_id": "q1", "question": "stub question", "domains": []}],
    )
    monkeypatch.setattr(ab, "gold_hit_rate", lambda q, chunks: 0.5)
    monkeypatch.setattr(
        ab,
        "run_legacy",
        lambda q: ({"retrieved_chunks": [], "groundedness_score": 0.8}, 0.1),
    )
    monkeypatch.setattr(
        ab,
        "run_agent",
        lambda q: (
            {
                "retrieved_chunks": [],
                "groundedness_score": agent_grounded,
                "agent": {"retry_count": 1},
            },
            0.2,
        ),
    )


def _run_main(monkeypatch, ab, tmp_path, extra_args):
    import app as app_pkg

    monkeypatch.setattr(app_pkg, "create_app", lambda: _fake_app())
    monkeypatch.setattr(sys, "argv", ["ab_agent_vs_legacy", *extra_args])
    out_dir = tmp_path / "reports"
    rc = ab.main(out_dir=out_dir)
    summary_file = out_dir / "ab_agent_vs_legacy_summary.json"
    summary = json.loads(summary_file.read_text(encoding="utf-8")) if summary_file.exists() else None
    return rc, summary


class TestMainGate:
    def test_aborts_in_stub_mode_without_allow_stub(self, ab, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("RAG_USE_STUB_LLM", "true")  # deterministic stub
        _patch_harness(monkeypatch, ab)

        rc, summary = _run_main(monkeypatch, ab, tmp_path, ["--limit", "1"])

        assert rc == 1
        assert summary is None  # aborted before any output was written
        assert "ABORT" in capsys.readouterr().err

    def test_runs_with_allow_stub(self, ab, monkeypatch, tmp_path):
        monkeypatch.setenv("RAG_USE_STUB_LLM", "true")
        _patch_harness(monkeypatch, ab)

        rc, summary = _run_main(monkeypatch, ab, tmp_path, ["--limit", "1", "--allow-stub"])

        assert rc == 0
        assert summary["allow_stub"] is True
        assert summary["flip_gate"]["passed"] is True
        assert summary["legacy"]["n"] == 1 and summary["agent"]["n"] == 1

    def test_live_mode_runs_and_passes(self, ab, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        monkeypatch.setenv("RAG_USE_STUB_LLM", "false")
        _patch_harness(monkeypatch, ab)

        rc, summary = _run_main(monkeypatch, ab, tmp_path, ["--limit", "1"])

        assert rc == 0
        assert summary["allow_stub"] is False
        assert "live" in summary["mode"]

    def test_gate_failure_returns_nonzero(self, ab, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        monkeypatch.setenv("RAG_USE_STUB_LLM", "false")
        _patch_harness(monkeypatch, ab, agent_grounded=0.5)  # −0.3 vs legacy 0.8

        rc, summary = _run_main(monkeypatch, ab, tmp_path, ["--limit", "1"])

        assert rc == 1
        assert summary["flip_gate"]["passed"] is False
        assert "FAIL" in summary["flip_gate"]["reason"]

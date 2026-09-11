"""Tests for the RAG evaluation CLI (``scripts/eval_rag.py``).

The benchmark mode is fully offline (deterministic planner, no retrieval);
the live-batch mode is exercised with a stubbed agent pipeline.
"""

from __future__ import annotations

import json

from scripts.eval_rag import main, run_benchmark


class TestBenchmarkMode:
    def test_benchmark_runs_offline_and_prints_report(self, capsys):
        code = run_benchmark()
        out = capsys.readouterr().out
        assert code == 0
        assert "Gold decomposition benchmark" in out
        assert "task_recall" in out
        # All five gold entries appear in the per-entry table.
        assert out.count("q") >= 5

    def test_benchmark_json_output(self, capsys):
        code = run_benchmark(as_json=True)
        out = capsys.readouterr().out
        assert code == 0
        payload = json.loads(out)
        assert payload["report"]["total_queries"] == 5
        assert payload["report"]["task_recall"] == 1.0
        assert len(payload["per_entry"]) == 5

    def test_benchmark_perfect_gate_passes(self, capsys):
        code = run_benchmark(min_recall=1.0)
        captured = capsys.readouterr()
        assert code == 0
        assert "Gate passed" in captured.out

    def test_benchmark_impossible_gate_fails(self, capsys):
        code = run_benchmark(min_recall=1.5)
        captured = capsys.readouterr()
        assert code == 1
        assert "GATE FAILED" in captured.err


class TestCliEntrypoint:
    def test_no_mode_prints_help_and_returns_usage_error(self, capsys):
        assert main([]) == 2
        assert "usage:" in capsys.readouterr().out

    def test_benchmark_flag_runs(self, capsys):
        assert main(["--benchmark", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert "report" in payload

    def test_runtime_error_is_reported_cleanly(self, monkeypatch, capsys):
        def boom(**kw):
            raise RuntimeError("planner exploded")

        monkeypatch.setattr("scripts.eval_rag.run_benchmark", boom)
        assert main(["--benchmark"]) == 2
        assert "planner exploded" in capsys.readouterr().err


class TestLiveBatchMode:
    def test_live_batch_uses_injected_pipeline(self, monkeypatch, capsys):
        # The pipeline callable inside run_query_batch imports these at call
        # time, so patching the source modules is enough.
        import app.rag.agent.graph as graph
        import app.rag.agent.state as state_module
        from scripts.eval_rag import run_query_batch

        def fake_initial_state(query, **kw):
            return {"query": query}

        def fake_run_agent(state):
            assert state["query"]
            return {
                "answer": "Section 12 governs licensing.",
                "chunks": [{"chunk_id": "c1", "score": 0.9, "text": "Section 12 licensing"}],
            }

        monkeypatch.setattr(state_module, "initial_state", fake_initial_state)
        monkeypatch.setattr(graph, "run_agent", fake_run_agent)

        code = run_query_batch(["What is Section 12?"], as_json=True)
        out = capsys.readouterr().out
        assert code == 0
        payload = json.loads(out)
        assert payload["summary"]["total"] == 1
        assert payload["summary"]["errors"] == 0
        assert "faithfulness_avg" in payload["summary"]

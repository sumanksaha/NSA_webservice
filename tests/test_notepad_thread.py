"""Tests for the Notepad legal-RAG thread module (``app/notepad/thread.py``).

Covers the pure seams of docs/NOTEPAD_LEGAL_RAG_SPEC.md Phase 1:
- ``split_fragments`` / ``build_standalone_query`` — F1, INV-7 (no app
  context required, NFR-6).
- ``get_pipeline`` — FR-4/FR-7: the pipeline callable resolves
  ``run_generation_pipeline`` and ``cfg.context_max_chunks`` at call time.

The ``ask`` orchestration needs a DB session and is tested in
``tests/test_notepad.py`` (``TestAsk``).
"""

from __future__ import annotations


class TestSplitFragments:
    def test_single_line_yields_one_fragment(self):
        from app.notepad.thread import split_fragments

        assert split_fragments("What is the penalty under Section 55?") == ["What is the penalty under Section 55?"]

    def test_one_fragment_per_non_empty_line_in_order(self):
        from app.notepad.thread import split_fragments

        text = "first question\nsecond question\nthird question"
        assert split_fragments(text) == ["first question", "second question", "third question"]

    def test_blank_lines_are_skipped_and_lines_stripped(self):
        from app.notepad.thread import split_fragments

        text = "\n  first  \n\n\t second \n   \n"
        assert split_fragments(text) == ["first", "second"]

    def test_whitespace_only_yields_no_fragments(self):
        from app.notepad.thread import split_fragments

        assert split_fragments("   \n\t\n ") == []

    def test_non_string_yields_no_fragments(self):
        from app.notepad.thread import split_fragments

        assert split_fragments(None) == []


class TestBuildStandaloneQuery:
    def test_contains_statement_and_new_question(self):
        from app.notepad.thread import build_standalone_query

        query = build_standalone_query("We seized adulterated milk.", [], "Which penalty applies?")
        assert "We seized adulterated milk." in query
        assert "Which penalty applies?" in query

    def test_contains_prior_turns_in_order(self):
        from app.notepad.thread import build_standalone_query

        prior = [("q-one", "a-one"), ("q-two", "a-two")]
        query = build_standalone_query("statement", prior, "q-new")
        assert "q-one" in query
        assert "a-one" in query
        assert "q-two" in query
        assert "a-two" in query
        assert "q-new" in query
        # prior exchanges precede the new question
        assert query.index("a-two") < query.index("q-new")

    def test_keeps_only_the_last_five_turns(self):
        from app.notepad.thread import build_standalone_query

        prior = [(f"turn-{i}-q", f"turn-{i}-a") for i in range(1, 8)]  # 7 turns
        query = build_standalone_query("statement", prior, "q-new")
        assert "turn-1-q" not in query
        assert "turn-2-q" not in query
        assert "turn-3-q" in query  # last five: 3..7
        assert "turn-7-a" in query

    def test_truncates_long_prior_answers(self):
        from app.notepad.thread import _ANSWER_PREVIEW_CHARS, build_standalone_query

        long_answer = "x" * 5000
        query = build_standalone_query("statement", [("q", long_answer)], "q-new")
        assert ("x" * _ANSWER_PREVIEW_CHARS) in query
        assert ("x" * (_ANSWER_PREVIEW_CHARS + 1)) not in query

    def test_empty_prior_turns_still_produces_a_usable_query(self):
        from app.notepad.thread import build_standalone_query

        query = build_standalone_query("statement", [], "q")
        assert "Earlier exchanges" not in query  # section omitted, not empty-labelled


class TestGetPipeline:
    def test_pipeline_fn_resolves_dependencies_at_call_time(self, monkeypatch):
        """FR-4/FR-7: late binding — patching the tasks module and cfg AFTER
        the pipeline is built must still be visible to the call."""
        import app.notepad.thread as thread
        import app.rag.tasks as tasks_mod
        from app.shared.config import cfg

        captured: dict = {}

        def fake_run(query, top_k=None, **kwargs):  # noqa: ARG001
            captured["query"] = query
            captured["top_k"] = top_k
            return {"answer": "stub"}

        monkeypatch.setattr(tasks_mod, "run_generation_pipeline", fake_run)
        monkeypatch.setattr(cfg, "context_max_chunks", 7)

        pipeline = thread.get_pipeline()  # built (or cached) before nothing here
        result = pipeline.run(query="what is section 55", top_k=None)  # ask() passes top_k=None

        assert result == {"answer": "stub"}
        assert captured["query"] == "what is section 55"
        assert captured["top_k"] == 7  # cfg read at call time, not at import

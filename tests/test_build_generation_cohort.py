"""Tests for the generation-cohort freezer (evaluation/build_generation_cohort.py).

The cohort is the substrate for the contrastive statutory reasoning
experiment, so the things it can get wrong matter:

* **the cohort definition.**  The plan says "filter for rows where
  ``bucket == 'generation'``".  That predicate yields **113** rows, not the
  59/60 the experiment intends -- the rest are ``evaluator_miss`` (the human
  audit said the model was *right*) and ``reference_narrow``.  The cohort must
  be ``generation AND model_wrong``, which is what
  ``evaluation.rerun_gold_in_prompt.load_targets`` returns.
* **the frozen context.**  It has to be the *packed window* the model
  actually sees, not the 500-chunk retrieval pool behind it, and it has to
  reproduce the harness byte-for-byte -- otherwise the experiment is not
  replaying the production prompt.
* **the baseline.**  A verification gate falls back to it, so it must be
  present and it must be recorded per question, with the tag that supplied
  it, so any mixing across tags is visible rather than silent.

0 LLM calls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.build_generation_cohort import (  # noqa: E402
    AUDIT_ACTIONS,
    DEFAULT_BASELINE_TAGS,
    EXPECTED_COUNT,
    _baseline_for,
    _check,
    _sha256_text,
)


class TestCohortDefinition:
    def test_the_expected_count_is_the_model_wrong_generation_slice(self):
        # The plan's "60" is the historical number for generation AND
        # model_wrong.  The live attribution has 113 rows in the generation
        # bucket, of which 59 are model_wrong.
        assert EXPECTED_COUNT == 59

    def test_the_cohort_is_the_model_wrong_generation_slice(self):
        from evaluation.rerun_gold_in_prompt import load_targets

        targets = load_targets()
        assert len(targets) == EXPECTED_COUNT
        # every cohort member is a generation failure, not an evaluator miss
        for t in targets:
            assert t["qid"]
            assert t["audit_action"] in AUDIT_ACTIONS

    def test_the_cohort_covers_all_three_audit_failure_modes(self):
        from collections import Counter

        from evaluation.rerun_gold_in_prompt import load_targets

        mix = Counter(t["audit_action"] for t in load_targets())
        assert set(mix) == set(AUDIT_ACTIONS)
        # the abstention slice is a REFUSAL failure and is 20 of 59 -- the
        # contrastive plan omits it entirely, so it must be visible here
        assert mix["abstention_gate"] >= 15


class TestFrozenArtifact:
    def test_the_frozen_cohort_reproduces_the_harness_prompt(self):
        """The frozen window must be the packed window, byte-identical."""
        from app.rag.generation.context_builder import ContextBuilder
        from evaluation.bench_p0_1_packing import (
            RAW_DIR,
            _chunks_for,
            _load_jsonl,
            _load_payload_index,
        )
        from evaluation.benchmark import load_questions

        out = ROOT / "evaluation" / "out" / "ceiling_v5" / "generation_cohort.json"
        if not out.exists():
            return  # the artifact is gitignored; skip when it has not been built
        cohort = json.loads(out.read_text(encoding="utf-8"))
        questions = {q.question_id: q for q in load_questions()}
        payload_index = _load_payload_index()
        arms = {
            str(r.get("question_id")): [str(c) for c in (r.get("chunk_ids") or [])]
            for r in _load_jsonl(RAW_DIR / "C_hybrid.jsonl")
        }
        checked = 0
        for row in cohort["questions"]:
            q = questions[row["qid"]]
            chunks = _chunks_for(arms[row["qid"]], payload_index)
            qt = q.question_types[0] if q.question_types else ""
            built = ContextBuilder(query_type=qt).build(q.question, chunks, qt)
            harness_ids = [str(c.get("chunk_id")) for c in built.citations]
            assert [c["chunk_id"] for c in row["context"]["chunks"]] == harness_ids, row["qid"]
            assert row["context"]["context_sha256"] == _sha256_text(built.context or "")
            checked += 1
        assert checked == EXPECTED_COUNT

    def test_the_frozen_window_is_smaller_than_the_retrieval_pool(self):
        out = ROOT / "evaluation" / "out" / "ceiling_v5" / "generation_cohort.json"
        if not out.exists():
            return
        cohort = json.loads(out.read_text(encoding="utf-8"))
        for row in cohort["questions"]:
            assert row["context"]["window_size"] <= row["context"]["pool_size"]
            assert len(row["context"]["chunks"]) == row["context"]["window_size"]


class TestBaselineSelection:
    def test_the_first_tag_with_a_usable_record_wins(self):
        caches = {
            "empty": {"Q1": {"answer": "", "error": None}},
            "errored": {"Q1": {"answer": "", "error": "LLM request failed"}},
            "good": {"Q1": {"answer": "an answer", "error": None, "binary_correct": 1}},
        }
        tag, rec = _baseline_for("Q1", caches)
        assert tag == "good"
        assert rec["answer"] == "an answer"
        assert rec["binary_correct"] == 1

    def test_no_usable_baseline_returns_none(self):
        caches = {"a": {"Q1": {"answer": "", "error": "boom"}}}
        assert _baseline_for("Q1", caches) is None

    def test_the_default_tags_cover_the_cohort(self):
        assert "cond_v5" in DEFAULT_BASELINE_TAGS


class TestDriftCheck:
    def test_it_reports_drift_when_the_cohort_changes(self):
        old = {
            "questions": [{"qid": "Q1", "context": {"context_sha256": "aaa"}, "baseline": {"answer": "x"}}],
            "provenance": {"failure_attribution.json": {"sha256": "h1"}},
        }
        new = {
            "questions": [
                {"qid": "Q1", "context": {"context_sha256": "bbb"}, "baseline": {"answer": "y"}},
                {"qid": "Q2", "context": {"context_sha256": "ccc"}, "baseline": {"answer": "z"}},
            ],
            "provenance": {"failure_attribution.json": {"sha256": "h2"}},
        }
        assert _check(old, new) == 1, "a changed context, a changed baseline and an added qid are drift"

    def test_it_reports_no_drift_when_nothing_changed(self):
        cohort = {
            "questions": [{"qid": "Q1", "context": {"context_sha256": "a"}, "baseline": {"answer": "x"}}],
            "provenance": {},
        }
        assert _check(cohort, cohort) == 0


class TestSha:
    def test_it_is_stable_and_content_sensitive(self):
        assert _sha256_text("abc") == _sha256_text("abc")
        assert _sha256_text("abc") != _sha256_text("abd")

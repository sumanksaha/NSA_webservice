"""Offline instruments on the gold-in-prompt rerun harness (0 LLM calls).

The W3/W4 flags are gated on the top-lawyer rubric, the citation gate and
replicates.  The rubric reads cached answers, but the citation gate needs the
harness to *record* what the generation path actually cited — ``run_one`` now
writes a per-record ``citation`` block, and the run summary aggregates it.

These tests pin that recording at its pure seams, so a gate run cannot
silently come back as "no data", or with a denominator that differs between
arms (which would make the paired comparison meaningless).
"""

from __future__ import annotations

from types import SimpleNamespace

from evaluation.rerun_gold_in_prompt import (
    citation_metrics,
    citation_summary,
    flags_used,
    run_flags,
    structured_summary,
    structured_summary_of_run,
)


class TestCitationMetrics:
    """Per-record citation gate: cite the gold that reached the prompt."""

    def test_recall_is_gold_cited_over_gold_admitted(self):
        m = citation_metrics(["c1", "c2", "x"], ["c1", "c2", "c3"])
        assert m["gold_admitted"] == 3
        assert m["gold_cited"] == 2
        assert m["recall"] == 0.6667
        assert m["precision"] == 0.6667  # 2 gold of 3 distinct cited
        assert m["has_citation"] == 1

    def test_recall_is_none_when_no_gold_reached_the_prompt(self):
        # Undefined, not zero: on a qid whose window holds no gold, citing
        # everything would still score 0 — that is a population filter, not a
        # model property (same rule ab_baseline_citation applies).
        m = citation_metrics(["c1"], [])
        assert m["recall"] is None
        assert m["gold_admitted"] == 0

    def test_precision_is_none_when_nothing_was_cited(self):
        m = citation_metrics([], ["c1"])
        assert m["precision"] is None
        assert m["has_citation"] == 0
        assert m["recall"] == 0.0  # gold was in the prompt and was not cited

    def test_cited_ids_are_deduplicated_and_sorted(self):
        m = citation_metrics(["b", "a", "b"], [])
        assert m["cited_chunk_ids"] == ["a", "b"]
        assert m["distinct_cited"] == 2

    def test_handles_missing_citation_record_inputs(self):
        assert citation_metrics(None, None)["distinct_cited"] == 0


class TestCitationSummary:
    """Run-level aggregation: macro and micro must not be conflated."""

    @staticmethod
    def _row(qid, recall, gold_admitted, gold_cited, distinct=2):
        return {
            "qid": qid,
            "citation": {
                "gold_admitted": gold_admitted,
                "gold_cited": gold_cited,
                "distinct_cited": distinct,
                "recall": recall,
                "precision": round(gold_cited / distinct, 4) if distinct else None,
                "has_citation": int(bool(distinct)),
            },
        }

    def test_macro_and_micro_are_reported_separately(self):
        # qid A: 1 of 1 gold (perfect); qid B: 1 of 9 gold.  Macro = 0.55,
        # micro = 2/10 — a single number would hide which one moved.
        rows = [self._row("A", 1.0, 1, 1), self._row("B", 0.1111, 9, 1)]
        block = citation_summary(rows)
        assert block["n"] == 2
        assert block["mean_recall"] == 0.5555
        assert block["micro_recall"] == 0.2
        assert block["gold_admitted_total"] == 10
        assert block["gold_cited_total"] == 2

    def test_qids_without_gold_are_excluded_from_macro_but_not_counted_as_zero(self):
        rows = [self._row("A", 1.0, 1, 1), self._row("B", None, 0, 0, distinct=0)]
        block = citation_summary(rows)
        assert block["n"] == 2  # both records reported
        assert block["mean_recall"] == 1.0  # B contributes no recall, not 0.0
        assert block["micro_recall"] == 1.0

    def test_no_citation_records_yields_none(self):
        # Pre-instrumentation tags carry no citation block: the summary must
        # omit the gate rather than report 0.0 for it.
        assert citation_summary([{"qid": "A", "answer": "x"}]) is None
        assert citation_summary([]) is None


class TestStructuredSummary:
    """W3 diagnostics: is the IR real, and did the reasoner fall back?"""

    def test_none_on_the_single_pass_path(self):
        assert structured_summary(SimpleNamespace(debug={})) is None
        assert structured_summary(SimpleNamespace(debug={"structured_argument": None})) is None
        assert structured_summary(None) is None

    def test_counts_the_ir_shape(self):
        resp = SimpleNamespace(
            debug={
                "structured_argument": {
                    "applicable_provisions": ["FSS Act s31"],
                    "legal_rules": ["r1", "r2"],
                    "condition_evaluations": [{"status": "unknown"}],
                    "uncertainties": [],
                }
            }
        )
        s = structured_summary(resp)
        assert s["n_provisions"] == 1
        assert s["n_rules"] == 2
        assert s["n_conditions"] == 1
        assert s["fallback"] is False

    def test_detects_the_fail_closed_skeleton(self):
        # StructuredReasoner.reason never raises: a failed or unparseable
        # reasoner call still renders the structured prompt, over a skeleton
        # whose uncertainties say so.  A W3 arm with a high fallback count is
        # measuring the fail-closed path, not two-pass reasoning.
        for text in (
            "reasoning unparseable (x) — argument not established",
            "reasoning unavailable — argument not established",
        ):
            resp = SimpleNamespace(debug={"structured_argument": {"uncertainties": [text]}})
            assert structured_summary(resp)["fallback"] is True

    def test_run_level_block_aggregates_only_structured_records(self):
        rows = [
            {"structured": {"n_provisions": 3, "n_conditions": 1, "n_uncertainties": 0, "fallback": False}},
            {"structured": {"n_provisions": 0, "n_conditions": 0, "n_uncertainties": 1, "fallback": True}},
            {"structured": None},
            {},
        ]
        block = structured_summary_of_run(rows)
        assert block["n"] == 2
        assert block["n_fallback"] == 1
        assert block["mean_provisions"] == 1.5
        assert structured_summary_of_run([{}]) is None


class TestRunFlags:
    """Every cached tag records which flags the run actually saw."""

    def test_distinct_flag_sets_are_preserved_in_order(self):
        rows = [
            {"flags": {"structured_reasoner": True, "query_type_directives": False}},
            {"flags": {"structured_reasoner": True, "query_type_directives": False}},
            {"flags": {"structured_reasoner": False, "query_type_directives": True}},
            {"flags": None},
            {},
        ]
        assert run_flags(rows) == [
            {"structured_reasoner": True, "query_type_directives": False},
            {"structured_reasoner": False, "query_type_directives": True},
        ]

    def test_flags_used_reads_the_live_config(self, monkeypatch):
        monkeypatch.setenv("ENABLE_STRUCTURED_REASONER", "true")
        monkeypatch.setenv("RAG_QUERY_TYPE_DIRECTIVES", "false")
        assert flags_used() == {"structured_reasoner": True, "query_type_directives": False}

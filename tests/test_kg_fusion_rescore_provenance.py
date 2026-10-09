"""Provenance guards on ``evaluation.ab_kg_fusion --rescore`` (fix 3).

The third measurement bug was not in the tracker — that was fixed in 7339929 —
but in the *re-measurement path*.  ``--rescore`` skipped rows it could not
re-derive and still printed a full report, so a shard written before answer
persistence produced citation columns that were the old tracker's values,
formatted exactly like a fresh re-measurement.

This repo has already shipped that mistake twice: analysis §5.3 (38 evaluator
misses read as model error) and ADR-0010 §3 (an empty gold set reported as a
citation result). A third instance, silently, is the same defect.

These tests pin that unrescorable input now aborts or is labelled.
"""

from __future__ import annotations

import json

from evaluation import ab_kg_fusion
from evaluation.ab_kg_fusion import _rescore_rows

_METRICS = {
    "binary_correct": 1.0,
    "answer_correctness": 0.4,
    "citation_recall": 0.0,
    "groundedness_score": 1.0,
    "hallucination_detected": 0.0,
    "n_invalid_citations": 0.0,
    "n_prompt_chunks": 10.0,
}


def _row(qid: str, *, answer: str | None, gold: list[str] | None, recall: float) -> dict:
    row = {
        "qid": qid,
        "m": {**_METRICS, "citation_recall": recall, "gold_in_prompt": 0},
        "kg": {"injected": 0, "provisions": 0},
    }
    if answer is not None:
        row["answer"] = answer
    if gold is not None:
        row["gold_chunk_ids"] = gold
    return row


def _shard(rows: list[dict]) -> str:
    return json.dumps({"kg_off": {r["qid"]: r for r in rows}, "kg_on": {r["qid"]: r for r in rows}})


class TestRescoreProvenance:
    def test_rescorable_row_is_recomputed(self):
        # Prompt pool is [gold, other]; the answer cites prompt position 1.
        rows = [_row("Q1", answer="Penalty applies. [Source 1]", gold=["gold"], recall=0.0)]
        rows[0]["pool_chunk_ids"] = ["gold", "other"]
        out, skipped = _rescore_rows(rows)
        assert skipped == []
        assert out[0]["m"]["citation_recall"] == 1.0
        assert out[0]["m"]["gold_in_prompt"] == 1
        assert out[0].get("_rescored") is True

    def test_row_without_stored_answer_is_reported_skipped(self):
        rows = [_row("Q1", answer=None, gold=None, recall=0.42)]
        out, skipped = _rescore_rows(rows)
        assert skipped == ["Q1"]
        # Value is left untouched — but the caller is now told, which is the
        # whole point: it must not be reported as re-measured.
        assert out[0]["m"]["citation_recall"] == 0.42
        assert not out[0].get("_rescored")

    def test_row_without_pool_cannot_be_rescored(self):
        """No prompt ordering => marker numbers can't map to chunk ids."""
        rows = [_row("Q1", answer="text [Source 1]", gold=["gold"], recall=0.9)]
        out, skipped = _rescore_rows(rows)
        assert skipped == ["Q1"]
        assert out[0]["m"]["citation_recall"] == 0.9


class TestReportLabelsStaleRows:
    @staticmethod
    def _arms():
        good = _row("Q1", answer="x [Source 1]", gold=["gold"], recall=0.0)
        good["pool_chunk_ids"] = ["gold"]
        stale = _row("Q2", answer=None, gold=None, recall=0.7)
        # Both qids in both arms, so both are paired and both reach the
        # aggregate — an unpaired stale row cannot move a reported number.
        return [dict(good), dict(stale)], [dict(good), dict(stale)]

    def test_stale_rows_are_named_in_limitations(self, monkeypatch, tmp_path):
        out_file = tmp_path / "report.json"
        monkeypatch.setattr(ab_kg_fusion, "OUT_FILE", out_file)
        rc = ab_kg_fusion._report(*self._arms(), rescored=1, unrescorable=["Q2"])
        assert rc == 0
        out = json.loads(out_file.read_text())
        assert out["rescored_rows"] == 1
        assert out["unrescorable_qids"] == ["Q2"]
        joined = " ".join(out["limitations"])
        assert "STALE" in joined
        assert "Q2" in joined

    def test_clean_rescore_states_provenance(self, monkeypatch, tmp_path):
        out_file = tmp_path / "report.json"
        monkeypatch.setattr(ab_kg_fusion, "OUT_FILE", out_file)
        rc = ab_kg_fusion._report(*self._arms(), rescored=2, unrescorable=[])
        assert rc == 0
        out = json.loads(out_file.read_text())
        assert out["unrescorable_qids"] == []
        joined = " ".join(out["limitations"])
        assert "Rescored offline through the current CitationTracker" in joined
        # And it must say what the rescore does NOT cover.
        assert "binary_correct" in joined
        assert "STALE" not in joined

    def test_unrescorable_aborts_before_reporting(self, capsys, monkeypatch, tmp_path):
        """The `--rescore` entry point must refuse when nothing is rescored."""
        import sys

        from evaluation import ab_kg_fusion

        for name in ("off.jsonl", "on.jsonl"):
            (tmp_path / name).write_text(_shard([_row("Q1", answer=None, gold=None, recall=0.7)]))
        monkeypatch.setattr(
            sys,
            "argv",
            ["ab_kg_fusion", "--rescore", str(tmp_path / "off.jsonl"), str(tmp_path / "on.jsonl")],
        )
        rc = ab_kg_fusion.main()
        assert rc == 1
        err = capsys.readouterr().err
        assert "ABORT" in err
        assert "OLD CitationTracker" in err

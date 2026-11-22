"""Tests for the noise-floor harness (evaluation/noise_floor.py).

The harness exists because every prompt comparison in this repo is judged
against a fixed threshold -- ``SOFT_TIE = 0.02``, ``NOISE_FLOOR_FLIPS = 4``,
the brevity experiment's ">= 15% length reduction" -- and a threshold is only
meaningful against the spread two runs of the *same* configuration produce.

These tests are hermetic: they build their own results file rather than
depending on the gitignored ``evaluation/out/`` artifacts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.noise_floor import _load_brevity_file, _paired  # noqa: E402


def _row(qid: str, variant: str, *, length: int, soft: float, coverage: float, binary: int = 0) -> dict:
    return {
        "qid": qid,
        "variant": variant,
        "answer": "x" * length,
        "answer_length": length,
        "answer_correctness": soft,
        "answer_coverage": coverage,
        "answer_jaccard": soft - coverage,
        "binary_correct": binary,
        "abstained": False,
        "error": None,
    }


def _write(tmp_path: Path, rows: list[dict]) -> str:
    path = tmp_path / "brevity.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return str(path)


class TestPairedSpread:
    def test_it_reports_the_per_metric_spread(self, tmp_path):
        rows = [
            _row("Q1", "a", length=1000, soft=0.40, coverage=0.60),
            _row("Q1", "b", length=1200, soft=0.44, coverage=0.64),
            _row("Q2", "a", length=2000, soft=0.30, coverage=0.50),
            _row("Q2", "b", length=1600, soft=0.28, coverage=0.48),
        ]
        _load_brevity_file(_write(tmp_path, rows))
        p = _paired("a", "b")
        assert p["n"] == 2
        assert p["answer_correctness"]["mean"] == 0.01
        assert p["answer_coverage"]["mean"] == 0.01
        # length deltas are +200 (Q1) and -400 (Q2), so the mean is -100
        assert p["answer_length"]["mean"] == -100.0
        # relative length deltas are +20% and -20%, so the mean is 0
        assert p["answer_length_pct"]["mean"] == 0.0

    def test_error_records_are_dropped_not_scored_as_zero(self, tmp_path):
        # A failed call leaves an empty answer: soft 0, coverage 0, length 0.
        # That is a *missing* observation, not a low one -- including it would
        # bias the spread downward and make every candidate look better.
        rows = [
            _row("Q1", "a", length=1000, soft=0.40, coverage=0.60),
            _row("Q1", "b", length=1000, soft=0.40, coverage=0.60),
            _row("Q2", "a", length=1000, soft=0.40, coverage=0.60),
            dict(_row("Q2", "b", length=0, soft=0.0, coverage=0.0), error="LLM request failed"),
        ]
        _load_brevity_file(_write(tmp_path, rows))
        p = _paired("a", "b")
        assert p["n"] == 1, "the errored record must not be paired"
        assert p["answer_correctness"]["mean"] == 0.0

    def test_binary_flips_are_counted_as_gained_and_lost(self, tmp_path):
        rows = [
            _row("Q1", "a", length=100, soft=0.6, coverage=0.7, binary=0),
            _row("Q1", "b", length=100, soft=0.6, coverage=0.7, binary=1),
            _row("Q2", "a", length=100, soft=0.6, coverage=0.7, binary=1),
            _row("Q2", "b", length=100, soft=0.6, coverage=0.7, binary=0),
        ]
        _load_brevity_file(_write(tmp_path, rows))
        p = _paired("a", "b")
        assert p["binary"] == {"gained": 1, "lost": 1, "net": 0}

    def test_a_tag_with_no_overlap_is_reported_not_raised(self, tmp_path):
        rows = [_row("Q1", "a", length=10, soft=0.1, coverage=0.1)]
        _load_brevity_file(_write(tmp_path, rows))
        assert "error" in _paired("a", "missing_tag")


class TestBrevityFileLoading:
    def test_variants_become_independent_tags(self, tmp_path):
        rows = [
            _row("Q1", "alpha", length=10, soft=0.1, coverage=0.1),
            _row("Q1", "beta", length=20, soft=0.2, coverage=0.2),
        ]
        _load_brevity_file(_write(tmp_path, rows))
        p = _paired("alpha", "beta")
        assert p["n"] == 1
        assert p["answer_length"]["mean"] == 10.0

"""SPEC-3: Verifier-gated retry replacement guard tests.

The selector must:
- Adopt retry when it is strictly better on binary correctness.
- Adopt retry when soft score improves enough without precision/groundedness regression.
- Keep baseline on ties, regressions, or insufficient improvement.
- Never produce a result worse than the best arm.

No network, no LLM, no Qdrant.
"""
from __future__ import annotations

from app.rag.agent.retry_guard import select_arm


def _scorecard(
    binary: int = 0,
    soft: float = 0.0,
    precision: float = 0.0,
    groundedness: float = 0.0,
) -> dict:
    return {
        "binary_correct": binary,
        "answer_correctness": soft,
        "citation_precision": precision,
        "groundedness_score": groundedness,
    }


class TestRetryAdoption:
    """Retry is selected only when measurably better."""

    def test_q003_shape_selects_retry(self):
        """Q003: baseline abstains (binary=0), retry answers correctly (binary=1)."""
        baseline = _scorecard(binary=0, soft=0.267, precision=0.0, groundedness=0.8)
        retry = _scorecard(binary=1, soft=0.617, precision=0.37, groundedness=0.9)
        result = select_arm(baseline, retry)
        assert result["selected_arm"] == "retry"
        assert result["reason"] == "binary_correct"

    def test_q132_shape_keeps_baseline(self):
        """Q132: baseline correct (binary=1), retry wrong (binary=0)."""
        baseline = _scorecard(binary=1, soft=0.400, precision=0.0, groundedness=0.8)
        retry = _scorecard(binary=0, soft=0.398, precision=0.37, groundedness=0.9)
        result = select_arm(baseline, retry)
        assert result["selected_arm"] == "baseline"
        assert result["reason"] == "baseline_preferred"

    def test_exact_tie_keeps_baseline(self):
        """Exact tie on both binary and soft score -> keep baseline."""
        baseline = _scorecard(binary=0, soft=0.5, precision=0.3, groundedness=0.8)
        retry = _scorecard(binary=0, soft=0.5, precision=0.3, groundedness=0.8)
        result = select_arm(baseline, retry)
        assert result["selected_arm"] == "baseline"

    def test_soft_delta_below_threshold_keeps_baseline(self):
        """Soft improvement of 0.03 (< 0.05) -> keep baseline."""
        baseline = _scorecard(binary=0, soft=0.5, precision=0.3, groundedness=0.8)
        retry = _scorecard(binary=0, soft=0.53, precision=0.3, groundedness=0.8)
        result = select_arm(baseline, retry)
        assert result["selected_arm"] == "baseline"

    def test_soft_delta_at_threshold_selects_retry(self):
        """Soft improvement of exactly 0.05 -> select retry."""
        baseline = _scorecard(binary=0, soft=0.5, precision=0.3, groundedness=0.8)
        retry = _scorecard(binary=0, soft=0.55, precision=0.3, groundedness=0.8)
        result = select_arm(baseline, retry)
        assert result["selected_arm"] == "retry"
        assert result["reason"] == "soft_delta"

    def test_groundedness_regression_beyond_slack_keeps_baseline(self):
        """Soft improves but groundedness drops by 0.15 (> 0.10 slack) -> keep baseline."""
        baseline = _scorecard(binary=0, soft=0.5, precision=0.3, groundedness=0.8)
        retry = _scorecard(binary=0, soft=0.6, precision=0.3, groundedness=0.65)
        result = select_arm(baseline, retry)
        assert result["selected_arm"] == "baseline"

    def test_groundedness_regression_within_slack_selects_retry(self):
        """Soft improves and groundedness drops by 0.08 (< 0.10 slack) -> select retry."""
        baseline = _scorecard(binary=0, soft=0.5, precision=0.3, groundedness=0.8)
        retry = _scorecard(binary=0, soft=0.6, precision=0.3, groundedness=0.72)
        result = select_arm(baseline, retry)
        assert result["selected_arm"] == "retry"

    def test_precision_regression_keeps_baseline(self):
        """Soft improves but precision regresses -> keep baseline."""
        baseline = _scorecard(binary=0, soft=0.5, precision=0.5, groundedness=0.8)
        retry = _scorecard(binary=0, soft=0.6, precision=0.3, groundedness=0.8)
        result = select_arm(baseline, retry)
        assert result["selected_arm"] == "baseline"


class TestMissingScorecard:
    """Missing or malformed scorecards default to baseline (fail-safe)."""

    def test_missing_retry_scorecard_keeps_baseline(self):
        baseline = _scorecard(binary=0, soft=0.5)
        retry = {}
        result = select_arm(baseline, retry)
        assert result["selected_arm"] == "baseline"

    def test_missing_baseline_scorecard_keeps_baseline(self):
        baseline = {}
        retry = _scorecard(binary=1, soft=0.9)
        result = select_arm(baseline, retry)
        # baseline binary=0 (default), retry binary=1 -> retry wins
        assert result["selected_arm"] == "retry"

    def test_partial_scorecard_uses_defaults(self):
        baseline = _scorecard(binary=0, soft=0.5, precision=0.3, groundedness=0.8)
        retry = {"binary_correct": 0, "answer_correctness": 0.6}
        result = select_arm(baseline, retry)
        # precision defaults to 0.0, groundedness defaults to 0.0
        # soft_delta=0.1 >= 0.05, prec_delta=-0.3 < 0 -> keep baseline
        assert result["selected_arm"] == "baseline"


class TestNeverWorseThanBest:
    """Property: selected binary_correct >= max(baseline, retry) binary."""

    def test_never_worse_than_best_arm(self):
        """Over a range of scorecards, the selected arm is never worse than the best."""
        for b_bin in (0, 1):
            for r_bin in (0, 1):
                for b_soft in (0.0, 0.3, 0.5, 0.7, 1.0):
                    for r_soft in (0.0, 0.3, 0.5, 0.7, 1.0):
                        baseline = _scorecard(binary=b_bin, soft=b_soft)
                        retry = _scorecard(binary=r_bin, soft=r_soft)
                        result = select_arm(baseline, retry)
                        selected_binary = b_bin if result["selected_arm"] == "baseline" else r_bin
                        best_binary = max(b_bin, r_bin)
                        assert selected_binary >= best_binary, (
                            f"b_bin={b_bin} r_bin={r_bin} b_soft={b_soft} r_soft={r_soft} "
                            f"selected={result['selected_arm']} selected_binary={selected_binary} "
                            f"best_binary={best_binary}"
                        )


class TestGuardedReport:
    """The Stage-2 eval mirror must produce flips_down == 0 by construction."""

    def _paired(self, pairs):
        B, R = {}, {}
        for q, (bb, bs, bp, bg, rb, rs, rp, rg) in pairs.items():
            B[q] = {"m": {"binary_correct": bb, "answer_correctness": bs, "citation_precision": bp, "groundedness_score": bg}}
            R[q] = {"m": {"binary_correct": rb, "answer_correctness": rs, "citation_precision": rp, "groundedness_score": rg}}
        return B, R

    def test_guarded_never_produces_flip_down(self):
        # Q003: retry wins (up).  Q132: retry loses (would be a down).
        # Q050: tie.  Q060: retry marginally better on soft but ties binary.
        pairs = {
            "Q003": (0, 0.267, 0.0, 0.8, 1, 0.617, 0.37, 0.9),
            "Q132": (1, 0.400, 0.0, 0.8, 0, 0.398, 0.37, 0.9),
            "Q050": (0, 0.50, 0.10, 0.80, 0, 0.50, 0.10, 0.80),
            "Q060": (0, 0.40, 0.10, 0.80, 0, 0.46, 0.10, 0.80),
        }
        from evaluation.ab_targeted_retry_answers_fast import guarded_report

        B, R = self._paired(pairs)
        g = guarded_report(B, R, sorted(pairs))
        assert g["flips_down"] == [], f"guard produced flip-downs: {g['flips_down']}"
        assert g["flips_up"] == ["Q003"], g["flips_up"]
        # Q060 soft delta 0.06 >= 0.05 with no precision/groundedness regression -> retry
        assert g["selected_retry"] == ["Q003", "Q060"], g["selected_retry"]

    def test_guarded_reports_per_qid_deltas(self):
        from evaluation.ab_targeted_retry_answers_fast import guarded_report

        B, R = self._paired({"Q003": (0, 0.267, 0.0, 0.8, 1, 0.617, 0.37, 0.9)})
        g = guarded_report(B, R, ["Q003"])
        row = g["per_question"]["Q003"]
        assert row["selected_arm"] == "retry"
        assert row["baseline_binary_correct"] == 0
        assert row["retry_binary_correct"] == 1
        assert row["selected_binary_correct"] == 1
        assert round(row["soft_delta"], 4) == 0.35

    def test_guarded_aggregate_matches_retry_where_selected(self):
        from evaluation.ab_targeted_retry_answers_fast import guarded_report

        B, R = self._paired({"Q003": (0, 0.267, 0.0, 0.8, 1, 0.617, 0.37, 0.9)})
        g = guarded_report(B, R, ["Q003"])
        assert g["aggregate"]["binary_correct"] == 1
        assert g["aggregate"]["answer_correctness"] == 0.617


class TestDeltasEmitted:
    """The selector emits deciding deltas for logging."""

    def test_deltas_present_in_result(self):
        baseline = _scorecard(binary=0, soft=0.4, precision=0.2, groundedness=0.7)
        retry = _scorecard(binary=1, soft=0.8, precision=0.6, groundedness=0.9)
        result = select_arm(baseline, retry)
        assert "soft_delta" in result
        assert "precision_delta" in result
        assert "groundedness_delta" in result
        assert result["soft_delta"] == 0.4
        assert result["precision_delta"] == 0.4
        assert result["groundedness_delta"] == 0.2

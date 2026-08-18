"""Temperature calibration for CE-v2 cross-encoder scores (CV2 P3).

Fits a temperature parameter T on the *test-split* pairs that minimizes
log-loss on min-max-normalized scores, then verifies rank-invariance.

Scope (re-scoped per G3):
  P3 **does not fix accuracy** — temperature scaling is order-preserving for
  positive T (score/T keeps every pairwise comparison identical).  It only
  normalises the *scale* for downstream consumers that use absolute scores:

  - ``EnsembleReranker`` CE bonus min-max normalisation
  - ``sweep_ce_weights.py`` weight sweep
  - Any threshold-based filtering

The calibration report prints T, the score spread before/after, and confirms
pairwise accuracy is unchanged.

Usage:
    python -m evaluation.calibrate_ce
    python -m evaluation.calibrate_ce --model evaluation/out/models/legal_ce_v2_K500
    python -m evaluation.calibrate_ce --model-v2 <path>  # override
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.config import CACHE_DIR

PAIRS_FILE = CACHE_DIR / "pairwise_training_v2.jsonl"
SPLIT_FILE = CACHE_DIR / "pairwise_train_split.json"
MODELS_DIR = PROJECT_ROOT / "evaluation" / "out" / "models"
DEFAULT_MODEL = MODELS_DIR / "legal_ce_v2_K500"
REPORT_FILE = CACHE_DIR / "calibration_report.json"

MAX_LEN = 256
BATCH = 64


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #


def load_test_pairs() -> list[dict]:
    """Return test-split pairwise records."""
    split = json.loads(SPLIT_FILE.read_text(encoding="utf-8")) if SPLIT_FILE.exists() else {}
    test_qids = set(split.get("test_qids", []))
    pairs: list[dict] = []
    with PAIRS_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rec = json.loads(line)
                if rec.get("question_id") in test_qids:
                    pairs.append(rec)
    return pairs


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


def score_test_pairs(model_path: Path) -> list[tuple[float, float, dict]]:
    """Score all test pairs, returning (pos_score, neg_score, record) tuples."""
    from sentence_transformers import CrossEncoder

    pairs = load_test_pairs()
    if not pairs:
        print("ERROR: no test pairs found", file=sys.stderr)
        return []

    model = CrossEncoder(str(model_path), max_length=MAX_LEN)

    results: list[tuple[float, float, dict]] = []
    # Score in batches of (query, positive) and (query, negative)
    for i in range(0, len(pairs), BATCH):
        batch = pairs[i : i + BATCH]
        pos_pairs = [(p["query"], p["positive"]) for p in batch]
        neg_pairs = [(p["query"], p["negative"]) for p in batch]
        pos_scores = model.predict(pos_pairs, batch_size=BATCH)
        neg_scores = model.predict(neg_pairs, batch_size=BATCH)
        for p, ps, ns in zip(batch, pos_scores, neg_scores, strict=False):
            results.append((float(ps), float(ns), p))

    return results


# --------------------------------------------------------------------------- #
# Accuracy helpers
# --------------------------------------------------------------------------- #


def pairwise_accuracy(scored: list[tuple[float, float, dict]]) -> float:
    """Fraction of pairs where pos_score > neg_score."""
    if not scored:
        return 0.0
    correct = sum(1 for ps, ns, _ in scored if ps > ns)
    return correct / len(scored)


def mean_margin(scored: list[tuple[float, float, dict]]) -> float:
    """Mean margin (pos_score - neg_score)."""
    if not scored:
        return 0.0
    return sum(ps - ns for ps, ns, _ in scored) / len(scored)


# --------------------------------------------------------------------------- #
# Temperature fitting
# --------------------------------------------------------------------------- #


def fit_temperature(scored: list[tuple[float, float, dict]], lr: float = 0.01, iters: int = 1000) -> float:
    """Fit temperature T via gradient descent on log-loss.

    Loss = -mean(log(sigmoid((pos - neg) / T)))

    T > 1  →  softens scores  (reduces spread)
    T < 1  →  sharpens scores (increases spread)
    T = 1  →  no change

    Returns the optimal T (> 0).
    """
    # Min-max normalise scores across all pairs
    all_scores = [s for ps, ns, _ in scored for s in (ps, ns)]
    s_min, s_max = min(all_scores), max(all_scores)
    if s_max == s_min:
        return 1.0  # all scores identical — no calibration possible

    norm_scored = [((ps - s_min) / (s_max - s_min), (ns - s_min) / (s_max - s_min), p) for ps, ns, p in scored]

    T = 1.0
    for _ in range(iters):
        # Gradient of log-loss w.r.t. T
        grad = 0.0
        loss = 0.0
        for pn, nn, _ in norm_scored:
            z = (pn - nn) / T
            # sigmoid(z) = 1 / (1 + exp(-z)), clamp for numerical safety
            sig = 1.0 / (1.0 + math.exp(-max(-500, min(500, z))))
            loss += -math.log(max(sig, 1e-10))
            # d/dT [log sigma(z/T)] = -(pn - nn) / (T^2) * (1 - sigma(z/T))
            grad += -(pn - nn) / (T * T) * (1.0 - sig)
        grad /= len(norm_scored)
        T = T - lr * grad
        T = max(T, 0.01)  # keep T positive

    return T


# --------------------------------------------------------------------------- #
# Min-max normalisation helper
# --------------------------------------------------------------------------- #


def _minmax(values: list[float]) -> list[float]:
    """Min-max normalise a list of floats to [0, 1]."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description="Temperature calibration for CE-v2 scores")
    parser.add_argument(
        "--model",
        type=Path,
        default=None,
        help=f"Path to the model directory (default: {DEFAULT_MODEL})",
    )
    args = parser.parse_args()

    model_path = args.model or DEFAULT_MODEL
    if not model_path.exists():
        print(f"ERROR: model not found at {model_path}", file=sys.stderr)
        return 1

    print(f"Loading model from {model_path} ...")
    scored = score_test_pairs(model_path)
    if not scored:
        return 1

    n_pairs = len(scored)
    acc_before = pairwise_accuracy(scored)
    margin_before = mean_margin(scored)

    print(f"\nTest pairs: {n_pairs}")
    print(f"Pairwise accuracy (pre-calibration): {acc_before:.4f}")
    print(f"Mean margin (pre-calibration):       {margin_before:.4f}")

    # Score spread (before)
    all_scores = [s for ps, ns, _ in scored for s in (ps, ns)]
    spread_before = max(all_scores) - min(all_scores)
    print(f"Score spread (pre-calibration):      {spread_before:.4f}")

    # Fit temperature
    print("\nFitting temperature T ...")
    T = fit_temperature(scored)
    print(f"Optimal T = {T:.4f}")

    # Apply calibration
    scored_cal = [(ps / T, ns / T, p) for ps, ns, p in scored]
    acc_after = pairwise_accuracy(scored_cal)
    margin_after = mean_margin(scored_cal)

    all_scores_cal = [s for ps, ns, _ in scored_cal for s in (ps, ns)]
    spread_after = max(all_scores_cal) - min(all_scores_cal)

    print(f"\nPairwise accuracy (post-calibration): {acc_after:.4f}")
    print(f"Mean margin (post-calibration):       {margin_after:.4f}")
    print(f"Score spread (post-calibration):      {spread_after:.4f}")

    # Rank-invariance check
    rank_invariant = abs(acc_before - acc_after) < 1e-10
    print(f"\nRank-invariant (accuracy unchanged): {'YES ✓' if rank_invariant else 'NO ✗'}")

    if not rank_invariant:
        print("WARNING: Temperature scaling should be rank-invariant for positive T.", file=sys.stderr)

    # Per-query breakdown
    from collections import defaultdict

    per_query: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for ps, ns, p in scored:
        per_query[p["question_id"]].append((ps, ns))

    print("\nPer-query accuracy (pre / post):")
    for qid in sorted(per_query):
        q_pairs = per_query[qid]
        pre = sum(1 for p, n in q_pairs if p > n) / len(q_pairs)
        post = sum(1 for p, n in q_pairs if p / T > n / T) / len(q_pairs)
        marker = "" if pre == post else " ← changed"
        print(f"  {qid}: {pre:.4f} / {post:.4f} ({len(q_pairs)} pairs){marker}")

    # Downstream consumer note
    print("\n--- What temperature calibration buys ---")
    print(f"T = {T:.4f} {'softens' if T > 1 else 'sharpens'} scores by {abs(T - 1):.2%}")
    print(f"Score spread: {spread_before:.4f} → {spread_after:.4f} ({spread_after / spread_before:.2f}×)")
    print("Consumers affected:")
    print("  • EnsembleReranker CE bonus min-max normalisation")
    print("  • sweep_ce_weights.py weight sweep")
    print("  • Any threshold-based filtering")
    print("Ranking / pairwise accuracy: UNCHANGED (by construction)")

    # Save report
    report = {
        "model": str(model_path),
        "n_pairs": n_pairs,
        "T": round(T, 6),
        "accuracy_pre": round(acc_before, 6),
        "accuracy_post": round(acc_after, 6),
        "margin_pre": round(margin_before, 6),
        "margin_post": round(margin_after, 6),
        "spread_pre": round(spread_before, 6),
        "spread_post": round(spread_after, 6),
        "rank_invariant": rank_invariant,
    }
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport saved to {REPORT_FILE}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

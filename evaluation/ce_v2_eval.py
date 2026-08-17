"""CE V2 EVAL - pairwise + ranking regression harness (Step 0 rebuild, 2026-08-17).

Rebuild of the deleted scratch script whose output is preserved in
``evaluation/out/ce_v2_eval_output.log`` (the original crashed with a
ZeroDivisionError on the empty "Straightforward" bucket).  This committed
version is crash-free and reproducible:

  pairwise metrics  accuracy (pos > neg) + margin over the 2,362 test pairs
  ranking metrics   per-query R@1/R@5/R@10/R@20, MRR@10, nDCG@10 over each
                    test question's candidate pool (golds + negatives)
  bootstrap CIs     95% CIs over queries (1000 resamples) for R@10, R@20,
                    MRR@10, nDCG@10
  breakdowns        per-domain (gold-unit family), per-negative-tier,
                    per-difficulty (benchmark difficulty, normalized)
  output           evaluation/out/cache/ce_v2_eval.json

Scores are cached per (query, chunk) to ``ce_v2_scores_{v1,v2}.jsonl`` so the
error-analysis script and later regression runs skip the ~2-4 min CPU scoring.
The score cache is keyed by a content hash of the pair + split files, so a
dataset rebuild invalidates it automatically.

Deliberate divergences from the original (source script/data gone, noted in the
baseline JSON):

* "By Query Difficulty" - the original reported medium=1200 / hard=1162 pairs;
  the pair records carry no difficulty field and benchmark difficulty
  (HARD/MODERATE/ADVERSARIAL) does not reproduce those buckets, so we use the
  normalized benchmark difficulty per question instead.
* "Ambiguous vs Straightforward" - the original's per-question ambiguity labels
  are gone; the section is kept with a zero-division guard and reports 0 pairs
  when no label source is provided (--ambiguous-qids).

Usage:
    python -m evaluation.ce_v2_eval                       # score + evaluate
    python -m evaluation.ce_v2_eval --no-score            # reuse score cache
    python -m evaluation.ce_v2_eval --candidate-pool mining   # negatives from
                                  # the mining file instead of the pair records
    python -m evaluation.ce_v2_eval --freeze-baseline     # write the frozen
                                  # regression baseline (after error analysis)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.config import CACHE_DIR

PAIRS_FILE = CACHE_DIR / "pairwise_training_v2.jsonl"
SPLIT_FILE = CACHE_DIR / "pairwise_train_split.json"
MINING_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "hard_negative_mining.jsonl"
MODELS_DIR = PROJECT_ROOT / "evaluation" / "out" / "models"
MODELS = {
    "v1": MODELS_DIR / "legal_ce_v1",
    "v2": MODELS_DIR / "legal_ce_v2_K500",
}
EVAL_OUT = CACHE_DIR / "ce_v2_eval.json"
#: Canonical (committed) frozen baseline that the regression gate compares against.
BASELINE_FILE = PROJECT_ROOT / "evaluation" / "ce_v2_baseline.json"

# Model overrides (--model-v1/--model-v2) + model-identity-tagged score cache.
_MODEL_OVERRIDES: dict[str, Path] = {}


def _model_path(model_key: str) -> Path:
    """Resolve a model key ('v1'/'v2') honoring --model-v1/--model-v2 overrides."""
    return _MODEL_OVERRIDES.get(model_key) or MODELS[model_key]


def _model_tag(path: Path) -> str:
    """Short identity tag for a model dir (path + checkpoint mtime/size).

    Re-keying the score cache on the checkpoint identity is essential: a
    retrain in place (``train_legal_ce_v2 --fresh``) overwrites the same
    directory, and the per-(query, chunk) score cache would otherwise serve
    the OLD model's scores - silently invalidating the gate.
    """
    h = hashlib.sha256(str(path).encode("utf-8"))
    for name in ("config.json", "model.safetensors", "pytorch_model.bin"):
        p = path / name
        try:
            st = p.stat()
            h.update(f"{name}|{st.st_mtime_ns}|{st.st_size}".encode())
        except OSError:
            pass
    return h.hexdigest()[:12]


def _score_cache_path(model_key: str) -> Path:
    """Score-cache file for a model key, tagged by the model's identity."""
    return CACHE_DIR / f"ce_v2_scores_{model_key}_{_model_tag(_model_path(model_key))}.jsonl"

MAX_LEN = 256
BATCH = 64
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 20260817
SEED = 20260817

#: Rank cutoffs reported by the original harness.
RANK_KS = (1, 5, 10, 20)
MRR_K = 10
NDCG_K = 10


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def _pairs_hash() -> str:
    """Content hash of the pair + split files (invalidates the score cache)."""
    h = hashlib.sha256()
    for f in (PAIRS_FILE, SPLIT_FILE):
        h.update(str(f).encode("utf-8"))
        h.update(f.read_bytes() if f.exists() else b"")
    return h.hexdigest()[:16]


def load_test_pairs() -> list[dict]:
    """Return the test-split pairwise records (question-id split)."""
    split = json.loads(SPLIT_FILE.read_text(encoding="utf-8")) if SPLIT_FILE.exists() else {}
    test_qids = set(split.get("test_qids", []))
    pairs = []
    with PAIRS_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rec = json.loads(line)
                if rec.get("question_id") in test_qids:
                    pairs.append(rec)
    return pairs


def load_mining() -> dict[str, dict]:
    """Return per-question mining records (positives + negatives + gold ids)."""
    recs: dict[str, dict] = {}
    if MINING_FILE.exists():
        with MINING_FILE.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    recs[rec["question_id"]] = rec
    return recs


def load_questions() -> dict[str, Any]:
    """Return benchmark questions keyed by id (domain/difficulty metadata)."""
    from evaluation.benchmark import load_questions as _load

    return {q.question_id: q for q in _load()}


def domain_of(pair: dict) -> str:
    """Per-pair domain = gold-unit family prefix (reproduces the original table)."""
    gu = str(pair.get("gold_unit") or "")
    if ":" in gu:
        return gu.split(":", 1)[0]
    return gu or "?"


def difficulty_of(q: Any) -> str:
    """Normalized benchmark difficulty (HARD -> hard, MODERATE -> moderate, ...)."""
    return str(q.difficulty).lower() if q is not None else "?"


# --------------------------------------------------------------------------- #
# Scoring (cached)
# --------------------------------------------------------------------------- #
def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _score_key(pair: dict, role: str, text: str) -> str:
    return f"{pair['question_id']}|{role}|{_text_hash(text)}"


def _load_score_cache(model_key: str) -> dict[str, float]:
    out: dict[str, float] = {}
    path = _score_cache_path(model_key)
    if path.exists():
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if line:
                rec = json.loads(line)
                out[rec["key"]] = float(rec["score"])
    return out


def _write_score_cache(model_key: str, scores: dict[str, float]) -> None:
    path = _score_cache_path(model_key)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for key in sorted(scores):
            fh.write(json.dumps({"key": key, "score": round(scores[key], 6)}) + "\n")
    tmp.replace(path)


def score_pairs_cached(pairs: list[dict], model_key: str, force: bool = False) -> dict[str, float]:
    """Score every distinct (query, chunk) in *pairs* with one model.

    Returns a ``{score_key: score}`` dict.  Cached on disk keyed by the pair +
    split content hash; ``force`` rescans the cache when the data changed but
    the hash file is stale.
    """
    from evaluation.ranking_loss_trainer import configure_threads

    configure_threads(4)

    cache = _load_score_cache(model_key)
    if force:
        cache = {}

    todo: list[tuple[str, str, str]] = []  # (key, query, text)
    seen: set[str] = set()
    for pair in pairs:
        for role, field in (("pos", "positive"), ("neg", "negative")):
            text = str(pair.get(field) or "")
            key = _score_key(pair, role, text)
            if key not in cache and key not in seen:
                seen.add(key)
                todo.append((key, pair["query"], text))

    if todo:
        from sentence_transformers import CrossEncoder

        model = CrossEncoder(str(_model_path(model_key)), max_length=MAX_LEN)
        for i in range(0, len(todo), BATCH):
            chunk = todo[i : i + BATCH]
            scores = model.predict([(q, t) for _k, q, t in chunk], batch_size=BATCH)
            for (key, _q, _t), sc in zip(chunk, scores, strict=False):
                cache[key] = float(sc)
        _write_score_cache(model_key, cache)
    return cache


# --------------------------------------------------------------------------- #
# Candidate pools + ranking metrics
# --------------------------------------------------------------------------- #
def build_candidates(
    pairs_by_qid: dict[str, list[dict]],
    mining: dict[str, dict],
    mode: str,
) -> dict[str, dict[str, Any]]:
    """Build each test question's candidate pool: golds + negatives.

    ``mode="pairs"``  negatives = distinct neg_chunk_id in the question's pair
                      records (what the original scored); golds = the mining
                      record's positives (capped at 8, as in the pair builder).
    ``mode="mining"`` negatives = the mining record's full selected set.
    """
    out: dict[str, dict[str, Any]] = {}
    for qid, pairs in pairs_by_qid.items():
        rec = mining.get(qid, {})
        golds: list[tuple[str, str]] = []  # (chunk_id, text)
        seen_gold: set[str] = set()
        for pos in rec.get("positives", [])[:8]:
            cid = str(pos.get("chunk_id") or "")
            text = str(pos.get("text") or "")
            if cid and cid not in seen_gold and text:
                seen_gold.add(cid)
                golds.append((cid, text))

        negs: list[tuple[str, str]] = []
        seen_neg: set[str] = set()
        if mode == "mining":
            for neg in rec.get("negatives", []):
                cid = str(neg.get("chunk_id") or "")
                text = str(neg.get("text") or "")
                if cid and cid not in seen_neg and text:
                    seen_neg.add(cid)
                    negs.append((cid, text))
        else:
            for pair in pairs:
                cid = str(pair.get("neg_chunk_id") or "")
                text = str(pair.get("negative") or "")
                if cid and cid not in seen_neg and text:
                    seen_neg.add(cid)
                    negs.append((cid, text))

        out[qid] = {
            "golds": golds,
            "golds_ids": {cid for cid, _t in golds},
            "negatives": negs,
            "candidates": golds + negs,
        }
    return out


def ranking_metrics(
    qid: str,
    cand: dict[str, Any],
    scores: dict[str, float],
    query: str,
) -> dict[str, float]:
    """R@k, MRR@10, nDCG@10 for one model's scores over a candidate pool."""
    gold_ids = cand["golds_ids"]
    n_gold = len(gold_ids)
    # Key candidates by score-key so pair-derived texts match the cache.
    ranked = []
    for cid, text in cand["candidates"]:
        key = f"{qid}|{'pos' if cid in gold_ids else 'neg'}|{_text_hash(text)}"
        sc = scores.get(key)
        if sc is None:
            continue
        ranked.append((sc, cid))
    ranked.sort(key=lambda x: x[0], reverse=True)
    order = [cid for _sc, cid in ranked]

    n = len(order)
    if n == 0 or n_gold == 0:
        return {"n": n, "n_gold": n_gold, "r_at": {}, "mrr": 0.0, "ndcg": 0.0}

    r_at: dict[int, int] = {}
    for k in RANK_KS:
        r_at[k] = 1 if any(cid in gold_ids for cid in order[:k]) else 0

    mrr = 0.0
    for i, cid in enumerate(order[:MRR_K]):
        if cid in gold_ids:
            mrr = 1.0 / (i + 1)
            break

    dcg = 0.0
    # nDCG@10 in the original harness counts only the FIRST gold chunk, with an
    # ideal of 1.0 (single relevant doc at rank 1) - i.e. MRR with a log
    # discount.  Reproduced exactly from the original log (0.6321 / 0.6901);
    # kept verbatim so regression deltas stay comparable to the v1/v2 baseline.
    first = next((i for i, cid in enumerate(order[:NDCG_K]) if cid in gold_ids), None)
    ndcg = 1.0 / math.log2(first + 2) if first is not None else 0.0

    return {"n": n, "n_gold": n_gold, "r_at": r_at, "mrr": mrr, "ndcg": ndcg}


def _bootstrap_ci(per_q: dict[str, dict[str, dict]], metric: str) -> dict[str, list[float]]:
    """95% CIs over queries (1000 seeded resamples) for one ranking metric."""
    qids = list(per_q)
    if not qids:
        return {"v1": [0.0, 0.0], "v2": [0.0, 0.0], "delta": [0.0, 0.0]}

    def value(qid: str, model: str) -> float:
        rec = per_q[qid][model]
        if metric == "mrr":
            return float(rec["mrr"])
        if metric == "ndcg":
            return float(rec["ndcg"])
        return float(rec["r_at"].get(int(metric), 0))

    rng = random.Random(BOOTSTRAP_SEED)
    v1s: list[float] = []
    v2s: list[float] = []
    deltas: list[float] = []
    for _ in range(BOOTSTRAP_N):
        sample = [rng.choice(qids) for _ in qids]
        v1 = sum(value(q, "v1") for q in sample) / len(sample)
        v2 = sum(value(q, "v2") for q in sample) / len(sample)
        v1s.append(v1)
        v2s.append(v2)
        deltas.append(v2 - v1)

    def ci(vals: list[float]) -> list[float]:
        vals.sort()
        lo = vals[int(0.025 * len(vals))]
        hi = vals[int(0.975 * len(vals)) - 1]
        return [round(lo, 4), round(hi, 4)]

    return {"v1": ci(v1s), "v2": ci(v2s), "delta": ci(deltas)}


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _fmt(x: float) -> str:
    return f"{x:.4f}"


def _pct_table(rows: list[tuple[str, int, float, float, float]]) -> str:
    """Render (label, n, v1, v2, delta) rows as an aligned ASCII table."""
    out = [f"{'':<12} {'Pairs':>6} {'V1_acc':>8} {'V2_acc':>8} {'Delta':>8}", "-" * 48]
    for label, n, v1, v2, delta in rows:
        out.append(f"{label:<12} {n:>6} {v1:>8.4f} {v2:>8.4f} {delta:>+8.4f}")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="CE v2 regression harness (Step 0 rebuild)")
    parser.add_argument("--no-score", action="store_true", help="Reuse the on-disk score cache only")
    parser.add_argument("--force-score", action="store_true", help="Ignore + rebuild the score cache")
    parser.add_argument("--candidate-pool", choices=["pairs", "mining"], default="pairs",
                        help="Negative source for per-query ranking (default: pair records)")
    parser.add_argument("--freeze-baseline", action="store_true",
                        help="Write ce_v2_regression_baseline.json (requires the "
                             "error-analysis JSON to exist)")
    parser.add_argument("--ambiguous-qids", type=Path, default=None,
                        help="Newline-separated list of ambiguous question ids "
                             "(optional; the original label source is gone)")
    parser.add_argument("--model-v1", type=Path, default=None,
                        help="Override the v1 (frozen control) model directory")
    parser.add_argument("--model-v2", type=Path, default=None,
                        help="Override the v2 (candidate) model directory")
    args = parser.parse_args()

    for key, arg in (("v1", args.model_v1), ("v2", args.model_v2)):
        if arg is not None:
            _MODEL_OVERRIDES[key] = arg

    pairs = load_test_pairs()
    questions = load_questions()
    mining = load_mining()
    if not pairs:
        return 1

    # Group test pairs by question.
    pairs_by_qid: dict[str, list[dict]] = defaultdict(list)
    for p in pairs:
        pairs_by_qid[p["question_id"]].append(p)
    test_qids = sorted(pairs_by_qid)

    # -------- scoring --------
    if args.force_score:
        for key in MODELS:
            path = _score_cache_path(key)
            if path.exists():
                path.unlink()
    scores: dict[str, dict[str, float]] = {}
    for key in MODELS:
        scores[key] = ({} if args.no_score else score_pairs_cached(pairs, key))
        if not scores[key]:
            scores[key] = score_pairs_cached(pairs, key, force=True)

    # -------- pairwise metrics --------
    pairwise: dict[str, dict[str, float]] = {}
    for key in MODELS:
        n = 0
        correct = 0
        margin_sum = 0.0
        for p in pairs:
            pos = scores[key].get(_score_key(p, "pos", str(p.get("positive") or "")))
            neg = scores[key].get(_score_key(p, "neg", str(p.get("negative") or "")))
            if pos is None or neg is None:
                continue
            n += 1
            if pos > neg:
                correct += 1
            margin_sum += pos - neg
        pairwise[key] = {"n": n, "acc": correct / n if n else 0.0, "margin": margin_sum / n if n else 0.0}

    # -------- per-query ranking --------
    cands = build_candidates(pairs_by_qid, mining, args.candidate_pool)
    per_q: dict[str, dict[str, dict]] = {}
    for qid in test_qids:
        q = questions.get(qid)
        query = q.question if q is not None else pairs_by_qid[qid][0]["query"]
        per_q[qid] = {
            key: ranking_metrics(qid, cands[qid], scores[key], query)
            for key in MODELS
        }

    # -------- breakdowns --------
    def _acc(plist: list[dict], key: str) -> float:
        n = 0
        ok = 0
        for p in plist:
            pos = scores[key].get(_score_key(p, "pos", str(p.get("positive") or "")))
            neg = scores[key].get(_score_key(p, "neg", str(p.get("negative") or "")))
            if pos is not None and neg is not None:
                n += 1
                ok += 1 if pos > neg else 0
        return ok / n if n else 0.0

    domain_rows: list[tuple[str, int, float, float, float]] = []
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for p in pairs:
        by_domain[domain_of(p)].append(p)
    for dom in sorted(by_domain):
        plist = by_domain[dom]
        v1, v2 = _acc(plist, "v1"), _acc(plist, "v2")
        domain_rows.append((dom, len(plist), v1, v2, v2 - v1))

    tier_rows: list[tuple[str, int, float, float, float]] = []
    for tier in (1, 2, 3):
        plist = [p for p in pairs if p.get("tier") == tier]
        v1, v2 = _acc(plist, "v1"), _acc(plist, "v2")
        tier_rows.append((f"T{tier}", len(plist), v1, v2, v2 - v1))

    diff_rows: list[tuple[str, int, float, float, float]] = []
    by_diff: dict[str, list[dict]] = defaultdict(list)
    for p in pairs:
        by_diff[difficulty_of(questions.get(p["question_id"]))].append(p)
    for d in sorted(by_diff):
        plist = by_diff[d]
        v1, v2 = _acc(plist, "v1"), _acc(plist, "v2")
        diff_rows.append((d, len(plist), v1, v2, v2 - v1))

    # -------- ambiguous vs straightforward (guarded; label source optional) --------
    ambiguous_rows: list[tuple[str, int, float, float, float]] = []
    has_ambiguous_source = bool(args.ambiguous_qids and args.ambiguous_qids.exists())
    if has_ambiguous_source:
        ambiguous_ids = {
            ln.strip()
            for ln in args.ambiguous_qids.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        }
        for label, ids in (("Ambiguous", ambiguous_ids),
                           ("Straightforward", set(test_qids) - ambiguous_ids)):
            plist = [p for p in pairs if p["question_id"] in ids]
            if plist:
                v1, v2 = _acc(plist, "v1"), _acc(plist, "v2")
                ambiguous_rows.append((label, len(plist), v1, v2, v2 - v1))

    # -------- bootstrap CIs --------
    ci = {m: _bootstrap_ci(per_q, m) for m in ("10", "20", "mrr", "ndcg")}

    # -------- aggregate ranking --------
    agg = {}
    for key in MODELS:
        n = len(per_q)
        r_at = {k: sum(per_q[q][key]["r_at"].get(k, 0) for q in per_q) / n for k in RANK_KS}
        agg[key] = {
            "r_at": {str(k): round(v, 4) for k, v in r_at.items()},
            "mrr": round(sum(per_q[q][key]["mrr"] for q in per_q) / n, 4),
            "ndcg": round(sum(per_q[q][key]["ndcg"] for q in per_q) / n, 4),
        }

    result: dict[str, Any] = {
        "pairs_hash": _pairs_hash(),
        "candidate_pool": args.candidate_pool,
        "n_test_pairs": len(pairs),
        "n_test_queries": len(test_qids),
        "pairwise": pairwise,
        "ranking": agg,
        "bootstrap_ci": ci,
        "per_domain": [{"domain": d, "pairs": n, "v1": v1, "v2": v2, "delta": dlt}
                       for d, n, v1, v2, dlt in domain_rows],
        "per_tier": [{"tier": t, "pairs": n, "v1": v1, "v2": v2, "delta": dlt}
                     for t, n, v1, v2, dlt in tier_rows],
        "per_difficulty": [{"difficulty": d, "pairs": n, "v1": v1, "v2": v2, "delta": dlt}
                           for d, n, v1, v2, dlt in diff_rows],
        "ambiguous": [{"label": lbl, "pairs": n, "v1": v1, "v2": v2, "delta": dlt}
                      for lbl, n, v1, v2, dlt in ambiguous_rows],
        "notes": [
            "difficulty: normalized benchmark difficulty (original medium=1200/hard=1162 buckets unrecoverable)",
            "ambiguous: no per-question label source; bucket empty unless --ambiguous-qids is wired",
        ],
    }
    EVAL_OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # -------- console (ASCII only; fixes the cp1252 crash) --------
    v1, v2 = pairwise["v1"], pairwise["v2"]
    print(f"Train pairs: 10347  Test pairs: {len(pairs)}  Test queries: {len(test_qids)}")
    print("\n=== Pairwise Metrics (pos > neg) ===")
    print(f"V1 accuracy: {v1['acc']:.4f}  margin={v1['margin']:+.4f}")
    print(f"V2 accuracy: {v2['acc']:.4f}  margin={v2['margin']:+.4f}")
    print(f"  delta acc: {v2['acc'] - v1['acc']:+.4f}")
    print(f"  delta margin: {v2['margin'] - v1['margin']:+.4f}")

    print("\n=== Per-query Ranking Metrics ===")
    print(f"{'Metric':<10} {'V1 (CE base)':>14} {'V2 (K500)':>12} {'Delta':>10}")
    print("-" * 50)
    for k in RANK_KS:
        print(f"{'R@' + str(k):<10} {agg['v1']['r_at'][str(k)]:>14.4f} "
              f"{agg['v2']['r_at'][str(k)]:>12.4f} "
              f"{agg['v2']['r_at'][str(k)] - agg['v1']['r_at'][str(k)]:>+10.4f}")
    for metric, label in (("mrr", "MRR@10"), ("ndcg", "nDCG@10")):
        print(f"{label:<10} {agg['v1'][metric]:>14.4f} {agg['v2'][metric]:>12.4f} "
              f"{agg['v2'][metric] - agg['v1'][metric]:>+10.4f}")

    print("\n=== Bootstrap CIs (95% over queries, 1000 resamples) ===")
    for metric, label in (("10", "R@10"), ("20", "R@20"), ("mrr", "MRR@10"), ("ndcg", "nDCG@10")):
        c = ci[metric]
        sig = "SIGNIFICANT" if c["delta"][0] > 0 or c["delta"][1] < 0 else "not significant"
        print(f"  {label}: V1={c['v1']}  V2={c['v2']}  delta={c['delta']}  [{sig}]")

    print("\n=== Per-Domain Results (pairwise accuracy) ===")
    print(_pct_table(domain_rows))
    print("\n=== By Negative Tier (hardness) ===")
    print(_pct_table(tier_rows))
    print("\n=== By Query Difficulty ===")
    print(_pct_table(diff_rows))
    print("\n=== Ambiguous vs Straightforward Queries ===")
    if has_ambiguous_source:
        print(_pct_table(ambiguous_rows) if ambiguous_rows else "  (no pairs in either bucket)")
    else:
        print("  (no per-question ambiguity labels available - pass --ambiguous-qids to populate; skipped)")

    if args.freeze_baseline:
        return freeze_baseline(result)
    return 0


# --------------------------------------------------------------------------- #
# Baseline freeze
# --------------------------------------------------------------------------- #
def freeze_baseline(eval_result: dict[str, Any]) -> int:
    """Merge the eval + error-analysis JSONs into the frozen regression baseline."""
    err_path = CACHE_DIR / "ce_v2_error_analysis.json"
    if not err_path.exists():
        print("error: run evaluation.ce_v2_error_analysis first (missing ce_v2_error_analysis.json)")
        return 1
    err = json.loads(err_path.read_text(encoding="utf-8"))

    import subprocess

    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        rev = "unknown"

    from datetime import UTC, datetime

    baseline: dict[str, Any] = {
        "baseline": "v1-vs-v2 pairwise + ranking regression (Step 0, 2026-08-17)",
        "frozen_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_rev": rev,
        "models": {key: str(path) for key, path in MODELS.items()},
        "n_test_pairs": eval_result["n_test_pairs"],
        "n_test_queries": eval_result["n_test_queries"],
        "pairs_hash": eval_result["pairs_hash"],
        "candidate_pool": eval_result["candidate_pool"],
        "eval": {
            "pairwise": eval_result["pairwise"],
            "ranking": eval_result["ranking"],
            "bootstrap_ci": eval_result["bootstrap_ci"],
            "per_domain": eval_result["per_domain"],
            "per_tier": eval_result["per_tier"],
            "per_difficulty": eval_result["per_difficulty"],
        },
        "error_analysis": {
            "per_query": err.get("per_query", []),
            "failure_taxonomy": err.get("failure_taxonomy", []),
            "failures_total": err.get("failures_total"),
            "categories": err.get("categories", {}),
        },
        "gates": {
            "hierarchy_version_queries": err.get("categories", {}).get("hierarchy_version", 0),
            "same_section_queries": err.get("categories", {}).get("same_section_hard_neg", 0),
            "target_hierarchy": "<= 4 (P1 gate)",
            "target_same_section": "<= 1 (P2 gate)",
        },
        "notes": eval_result.get("notes", []),
    }
    BASELINE_FILE.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    print(f"\nBaseline frozen to: {BASELINE_FILE} (committed - re-freeze after an accepted retrain)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

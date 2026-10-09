"""Train legal_ce_v3_K500 from the existing pairwise pool with a lighter, v3-only profile.

Design (from the on-disk test surface tests/test_train_legal_ce_v3.py):

  v3 is a deliberately lighter fine-tune than v2:
    V3_EPOCHS == 2
    V3_BATCH_SIZE <= 8
    V3_MAX_LEN <= 192
    V3_LR <= 1e-5

  plus a T3 mass cap (`downsample_t3`) so the tier-3-heavy pairwise pool does not
  dominate the lighter v3 budget. v2 is left untouched as the frozen control.

The trainer reuses the crash-safe MarginRankingLossTrainer in
evaluation.ranking_loss_trainer (model/splits/pair-loading, resumable
train_state.pt, pollable training_status.json), so the only v3-specific pieces
are the profile constants and the pool pre-filter.

Inputs (frozen training substrate):
  evaluation/out/cache/pairwise_training_v2.jsonl        27,207 pairs
  evaluation/out/cache/pairwise_train_split.json         train/val/test splits by qid
  evaluation/out/cache/train_pool_v1.json                question pool (--use-pool)

Output:
  evaluation/out/models/legal_ce_v3_K500/               best-val-loss checkpoint
  evaluation/out/models/ce_train_summary_v3_k500.json   run metrics

Usage:
    python -m evaluation.train_legal_ce_v3 --use-pool      # recommended entry point
    python -m evaluation.train_legal_ce_v3 --use-pool --pool-scale 1.0
    python -m evaluation.train_legal_ce_v3 --max-steps 10   # calibration
    python -m evaluation.train_legal_ce_v3                  # full training (resumes)
    python -m evaluation.train_legal_ce_v3 --fresh          # ignore checkpoint
    python -m evaluation.train_legal_ce_v3 --status         # pollable status
    python -m evaluation.train_legal_ce_v3 --watch          # watch until done
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.ranking_loss_trainer import (
    BASE_MODEL,
    MARGIN,
    MarginRankingLossTrainer,
    configure_threads,
    load_pairs,
    load_splits,
)

OUT_DIR = PROJECT_ROOT / "evaluation" / "out"
MODELS_DIR = OUT_DIR / "models"
CHECKPOINT_DIR = MODELS_DIR / "legal_ce_v3_K500"
SUMMARY_FILE = MODELS_DIR / "ce_train_summary_v3_k500.json"
CHECKPOINT_FILE = CHECKPOINT_DIR / "train_state.pt"
STATUS_FILE = CHECKPOINT_DIR / "training_status.json"
POOL_FILE = OUT_DIR / "cache" / "train_pool_v1.json"

# ---------------------------------------------------------------- V3 profile
# Lighter than v2 by construction; asserted by tests/test_train_legal_ce_v3.py.
V3_EPOCHS = 2
V3_BATCH_SIZE = 8
V3_MAX_LEN = 192
V3_LR = 1e-5

# T3 mass cap: when T3 is more than `T3_RATIO` times the combined T1+T2 mass,
# downsample T3 with a deterministic cap so the lighter 2-epoch v3 budget is not
# eaten by the adversarial tier. The cap preserves the T3 ordering (kept slice is
# the deterministic first N after sort by chunk_id) so the result is reproducible.
T3_RATIO = 2.0


def downsample_t3(pairs: list[dict], ratio: float = T3_RATIO) -> tuple[list[dict], dict]:
    """Deterministically cap tier-3 mass relative to T1+T2.

    Returns (filtered_pairs, info). When T3 is already within the ratio there is
    no change; otherwise T3 is truncated to `ratio * (T1 + T2)` examples (kept
    slice is the deterministic first N after sorting by neg_chunk_id then query).
    """
    t1 = [p for p in pairs if p.get("tier") == 1]
    t2 = [p for p in pairs if p.get("tier") == 2]
    t3 = [p for p in pairs if p.get("tier") == 3]

    t12 = len(t1) + len(t2)
    t3_after = len(t3)
    if t3_after <= int(t12 * ratio):
        info = {"t1": len(t1), "t2": len(t2), "t3_before": len(t3), "t3_after": len(t3), "capped": False}
        return pairs, info

    keep = int(t12 * ratio)
    # Deterministic ordering: stable sort so resumed/re-run runs are bit-identical.
    ordered = sorted(t3, key=lambda p: (str(p.get("neg_chunk_id") or ""), str(p.get("query") or "")))
    t3_kept = ordered[:keep]
    out = t1 + t2 + t3_kept
    info = {"t1": len(t1), "t2": len(t2), "t3_before": len(t3), "t3_after": len(t3_kept), "capped": True}
    return out, info


def load_pool(path: Path = POOL_FILE) -> dict:
    """Load the v3 question pool (``train_pool_v1.json``).

    The pool classifies all 150 benchmark qids into a bucket with a training
    weight: priority_provision 3.0, priority_promotable 2.0, guard 1.0,
    untrainable 0.0.  Returns an empty dict when the file is absent so the
    trainer degrades to the un-pooled behaviour instead of crashing.
    """
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def apply_pool(pairs: list[dict], pool: dict, scale: float = 1.0) -> tuple[list[dict], dict]:
    """Weight-filter and oversample pairs by the v3 train-pool.

    Each pair is bucketed by its ``question_id``:

      * bucket weight 0 (untrainable — generation / never_retrieved error that
        ranking training cannot address) -> the pair is dropped;
      * bucket weight w > 0               -> the pair is repeated
        ``max(1, round(w * scale))`` times, so priority_provision contributes
        3x, priority_promotable 2x, guard 1x at ``scale=1.0``.

    qids absent from the pool are treated as guard (kept once) so a pool file
    covering a subset of qids never silently deletes data.

    Validation pairs are deliberately NOT pooled: ``val_loss`` stays on the
    full frozen val split so v3 model selection remains comparable with v2.

    Returns (pairs, info).  Deterministic: input order is preserved and
    repeats are adjacent, so re-runs are bit-identical.
    """
    weights = pool.get("weights") or {}
    bucket_of: dict[str, str] = {}
    for bucket, qids in (pool.get("qids") or {}).items():
        for qid in qids:
            bucket_of[str(qid)] = str(bucket)

    out: list[dict] = []
    bucket_pairs: Counter[str] = Counter()
    dropped = 0
    for p in pairs:
        bucket = bucket_of.get(str(p.get("question_id")))
        # qids absent from the pool keep a single copy (guard default).
        weight = float(weights.get(bucket, 1.0)) if bucket is not None else 1.0
        w = weight * float(scale)
        if w <= 0:
            dropped += 1
            continue
        copies = max(1, round(w))
        for _ in range(copies):
            out.append(p)
        bucket_pairs[bucket or "unlisted"] += copies

    info = {
        "applied": bool(pool),
        "scale": scale,
        "source": POOL_FILE.name,
        "in": len(pairs),
        "out": len(out),
        "dropped": dropped,
        "bucket_pairs": dict(sorted(bucket_pairs.items())),
        "weights": {str(k): float(v) for k, v in weights.items()},
    }
    return out, info


def _fmt_eta(seconds: int | None) -> str:
    if not seconds:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


def _print_status(path: Path) -> int:
    """Print the current training-status JSON (``--status``)."""
    if not path.exists():
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def _watch_status(path: Path, interval: float) -> int:
    last = None
    while True:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = None
            if data is not None and data != last:
                print(
                    f"step {data.get('global_step')}/{data.get('total_steps')} "
                    f"({data.get('percent')}%) epoch {data.get('epoch')}/{data.get('epochs')} "
                    f"phase={data.get('phase')} loss={data.get('train_loss')} "
                    f"val={data.get('val_loss')} best={data.get('best_val_loss')} "
                    f"rss={data.get('peak_rss_mb')}MB eta={_fmt_eta(data.get('eta_seconds'))}",
                )
                last = data
                if data.get("status") in ("done", "interrupted"):
                    return 0
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train legal_ce_v3_K500")
    parser.add_argument("--max-steps", type=int, default=None, help="Calibration: stop after N optimizer steps.")
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="Override device auto-detection (default: CPU).",
    )
    parser.add_argument("--threads", type=int, default=2, help="Torch intra-op thread cap (v3 default 2).")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=V3_BATCH_SIZE,
        help=f"Per-step batch size (default {V3_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--seconds-per-step",
        type=float,
        default=9.0,
        help="Measured s/step used only for the ETA printout (default 9.0).",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore any existing train_state.pt and start from scratch.",
    )
    parser.add_argument(
        "--max-epochs",
        type=int,
        default=None,
        help="Cap training at N epochs (--max-epochs 1 stops after epoch 1).",
    )
    parser.add_argument(
        "--save-every",
        type=int,
        default=25,
        help="Save a resumable checkpoint every N optimizer steps (default 25).",
    )
    parser.add_argument(
        "--t3-ratio",
        type=float,
        default=T3_RATIO,
        help="T3 mass cap ratio relative to T1+T2 (default 2.0).",
    )
    parser.add_argument(
        "--no-t3-cap",
        action="store_true",
        help="Disable the T3 mass cap (use the full pairwise pool as-is).",
    )
    parser.add_argument(
        "--use-pool",
        action="store_true",
        help="Apply the v3 question pool (train_pool_v1.json): drop untrainable "
        "qids and oversample priority buckets by their weights.",
    )
    parser.add_argument(
        "--pool-scale",
        type=float,
        default=1.0,
        help="Multiplier applied to pool weights when --use-pool is set (default 1.0).",
    )
    parser.add_argument("--status", action="store_true", help="Print the current training-status JSON and exit.")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Poll the training-status file until training completes.",
    )
    parser.add_argument(
        "--watch-interval",
        type=float,
        default=30.0,
        help="Seconds between --watch polls (default 30).",
    )
    args = parser.parse_args()

    if args.status:
        return _print_status(STATUS_FILE)
    if args.watch:
        return _watch_status(STATUS_FILE, args.watch_interval)

    configure_threads(args.threads)

    pairs = load_pairs()
    if not pairs:
        return 1
    splits = load_splits()
    train_qids = set(splits.get("train_qids", []))
    val_qids = set(splits.get("val_qids", []))

    # Pre-filter to training questions first, then apply the v3 pool weights,
    # then the T3 mass cap (so the cap bounds the FINAL tier mass).
    train_pairs_all = [p for p in pairs if p["question_id"] in train_qids]
    val_pairs = [p for p in pairs if p["question_id"] in val_qids]
    if not train_pairs_all:
        return 1

    pool = load_pool() if args.use_pool else {}
    if args.use_pool and not pool:
        print(f"--use-pool: pool file not found: {POOL_FILE}", file=sys.stderr)
        return 1
    train_pairs, pool_info = apply_pool(train_pairs_all, pool, scale=args.pool_scale)
    if not train_pairs:
        return 1

    if not args.no_t3_cap:
        train_pairs, t3_info = downsample_t3(train_pairs, ratio=args.t3_ratio)
    else:
        t3_info = {"capped": False}

    if not train_pairs:
        return 1

    try:
        import psutil

        ram_gb = round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:
        ram_gb = "?"

    t0 = time.time()
    trainer = MarginRankingLossTrainer(
        model_name=BASE_MODEL,
        max_len=V3_MAX_LEN,
        margin=MARGIN,
        device=args.device,
    )
    result = trainer.train(
        train_pairs=train_pairs,
        val_pairs=val_pairs,
        epochs=args.max_epochs or V3_EPOCHS,
        lr=V3_LR,
        batch_size=args.batch_size,
        loss_type="margin",
        curriculum=False,
        output_dir=CHECKPOINT_DIR,
        max_steps=args.max_steps,
        val_cap=480,
        val_batch_size=64,
        resume=not args.fresh,
        save_every=args.save_every,
        status_file=STATUS_FILE,
    )
    result["elapsed_seconds"] = round(time.time() - t0, 1)
    result["checkpoint_dir"] = CHECKPOINT_DIR.as_posix()
    result["hardware"] = {
        "device": args.device or "cpu (auto-detected)",
        "threads": args.threads,
        "ram_gb": ram_gb,
    }
    result["status_file"] = STATUS_FILE.as_posix()
    result["checkpoint_file"] = CHECKPOINT_FILE.as_posix()
    result["dataset"] = (
        "pairwise_training_v2.jsonl (27,207 pairs) -> v3 pool "
        f"(train questions only; pool={'on' if args.use_pool else 'off'}"
        f", scale={args.pool_scale}; T3 capped at {args.t3_ratio}x T1+T2)"
    )
    result["v3_profile"] = {
        "epochs": args.max_epochs or V3_EPOCHS,
        "batch_size": args.batch_size,
        "max_len": V3_MAX_LEN,
        "lr": V3_LR,
        "loss": "margin",
        "curriculum": False,
        "use_pool": args.use_pool,
        "pool_scale": args.pool_scale,
        "pool_info": pool_info,
        "t3_cap_ratio": args.t3_ratio,
        "t3_cap_applied": not args.no_t3_cap,
        "t3_info": t3_info,
    }
    result["notes"] = (
        "Lighter v3 fine-tune over the existing v2 pairwise pool. "
        "legal_ce_v2_K500 is the frozen control. v3 profile satisfies tests/test_train_legal_ce_v3.py "
        "(epochs=2, batch<=8, max_len<=192, lr<=1e-5)."
    )
    SUMMARY_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

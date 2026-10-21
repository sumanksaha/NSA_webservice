"""Train legal_ce_v4_K500 — v4 iteration with failure-cause fixes from v3.

v3 diagnosis (from evaluation/out/ceiling_v5/ce_v3_promotion_diagnosis.json):
  * 12/26 promotion qids: gold in head-150 but v3 CE demoted it by 3-8 points
    → fix: margin 1.0 → 2.0 (force separation)
  * 4/26 promotion qids: gold NOT in head-150 (retrieval-side dropout)
    → fix: upweight those qids' pairs via the train-pool (--use-pool); the
      summary records Q024/Q063/Q072/Q122 and the bucket that carries them so
      evaluation/ce_v3_dropout_check.py can verify the fix landed
  * 2/26: builder_dropped (context-window packing — not CE-fixable)
  * Val loss rose 0.7319 → 0.7369 between epoch 0 and epoch 1 → overfit
  * v3 regressed vs v1/v2 on Q005 and Q076

v4 profile (lighter than v3 by construction):

| Parameter | v3 | v4 | Rationale |
|-----------|-----|-----|-----------|
| Epochs    | 2   | 1   | Epoch 1 caused overfit (val loss 0.7319→0.7369) |
| Batch size| 8   | 16  | More gradient signal per step |
| Max len   | 192 | 192 | Keep unchanged |
| LR        | 1e-5| 5e-6| Lower LR for single epoch, less overfit risk |
| Margin    | 1.0 | 2.0 | Gold loses by 3-8 points; wider margin forces separation |
| T3 ratio  | 2.0 | 1.5 | Reduce adversarial tier dominance |
| Save every| 50  | 25  | More frequent crash-safe checkpoints |
| Curriculum| False|False| Keep disabled (lighter fine-tune) |

Inputs:
  evaluation/out/cache/pairwise_training_v2.jsonl   27,207 pairs
  evaluation/out/cache/pairwise_train_split.json    train/val/test splits by qid
  evaluation/out/cache/train_pool_v1.json           question pool (--use-pool)

Output:
  evaluation/out/models/legal_ce_v4_K500/          best-val-loss checkpoint
  evaluation/out/models/ce_train_summary_v4_k500.json  run metrics
"""
from __future__ import annotations

import argparse
import json
import sys
import time
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
CHECKPOINT_DIR = MODELS_DIR / "legal_ce_v4_K500"
SUMMARY_FILE = MODELS_DIR / "ce_train_summary_v4_k500.json"
CHECKPOINT_FILE = CHECKPOINT_DIR / "train_state.pt"
STATUS_FILE = CHECKPOINT_DIR / "training_status.json"
POOL_FILE = OUT_DIR / "cache" / "train_pool_v1.json"

V4_EPOCHS = 1
V4_BATCH_SIZE = 16
V4_MAX_LEN = 192
V4_LR = 5e-6
V4_MARGIN = 2.0
V4_T3_RATIO = 1.5
V4_SAVE_EVERY = 25

V4_DROPOUT_QIDS = ["Q024", "Q063", "Q072", "Q122"]
V4_DEMOTED_QIDS = ["Q005", "Q026", "Q048", "Q076", "Q116", "Q126"]



def downsample_t3(pairs: list[dict], ratio: float = V4_T3_RATIO) -> tuple[list[dict], dict]:
    """Deterministically cap tier-3 mass relative to T1+T2.

    Returns (filtered_pairs, info). When T3 is already within the ratio there is
    no change; otherwise T3 is truncated to `ratio * (T1 + T2)` examples.
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
    ordered = sorted(t3, key=lambda p: (str(p.get("neg_chunk_id") or ""), str(p.get("query") or "")))
    t3_kept = ordered[:keep]
    out = t1 + t2 + t3_kept
    info = {"t1": len(t1), "t2": len(t2), "t3_before": len(t3), "t3_after": len(t3_kept), "capped": True}
    return out, info


def load_pool(path: Path = POOL_FILE) -> dict:
    """Load the v3 question pool (``train_pool_v1.json``).

    Classifies benchmark qids into buckets with training weights:
    priority_provision 3.0, priority_promotable 2.0, guard 1.0, untrainable 0.0.
    Returns an empty dict when the file is absent so the trainer degrades to
    un-pooled behaviour instead of crashing.
    """
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def apply_pool(pairs: list[dict], pool: dict, scale: float = 1.0) -> tuple[list[dict], dict]:
    """Weight-filter and oversample pairs by the v3 train-pool.

    Each pair is bucketed by its ``question_id``:
      * bucket weight 0 (untrainable) -> the pair is dropped;
      * bucket weight w > 0           -> repeated max(1, round(w * scale)) times.

    qids absent from the pool are treated as guard (kept once).
    Validation pairs are deliberately NOT pooled.
    Returns (pairs, info).
    """
    weights = pool.get("weights") or {}
    bucket_of: dict[str, str] = {}
    for bucket, qids in (pool.get("qids") or {}).items():
        for qid in qids:
            bucket_of[str(qid)] = str(bucket)

    out: list[dict] = []
    bucket_pairs: dict[str, int] = {}
    dropped = 0
    for p in pairs:
        bucket = bucket_of.get(str(p.get("question_id")))
        weight = float(weights.get(bucket, 1.0)) if bucket is not None else 1.0
        w = weight * float(scale)
        if w <= 0:
            dropped += 1
            continue
        copies = max(1, round(w))
        for _ in range(copies):
            out.append(p)
        bucket_pairs[bucket or "unlisted"] = bucket_pairs.get(bucket or "unlisted", 0) + copies

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


def dropout_pool_coverage(pool: dict, qids: list[str] | None = None) -> dict[str, str]:
    """Map each dropout qid to the pool bucket that upweights it.

    The dropout fix is delivered through the pool: ``apply_pool`` oversamples a
    qid's pairs by ``weight x scale`` when it sits in a priority bucket.  This
    mapping makes it visible when the pool stops covering a dropout qid
    (``"unlisted"``) instead of silently training on the un-targeted set.
    """
    bucket_of: dict[str, str] = {}
    for bucket, bucket_qids in (pool.get("qids") or {}).items():
        for qid in bucket_qids:
            bucket_of[str(qid)] = str(bucket)
    return {qid: bucket_of.get(qid, "unlisted") for qid in (qids or V4_DROPOUT_QIDS)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Train legal_ce_v4_K500")
    parser.add_argument("--max-steps", type=int, default=None, help="Calibration: stop after N optimizer steps.")
    parser.add_argument("--device", type=str, default=None, help="Override device auto-detection (default: CPU).")
    parser.add_argument("--threads", type=int, default=2, help="Torch intra-op thread cap (v4 default 2).")
    parser.add_argument("--batch-size", type=int, default=V4_BATCH_SIZE, help=f"Per-step batch size (default {V4_BATCH_SIZE}).")
    parser.add_argument("--seconds-per-step", type=float, default=9.0, help="Measured s/step used only for the ETA printout (default 9.0).")
    parser.add_argument("--fresh", action="store_true", help="Ignore any existing train_state.pt and start from scratch.")
    parser.add_argument("--max-epochs", type=int, default=None, help="Cap training at N epochs (--max-epochs 1 stops after epoch 1).")
    parser.add_argument("--save-every", type=int, default=V4_SAVE_EVERY, help=f"Save a resumable checkpoint every N optimizer steps (default {V4_SAVE_EVERY}).")
    parser.add_argument("--t3-ratio", type=float, default=V4_T3_RATIO, help="T3 mass cap ratio relative to T1+T2 (default 1.5).")
    parser.add_argument("--no-t3-cap", action="store_true", help="Disable the T3 mass cap (use the full pairwise pool as-is).")
    parser.add_argument("--use-pool", action="store_true", help="Apply the v3 question pool (train_pool_v1.json): drop untrainable qids and oversample priority buckets by their weights.")
    parser.add_argument("--pool-scale", type=float, default=1.5, help="Multiplier applied to pool weights when --use-pool is set (default 1.5).")
    parser.add_argument("--margin", type=float, default=V4_MARGIN, help=f"Margin for margin ranking loss (default {V4_MARGIN}).")
    parser.add_argument("--status", action="store_true", help="Print the current training-status JSON and exit.")
    parser.add_argument("--watch", action="store_true", help="Poll the training-status file until training completes.")
    parser.add_argument("--watch-interval", type=float, default=30.0, help="Seconds between --watch polls (default 30).")
    args = parser.parse_args()

    if args.status:
        print(STATUS_FILE.read_text(encoding="utf-8"))
        return 0
    if args.watch:
        import time as _time
        last = None
        while True:
            if STATUS_FILE.exists():
                try:
                    data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
                except Exception:
                    data = None
                if data is not None and data != last:
                    print(f"step {data.get('global_step')}/{data.get('total_steps')} ({data.get('percent')}%) "
                          f"epoch {data.get('epoch')}/{data.get('epochs')} phase={data.get('phase')} "
                          f"loss={data.get('train_loss')} val={data.get('val_loss')} best={data.get('best_val_loss')} "
                          f"rss={data.get('peak_rss_mb')}MB")
                    last = data
                    if data.get("status") in ("done", "interrupted"):
                        return 0
            _time.sleep(args.watch_interval)

    configure_threads(args.threads)

    pairs = load_pairs()
    if not pairs:
        print("No training pairs found.", file=sys.stderr)
        return 1
    splits = load_splits()
    train_qids = set(splits.get("train_qids", []))
    val_qids = set(splits.get("val_qids", []))

    # Pre-filter to training questions first, then apply pool weights, then T3 cap.
    train_pairs_all = [p for p in pairs if p["question_id"] in train_qids]
    val_pairs = [p for p in pairs if p["question_id"] in val_qids]
    if not train_pairs_all:
        print("No train pairs after qid split filter.", file=sys.stderr)
        return 1

    pool = load_pool() if args.use_pool else {}
    if args.use_pool and not pool:
        print(f"--use-pool: pool file not found: {POOL_FILE}", file=sys.stderr)
        return 1
    train_pairs, pool_info = apply_pool(train_pairs_all, pool, scale=args.pool_scale)
    if not train_pairs:
        print("No train pairs after pool filtering.", file=sys.stderr)
        return 1

    if not args.no_t3_cap:
        train_pairs, t3_info = downsample_t3(train_pairs, ratio=args.t3_ratio)
    else:
        t3_info = {"capped": False}

    if not train_pairs:
        print("No train pairs after T3 cap.", file=sys.stderr)
        return 1

    try:
        import psutil
        ram_gb = round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:
        ram_gb = "?"

    t0 = time.time()
    trainer = MarginRankingLossTrainer(
        model_name=BASE_MODEL,
        max_len=V4_MAX_LEN,
        margin=args.margin,       # v4: wider margin (2.0 vs v3's 1.0)
        device=args.device,
    )
    result = trainer.train(
        train_pairs=train_pairs,
        val_pairs=val_pairs,
        epochs=args.max_epochs or V4_EPOCHS,
        lr=V4_LR,
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
    result["dataset"] = (
        "pairwise_training_v2.jsonl (27,207 pairs) -> v4 pool "
        f"(train questions only; pool={'on' if args.use_pool else 'off'}"
        f", scale={args.pool_scale}; T3 capped at {args.t3_ratio}x T1+T2)"
    )
    result["v4_profile"] = {
        "epochs": args.max_epochs or V4_EPOCHS,
        "batch_size": args.batch_size,
        "max_len": V4_MAX_LEN,
        "lr": V4_LR,
        "margin": args.margin,
        "loss": "margin",
        "curriculum": False,
        "use_pool": args.use_pool,
        "pool_scale": args.pool_scale,
        "pool_info": pool_info,
        "t3_cap_ratio": args.t3_ratio,
        "t3_cap_applied": not args.no_t3_cap,
        "t3_info": t3_info,
        "dropout_qids": V4_DROPOUT_QIDS,
        "dropout_pool_bucket": dropout_pool_coverage(pool),
        "demoted_qids": V4_DEMOTED_QIDS,
    }
    result["notes"] = (
        "v4 iteration over legal_ce_v3_K500 with failure-cause fixes: "
        "1 epoch (no overfit), margin=2.0 (gold separation), batch=16 (more signal), "
        "lr=5e-6 (lower overfit risk), T3 ratio=1.5 (less adversarial dominance). "
        "Dropout qids are upweighted only when --use-pool is set. "
        "legal_ce_v2_K500 is the frozen control."
    )
    SUMMARY_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

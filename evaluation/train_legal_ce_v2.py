"""Train legal_ce_v2_K500 with the existing ranking-loss trainer.

Reuses :class:`evaluation.ranking_loss_trainer.MarginRankingLossTrainer`
(no new architecture) to train the primary experiment model on the existing
14,629-pair hard-negative dataset (``pairwise_training_v2.jsonl``) with:

    loss:       margin ranking loss (margin=1.0)
    curriculum: progressive T1 (random) -> T2 (semantic) -> T3 (adversarial)
    epochs:     3 (epoch 0 = T1 only, epoch 1 = T1+T2, epoch 2 = all tiers)
    batch:      24, lr: 2e-5, max_len: 256, torch threads: 4

Checkpoint: evaluation/out/models/legal_ce_v2_K500/  (best validation loss)
Summary:    evaluation/out/models/ce_train_summary_v2_k500.json

legal_ce_v1 is never touched — it stays the frozen control.

Crash-safe (laptop-friendly): a resumable ``train_state.pt`` is saved every
``--save-every`` steps (model + AdamW + RNG + epoch shuffle) and written
atomically, so closing/restarting the laptop loses at most a few minutes of
progress — re-running the same command resumes automatically and skips the
~4 min re-tokenization via the tokenized cache.  A pollable
``training_status.json`` is written every 5 steps; monitor it from another
terminal with ``--status`` / ``--watch``.

Usage:
    python -m evaluation.train_legal_ce_v2 --max-steps 10   # calibration
    python -m evaluation.train_legal_ce_v2                   # full training (resumes if interrupted)
    python -m evaluation.train_legal_ce_v2 --fresh           # ignore checkpoint, start over
    python -m evaluation.train_legal_ce_v2 --save-every 50   # fewer, bigger checkpoints
    python -m evaluation.train_legal_ce_v2 --status          # print current progress
    python -m evaluation.train_legal_ce_v2 --watch           # poll progress until done
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.ranking_loss_trainer import (  # noqa: E402
    BASE_MODEL,
    BATCH_SIZE,
    LR,
    MARGIN,
    MAX_LEN,
    MarginRankingLossTrainer,
    configure_threads,
    load_pairs,
    load_splits,
)

OUT_DIR = PROJECT_ROOT / "evaluation" / "out"
MODELS_DIR = OUT_DIR / "models"
CHECKPOINT_DIR = MODELS_DIR / "legal_ce_v2_K500"
SUMMARY_FILE = MODELS_DIR / "ce_train_summary_v2_k500.json"
CHECKPOINT_FILE = CHECKPOINT_DIR / "train_state.pt"
STATUS_FILE = CHECKPOINT_DIR / "training_status.json"

EPOCHS = 3


def _fmt_eta(seconds: int | None) -> str:
    """Format seconds as '3h24m' / '12m05s' / '?'."""
    if not seconds:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


def _print_status(path: Path) -> int:
    """Print the current training-status JSON (pollable from another process)."""
    if not path.exists():
        print(
            f"[status] no status file at {path} — training has not started yet",
            file=sys.stderr,
        )
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def _watch_status(path: Path, interval: float) -> int:
    """Poll the status file and print progress until training completes.

    Run this in a separate terminal while the trainer runs; it never
    touches the training process (the trainer writes the file atomically).
    """
    print(f"[watch] polling {path} every {interval:g}s (Ctrl+C to stop)", file=sys.stderr)
    last = None
    while True:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - torn/partial read (atomic write avoids this)
                data = None
            if data is not None and data != last:
                line = (
                    f"step {data.get('global_step')}/{data.get('total_steps')} "
                    f"({data.get('percent')}%) epoch {data.get('epoch')}/{data.get('epochs')} "
                    f"phase={data.get('phase')} loss={data.get('train_loss')} "
                    f"val={data.get('val_loss')} best={data.get('best_val_loss')} "
                    f"rss={data.get('peak_rss_mb')}MB eta={_fmt_eta(data.get('eta_seconds'))}"
                )
                print(line, flush=True)
                last = data
                if data.get("status") in ("done", "interrupted"):
                    print(f"[watch] training {data.get('status')} — stopping", flush=True)
                    return 0
        else:
            print("[watch] status file not found yet — is training running?", file=sys.stderr)
        time.sleep(interval)


def _eta_estimate(train_pairs: list[dict], batch_size: int, seconds_per_step: float = 7.0) -> str:
    """Best-effort wall-clock estimate for the curriculum schedule.

    Curriculum: epoch 0 = Tier 1 only, epoch 1 = Tier 1+2, epoch 2 = all.
    Uses the measured 7.0s/step (batch 24, 4 threads, i5-1135G7) as the
    default per-step cost; the caller can pass their own calibration value.
    """
    from collections import Counter

    tiers = Counter(p.get("tier", 1) for p in train_pairs)
    steps = 0
    for epoch_max_tier in (1, 2, 3):
        n = sum(v for t, v in tiers.items() if t <= epoch_max_tier)
        steps += max(n // batch_size, 1)
    mins = steps * seconds_per_step / 60.0
    return (
        f"~{steps} optimizer steps -> ~{mins:.0f} min at {seconds_per_step:.1f}s/step "
        f"(tiers: T1={tiers.get(1, 0)}, T2={tiers.get(2, 0)}, T3={tiers.get(3, 0)})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Train legal_ce_v2_K500")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Calibration mode: stop after this many optimizer steps.",
    )
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="Override device auto-detection (default: CPU; the measured fastest "
        "and only RAM-safe option on shared-memory iGPUs like Iris Xe).",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=4,
        help="Torch intra-op thread cap (default 4; the measured sweet spot).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Per-step batch size (default {BATCH_SIZE}; batch 48 + unbounded "
        "threads swap-thrashed this 8 GB machine — keep <= 32 unless RAM is free).",
    )
    parser.add_argument(
        "--seconds-per-step",
        type=float,
        default=7.0,
        help="Measured s/step used only for the ETA printout (default 7.0).",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore any existing train_state.pt and start from scratch "
        "(delete the checkpoint first to free disk space).",
    )
    parser.add_argument(
        "--save-every",
        type=int,
        default=25,
        help="Save a resumable checkpoint every N optimizer steps (default 25 "
        "= 5-10 min of progress). Raise it if the ~1.3 GB checkpoint writes "
        "slow the laptop; lower it for tighter crash tolerance.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print the current training-status JSON and exit (works while "
        "training runs in another process).",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Poll the training-status file periodically and print progress "
        "until training completes (run in a separate terminal).",
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

    # Bound threads before any torch import / parallel work starts.
    configure_threads(args.threads)

    pairs = load_pairs()
    if not pairs:
        print("[train_legal_ce_v2] no pairs — run evaluation.pairwise_dataset", file=sys.stderr)
        return 1
    splits = load_splits()
    train_qids = set(splits.get("train_qids", []))
    val_qids = set(splits.get("val_qids", []))
    train_pairs = [p for p in pairs if p["question_id"] in train_qids]
    val_pairs = [p for p in pairs if p["question_id"] in val_qids]
    if not train_pairs:
        print("[train_legal_ce_v2] no train pairs", file=sys.stderr)
        return 1

    try:
        import psutil

        ram_gb = round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:
        ram_gb = "?"
    print(
        f"[train_legal_ce_v2] train={len(train_pairs)} val={len(val_pairs)} "
        f"epochs={EPOCHS} loss=margin curriculum=T1->T2->T3 batch={args.batch_size} "
        f"lr={LR} device={args.device or 'auto(CPU-first)'} threads={args.threads} "
        f"ram={ram_gb}GB",
        file=sys.stderr,
    )
    print(f"[train_legal_ce_v2] ETA: {_eta_estimate(train_pairs, args.batch_size, args.seconds_per_step)}", file=sys.stderr)
    if CHECKPOINT_FILE.exists() and not args.fresh:
        try:
            ck = json.loads(
                CHECKPOINT_FILE.with_name("training_status.json").read_text(encoding="utf-8")
            ) if STATUS_FILE.exists() else {}
            step = ck.get("global_step", "?")
        except Exception:  # noqa: BLE001
            step = "?"
        print(f"[train_legal_ce_v2] checkpoint found — will RESUME (step {step}); use --fresh to restart", file=sys.stderr)

    t0 = time.time()
    trainer = MarginRankingLossTrainer(
        model_name=BASE_MODEL, max_len=MAX_LEN, margin=MARGIN, device=args.device
    )
    result = trainer.train(
        train_pairs=train_pairs,
        val_pairs=val_pairs,
        epochs=EPOCHS,
        lr=LR,
        batch_size=args.batch_size,
        loss_type="margin",
        curriculum=True,
        output_dir=CHECKPOINT_DIR,
        max_steps=args.max_steps,
        # Validate on a stride-sampled subset during training (fast model
        # selection on an 8 GB laptop); the final v1-vs-v2 comparison still
        # runs on the full untouched val/test sets.
        val_cap=480,
        val_batch_size=64,
        # Crash-safe training: resume from train_state.pt when present
        # (laptop restarts lose at most --save-every steps) and write a
        # pollable training_status.json every 5 steps.
        resume=not args.fresh,
        save_every=args.save_every,
        status_file=STATUS_FILE,
    )
    result["elapsed_seconds"] = round(time.time() - t0, 1)
    result["checkpoint_dir"] = CHECKPOINT_DIR.as_posix()
    result["hardware"] = {
        "device": args.device or "cpu (auto-detected: DirectML iGPU skipped — slower + OOMs)",
        "threads": args.threads,
        "ram_gb": ram_gb,
    }
    result["status_file"] = STATUS_FILE.as_posix()
    result["checkpoint_file"] = CHECKPOINT_FILE.as_posix()
    result["dataset"] = "pairwise_training_v2.jsonl (14,629 pairs; whole-corpus offline mining)"
    result["notes"] = (
        "legal_ce_v1 untouched (frozen control). Margin ranking loss + progressive "
        "curriculum T1(random)->T2(semantic)->T3(adversarial)."
    )
    SUMMARY_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

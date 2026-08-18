"""Ranking-loss trainer — improved CE fine-tuning with pairwise losses.

Replaces the pointwise BCE loss in finetune_ce.py with ranking-oriented
losses (margin ranking, contrastive) and supports curriculum training
(easy → semantic → adversarial negatives).

Compares at least:
  Baseline: existing CE without new training
  Model A:  CE + ordinary negatives (Tier 1+2)
  Model B:  CE + semantic hard negatives (Tier 2)
  Model C:  CE + adversarial legal negatives (Tier 3)
  Model D:  CE + mixed negative curriculum (T1→T2→T3 progressive)

Output: evaluation/out/models/legal_ce_v2_<variant>/

Usage:
    python -m evaluation.ranking_loss_trainer --variant model_d
    python -m evaluation.ranking_loss_trainer --variant all --epochs 3
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / "evaluation" / "out"
CACHE_DIR = OUT_DIR / "cache"
MODELS_DIR = OUT_DIR / "models"
PAIRS_FILE = CACHE_DIR / "pairwise_training_v2.jsonl"
SPLIT_FILE = CACHE_DIR / "pairwise_train_split.json"
BASE_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# Training hyperparameters
MAX_LEN = 256
BATCH_SIZE = 24
LR = 2e-5
MARGIN = 1.0  # margin for margin ranking loss


def load_pairs() -> list[dict]:
    """Load pairwise training examples."""
    pairs = []
    with open(PAIRS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs


def load_splits() -> dict[str, list[str]]:
    """Load question-id splits."""
    if SPLIT_FILE.exists():
        with open(SPLIT_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def filter_by_variant(pairs: list[dict], variant: str) -> list[dict]:
    """Filter pairs by variant (tier selection)."""
    if variant in ("baseline", "model_a"):
        # Model A: all tiers (ordinary negatives)
        return pairs
    elif variant == "model_b":
        # Model B: semantic hard negatives only (Tier 2)
        return [p for p in pairs if p["tier"] == 2]
    elif variant == "model_c":
        # Model C: adversarial legal negatives only (Tier 3)
        return [p for p in pairs if p["tier"] == 3]
    elif variant == "model_d":
        # Model D: mixed curriculum (all tiers, progressive)
        return pairs
    else:
        return pairs


def configure_threads(threads: int | None = None) -> None:
    """Bound PyTorch's intra/inter-op thread pools.

    On Windows, PyTorch defaults to one thread per core; for small per-step
    work the sync overhead dominates and the run grinds to a near-halt (see
    the swap-thrash note in finetune_ce.py — batch 48 + unbounded threads
    halted this 8 GB machine).  ``set_num_interop_threads`` must run before
    any parallel torch work starts, so call this as early as possible
    (main() and train()).  When *threads* is None, reuse a value already set
    via ``OMP_NUM_THREADS`` (e.g. by an earlier ``main()`` call) — so a
    ``--threads`` CLI override survives the ``train()`` re-entry.  Measured
    sweet spot for the reference laptop (i5-1135G7, 8 GB): 4 threads →
    7.0s/step @ batch 24, 846 MB RSS.
    """
    import os

    if threads is None:
        try:
            threads = int(os.environ.get("OMP_NUM_THREADS", "4"))
        except ValueError:
            threads = 4
    if os.environ.get("OMP_NUM_THREADS") is None:
        os.environ["OMP_NUM_THREADS"] = str(threads)
    try:
        import torch

        torch.set_num_threads(threads)
        with contextlib.suppress(Exception):
            torch.set_num_interop_threads(1)
    except ImportError:
        pass


def detect_device() -> str:
    """Auto-detect the best available PyTorch device.

    Priority:
      1. CUDA -- NVIDIA GPUs (fastest when present)
      2. XPU -- Intel Arc discrete / IPEX
      3. CPU -- the safe default; on shared-memory iGPUs it is *faster*
         than DirectML for this model class (measured on Iris Xe / 8 GB:
         CPU 7.0s/step vs DirectML OOM)
      4. DirectML -- last resort (opt-in via ``--device privateuseone:0``);
         on integrated GPUs the "VRAM" is carved from system RAM, so a
         110M-param CE + optimizer states routinely exceeds what is left
         and the DirectML backend is slower than a 4-thread CPU anyway.
    """
    # CUDA (NVIDIA)
    try:
        import torch
        if torch.cuda.is_available() and torch.cuda.device_count() > 0:
            dev = "cuda:" + str(torch.cuda.current_device())
            return dev
    except Exception:
        pass

    # XPU (Intel Arc / IPEX)
    try:
        import torch
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            dev = "xpu:" + str(torch.xpu.current_device())
            return dev
    except Exception:
        pass

    # DirectML is *last*: on shared-memory iGPUs (Iris Xe etc.) it is slower
    # than a thread-capped CPU and commonly OOMs for training (measured).
    # Force it explicitly with --device privateuseone:0 when you want it.
    try:
        import torch_directml
        dev = torch_directml.device(0)
        if dev is not None:
            pass
    except Exception:
        pass

    return "cpu"


# --------------------------------------------------------------------------- #
# Crash-safe checkpointing + pollable status (laptop-friendly training)
# --------------------------------------------------------------------------- #


def save_training_checkpoint(
    path: Path,
    *,
    model,
    optimizer,
    epoch: int,
    steps_in_epoch: int,
    global_step: int,
    best_val_loss: float,
    history: dict,
    loss_type: str,
    curriculum: bool,
    epochs: int,
    max_steps: int | None,
    torch_rng,
    python_rng,
    epoch_order,
    order_epoch: int,
    device: str,
) -> None:
    """Atomically write a resumable training checkpoint.

    The payload is written to a ``.tmp`` sibling and then ``os.replace``d
    into place, so a crash mid-write (laptop closed, power cut) can never
    corrupt the previous checkpoint — the worst case is a stale ``.tmp``
    that the next run simply ignores.

    The checkpoint holds the model + AdamW state + RNG state + the current
    epoch's shuffle order, so a resumed run continues bit-identically inside
    the partially-completed epoch.  *epoch_order* is the ``torch.randperm``
    index tensor for the epoch in progress (saved so resume reuses it
    instead of re-shuffling mid-epoch); *order_epoch* records which epoch it
    belongs to so a stale order is never reused.
    """
    import torch

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = {
        "version": 2,
        "model_state": model.state_dict(),
        "optimizer_state": optimizer.state_dict(),
        "epoch": int(epoch),
        "steps_in_epoch": int(steps_in_epoch),
        "global_step": int(global_step),
        "best_val_loss": float(best_val_loss),
        "history": history,
        "loss_type": loss_type,
        "curriculum": bool(curriculum),
        "epochs": int(epochs),
        "max_steps": max_steps,
        "device": str(device),
        "torch_rng": torch_rng,
        "python_rng": python_rng,
        "epoch_order": epoch_order,
        "order_epoch": int(order_epoch),
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    torch.save(payload, tmp)
    os.replace(tmp, path)


def load_training_checkpoint(path: Path) -> dict | None:
    """Load a resumable checkpoint, or ``None`` when absent/unreadable."""
    import torch

    if not path.exists():
        return None
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except Exception:
        return None


def write_training_status(
    path: Path | None,
    *,
    status: str,
    phase: str,
    epoch: int,
    epochs: int,
    global_step: int,
    total_steps: int,
    train_loss: float | None = None,
    val_loss: float | None = None,
    best_val_loss: float | None = None,
    peak_rss_mb: float | None = None,
    device: str | None = None,
    threads: int | None = None,
    elapsed_seconds: float | None = None,
    eta_seconds: float | None = None,
    last_checkpoint: str | None = None,
) -> None:
    """Atomically write the pollable training-status JSON.

    The training loop calls this every ``status_every`` steps; a separate
    process can poll the file (``python -m evaluation.train_legal_ce_v2
    --status`` / ``--watch``) to see live progress without touching the
    trainer.  Written atomically so a poller never reads a torn file.
    """
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "phase": phase,
        "epoch": int(epoch),
        "epochs": int(epochs),
        "global_step": int(global_step),
        "total_steps": int(total_steps),
        "percent": round(100.0 * int(global_step) / max(int(total_steps), 1), 1),
        "train_loss": round(float(train_loss), 5) if train_loss is not None else None,
        "val_loss": round(float(val_loss), 5) if val_loss is not None else None,
        "best_val_loss": round(float(best_val_loss), 5) if best_val_loss is not None else None,
        "peak_rss_mb": round(float(peak_rss_mb), 1) if peak_rss_mb is not None else None,
        "device": device,
        "threads": threads,
        "elapsed_seconds": round(float(elapsed_seconds), 1) if elapsed_seconds is not None else None,
        "eta_seconds": int(eta_seconds) if eta_seconds is not None else None,
        "last_checkpoint": last_checkpoint,
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _pairs_hash() -> str | None:
    """Content hash of the training-data files.

    Returns ``None`` when the real JSONL dataset is absent (tests / synthetic
    data) — in which case the tokenized-cache is disabled entirely.
    """
    import hashlib

    if not PAIRS_FILE.exists():
        return None
    h = hashlib.sha256()
    for f in (PAIRS_FILE, SPLIT_FILE):
        h.update(str(f).encode("utf-8"))
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


class MarginRankingLossTrainer:
    """Train a cross-encoder with margin ranking loss.

    For each (query, positive, negative) triple, the loss encourages:
        score(query, positive) > score(query, negative) + margin
    """

    def __init__(self, model_name: str, max_len: int = 256, margin: float = 1.0,
                 device: str | None = None):
        self.model_name = model_name
        self.max_len = max_len
        self.margin = margin
        self.model = None
        self.tokenizer = None
        self.device = device or detect_device()

    def _load_model(self):
        if self.model is not None:
            return
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            self.model_name, num_labels=1
        )
        self.model.to(self.device)

    def train(
        self,
        train_pairs: list[dict],
        val_pairs: list[dict],
        epochs: int = 3,
        lr: float = 2e-5,
        batch_size: int = 24,
        loss_type: str = "margin",
        curriculum: bool = False,
        output_dir: Path | None = None,
        max_steps: int | None = None,
        val_cap: int | None = None,
        val_batch_size: int | None = None,
        resume: bool = True,
        save_every: int = 25,
        status_every: int = 5,
        status_file: Path | None = None,
    ) -> dict:
        """Train the model and return training metrics.

        Args:
            train_pairs: list of {query, positive, negative, tier}
            val_pairs: validation pairs
            epochs: number of training epochs
            lr: learning rate
            batch_size: batch size
            loss_type: 'margin' or 'contrastive'
            curriculum: if True, progressively increase difficulty
            output_dir: where to save the model
            val_cap: when set, validate on a stride-sampled subset of at most
                this many pairs each epoch (distribution-preserving).  Loss
                on a subset is a fine early-stopping signal and avoids ~6 min
                of per-epoch validation on 8 GB laptops (1,920 pairs @ batch
                24 = 80 batches x 2 forwards).  The *final* model evaluation
                always uses the full untouched val/test sets — this only
                affects training-time model selection.
            val_batch_size: batch size for the validation loader (larger is
                faster on CPU; defaults to *batch_size*).
            resume: when True (default) and ``output_dir/train_state.pt``
                exists, continue from it instead of starting over.  A laptop
                that is closed / restarted / Ctrl+C'd loses at most
                ``save_every`` steps of progress, and restarts skip the
                ~3-4 min re-tokenization via the tokenized cache.  The
                resumed run is bit-identical inside the partially-completed
                epoch (model + AdamW + RNG + epoch shuffle order are saved).
            save_every: save a resumable checkpoint every this many optimizer
                steps (default 25 ≈ 5-10 min of progress on this hardware).
            status_every: write the pollable ``training_status.json`` every
                this many steps (default 5).
            status_file: where to write the pollable status JSON (defaults
                to ``output_dir/training_status.json``).
        """
        import torch
        from torch.utils.data import DataLoader, Dataset


        # Bound the thread pools unconditionally: even on a (rare) discrete
        # GPU the data-prep/tokeniser work is CPU-bound, and on CPU the
        # one-thread-per-core default swap-thrashes small laptops.
        configure_threads()
        n_threads = int(torch.get_num_threads())
        self._load_model()
        if status_file is None and output_dir is not None:
            status_file = output_dir / "training_status.json"

        # RAM pre-flight: warn before tokenising the whole corpus if the host
        # is already close to its limit (8 GB class machines swap-thrash).
        try:
            import psutil

            avail_gb = psutil.virtual_memory().available / (1024 ** 3)
            if avail_gb < 2.0:
                pass
        except Exception:
            pass

        def _peak_rss_mb() -> float:
            """Return current process RSS in MB."""
            try:
                import psutil
                return psutil.Process().memory_info().rss / (1024 * 1024)
            except ImportError:
                try:
                    import resource
                    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    if sys.platform == "darwin":
                        return rss / (1024 * 1024)
                    return rss / 1024
                except Exception:
                    return -1.0

        class PairDataset(Dataset):
            def __init__(self, pairs, tokenizer, max_len):
                self.pairs = pairs
                self.tokenizer = tokenizer
                self.max_len = max_len

            def __len__(self):
                return len(self.pairs)

            def __getitem__(self, idx):
                p = self.pairs[idx]
                pos_enc = self.tokenizer(
                    p["query"], p["positive"],
                    padding="max_length", truncation=True,
                    max_length=self.max_len, return_tensors="pt",
                )
                neg_enc = self.tokenizer(
                    p["query"], p["negative"],
                    padding="max_length", truncation=True,
                    max_length=self.max_len, return_tensors="pt",
                )
                return {
                    "pos_input_ids": pos_enc["input_ids"].squeeze(0),
                    "pos_attention_mask": pos_enc["attention_mask"].squeeze(0),
                    "neg_input_ids": neg_enc["input_ids"].squeeze(0),
                    "neg_attention_mask": neg_enc["attention_mask"].squeeze(0),
                    "tier": p.get("tier", 1),
                }

        # Progressive curriculum: epoch 0 -> T1 only, epoch 1 -> T1+T2,
        # epoch 2 -> T1+T2+T3.  Falls back to all-tiers shuffled when
        # curriculum is disabled or only one epoch is requested.
        n_train = len(train_pairs)
        max_possible_steps = epochs * max(n_train // batch_size, 1)
        total_steps = max_possible_steps
        if max_steps is not None and max_steps < max_possible_steps:
            total_steps = max_steps

        # Pre-tokenise once: all pairs are short (<=256).  This mirrors
        # finetune_ce.py's up-front encoding and avoids per-item tokenisation
        # in the DataLoader hot path.  The tensors are cached to disk so a
        # laptop restart resumes without paying the ~3-4 min tokenise cost
        # (cache is validated against a content hash of the data files).
        cache_path = None
        pairs_hash = None
        if output_dir is not None:
            cache_path = output_dir / "tokenized_cache.pt"
            pairs_hash = _pairs_hash()
        cached = None
        if cache_path is not None and pairs_hash is not None and cache_path.exists():
            try:
                cached = torch.load(cache_path, map_location="cpu", weights_only=False)
                if (
                    cached.get("pairs_hash") != pairs_hash
                    or cached.get("max_len") != self.max_len
                    or cached.get("n") != len(train_pairs)
                ):
                    cached = None
            except Exception:
                cached = None
        if cached is not None:
            all_pos = cached["all_pos"]
            all_neg = cached["all_neg"]
            all_y = cached["all_y"]
        else:
            write_training_status(
                status_file, status="training", phase="tokenizing",
                epoch=0, epochs=epochs, global_step=0, total_steps=total_steps,
                device=self.device, threads=n_threads,
            )
            all_pos = self.tokenizer(
                [p["query"] for p in train_pairs],
                [p["positive"] for p in train_pairs],
                padding="max_length", truncation=True,
                max_length=self.max_len, return_tensors="pt",
            )
            all_neg = self.tokenizer(
                [p["query"] for p in train_pairs],
                [p["negative"] for p in train_pairs],
                padding="max_length", truncation=True,
                max_length=self.max_len, return_tensors="pt",
            )
            all_y = torch.tensor(
                [1.0] * len(train_pairs), dtype=torch.float32
            )
            if cache_path is not None and pairs_hash is not None:
                tmp = cache_path.with_name(cache_path.name + ".tmp")
                torch.save(
                    {
                        "pairs_hash": pairs_hash,
                        "max_len": self.max_len,
                        "n": len(train_pairs),
                        "all_pos": all_pos,
                        "all_neg": all_neg,
                        "all_y": all_y,
                    },
                    tmp,
                )
                os.replace(tmp, cache_path)

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=lr)
        # Ensure scheduler gets at least 2 steps to avoid ZeroDivisionError
        # in OneCycleLR phase-boundary computation (PyTorch edge case).
        sched_total = max(total_steps, 2)
        if sched_total < 10:
            # Calibration / tiny runs: OneCycleLR's phase math divides by
            # (end_step - start_step), which is 0 when the anneal phase is
            # empty (small totals).  Fall back to the linear + warmup
            # schedule that finetune_ce.py (v1) proved robust on this machine.
            from transformers import get_linear_schedule_with_warmup

            scheduler = get_linear_schedule_with_warmup(
                optimizer,
                num_warmup_steps=max(int(0.1 * sched_total), 1),
                num_training_steps=sched_total,
            )
        else:
            # Full runs: OneCycleLR with a 10% warmup phase (identical to the
            # original trainer's schedule once totals are large enough that
            # 2.0/sched_total < 0.1).
            pct_start = max(0.1, 2.0 / sched_total)
            scheduler = torch.optim.lr_scheduler.OneCycleLR(
                optimizer, max_lr=lr, total_steps=sched_total,
                pct_start=pct_start,
            )



        # ---- Resume from checkpoint (crash-safe training on laptops) ----
        checkpoint_path = output_dir / "train_state.pt" if output_dir else None
        ckpt = None
        if resume and checkpoint_path is not None:
            ckpt = load_training_checkpoint(checkpoint_path)

        start_epoch = 0
        start_j = 0
        global_step = 0
        best_val_loss = float("inf")
        best_state = None
        history = {"train_loss": [], "val_loss": [], "epoch": [], "peak_rss_mb": []}
        resumed = ckpt is not None
        t_start = time.time()

        if ckpt is not None:
            global_step = int(ckpt["global_step"])
            if global_step >= total_steps:
                # The run already reached the target step count — nothing to do.
                return {
                    "epochs": epochs,
                    "loss_type": loss_type,
                    "curriculum": curriculum,
                    "best_val_loss": float(ckpt["best_val_loss"]),
                    "history": ckpt["history"],
                    "n_train": len(train_pairs),
                    "n_val": len(val_pairs),
                    "max_steps": max_steps,
                    "total_steps_target": total_steps,
                    "global_steps_completed": global_step,
                    "resumed": True,
                    "checkpoint_path": str(checkpoint_path),
                }
            start_epoch = int(ckpt["epoch"])
            if start_epoch >= epochs:
                # All epochs already completed (e.g. the checkpoint is a
                # previous run's epoch-end save for the last epoch) — nothing
                # left to train.  The global_step >= total_steps check above
                # covers the same-budget re-run; this covers extending past
                # the completed epoch budget.
                return {
                    "epochs": epochs,
                    "loss_type": loss_type,
                    "curriculum": curriculum,
                    "best_val_loss": float(ckpt["best_val_loss"]),
                    "history": ckpt["history"],
                    "n_train": len(train_pairs),
                    "n_val": len(val_pairs),
                    "max_steps": max_steps,
                    "total_steps_target": total_steps,
                    "global_steps_completed": global_step,
                    "resumed": True,
                    "checkpoint_path": str(checkpoint_path),
                }
            start_j = int(ckpt["steps_in_epoch"])
            best_val_loss = float(ckpt["best_val_loss"])
            history = ckpt["history"]
            self.model.load_state_dict(ckpt["model_state"])
            # Restore the AdamW per-parameter moments (exp_avg / exp_avg_sq /
            # step) but keep THIS run's param-group hyperparameters.  A
            # checkpoint saved under a different scheduler config (e.g. the
            # linear fallback used for small step budgets, which never adds
            # max_lr and can save lr=0.0 at the decay tail) would otherwise
            # clobber the current scheduler's keys and crash OneCycleLR.
            opt_state = ckpt["optimizer_state"]
            current_hyper = [
                {k: v for k, v in g.items() if k != "params"}
                for g in optimizer.param_groups
            ]
            optimizer.load_state_dict(opt_state)
            for group, hyper in zip(optimizer.param_groups, current_hyper, strict=False):
                group.update(hyper)
            torch.set_rng_state(ckpt["torch_rng"])
            random.setstate(ckpt["python_rng"])
            # Rebuild the scheduler for the current step budget and
            # fast-forward it to the resumed step.  The loop calls
            # ``optimizer.step()`` then ``scheduler.step()``, so after N
            # completed steps ``last_epoch == N`` and ``group['lr']`` holds
            # the LR for step N+1; presetting both reproduces the exact LR
            # sequence of the uninterrupted run.  (Calling
            # ``scheduler.step()`` repeatedly instead hits OneCycleLR's
            # un-initialized-phase KeyError — it expects ``optimizer.step()``
            # to run first.)
            scheduler.last_epoch = global_step
            for group, lr in zip(optimizer.param_groups, scheduler.get_last_lr(), strict=False):
                group["lr"] = float(lr)
        else:
            write_training_status(
                status_file, status="training", phase="initializing",
                epoch=0, epochs=epochs, global_step=0, total_steps=total_steps,
                device=self.device, threads=n_threads,
            )

        # Validation DataLoader (reuses PairDataset for tokenisation).
        # Optionally cap to a stride-sampled subset so per-epoch validation
        # does not dominate wall-clock on slow laptops.
        val_sample = val_pairs
        if val_cap is not None and len(val_pairs) > val_cap:
            stride = max(len(val_pairs) // val_cap, 1)
            val_sample = val_pairs[::stride][:val_cap]
        val_ds = PairDataset(val_sample, self.tokenizer, self.max_len)
        val_loader = DataLoader(
            val_ds, batch_size=val_batch_size or batch_size, shuffle=False
        )

        self.model.train()
        epoch = start_epoch
        j = 0
        order = None
        last_save_ts: str | None = None
        try:
            for epoch in range(start_epoch, epochs):
                epoch_loss = 0.0
                n_batches = 0
                t0 = time.time()

                # Progressive curriculum: filter pairs by tier per epoch
                if curriculum:
                    max_tier = epoch + 1  # epoch 0 -> tier 1; epoch 1 -> tiers 1-2; epoch 2 -> all
                    epoch_pairs_idx = [
                        i for i, p in enumerate(train_pairs)
                        if p.get("tier", 1) <= max_tier
                    ]
                else:
                    epoch_pairs_idx = list(range(len(train_pairs)))

                if not curriculum:
                    import random as _random
                    _random.shuffle(epoch_pairs_idx)

                # Build per-epoch DataLoader from the pre-tokenised tensors
                if epoch_pairs_idx:
                    pos_ids = all_pos["input_ids"][epoch_pairs_idx]
                    pos_mask = all_pos["attention_mask"][epoch_pairs_idx]
                    neg_ids = all_neg["input_ids"][epoch_pairs_idx]
                    neg_mask = all_neg["attention_mask"][epoch_pairs_idx]
                    ys = all_y[epoch_pairs_idx]
                else:
                    pos_ids = all_pos["input_ids"]
                    pos_mask = all_pos["attention_mask"]
                    neg_ids = all_neg["input_ids"]
                    neg_mask = all_y
                    ys = all_y

                n_epoch = len(epoch_pairs_idx)
                if (
                    resumed and epoch == start_epoch and ckpt is not None
                    and ckpt.get("order_epoch") == start_epoch
                    and "epoch_order" in ckpt
                    and ckpt["epoch_order"] is not None
                    and len(ckpt["epoch_order"]) == n_epoch
                ):
                    # Reuse the interrupted run's shuffle so the resumed epoch is
                    # bit-identical to what the uninterrupted run would have done.
                    order = ckpt["epoch_order"]
                else:
                    order = torch.randperm(n_epoch)
                pos_ids = pos_ids[order]
                pos_mask = pos_mask[order]
                neg_ids = neg_ids[order]
                neg_mask = neg_mask[order]
                ys = ys[order]

                j0 = start_j if (resumed and epoch == start_epoch) else 0
                for j in range(j0, n_epoch, batch_size):
                    bi = slice(j, j + batch_size)
                    pos_ids_b = pos_ids[bi].to(self.device)
                    pos_mask_b = pos_mask[bi].to(self.device)
                    neg_ids_b = neg_ids[bi].to(self.device)
                    neg_mask_b = neg_mask[bi].to(self.device)

                    # Score positive and negative
                    pos_out = self.model(input_ids=pos_ids_b, attention_mask=pos_mask_b)
                    neg_out = self.model(input_ids=neg_ids_b, attention_mask=neg_mask_b)

                    pos_scores = pos_out.logits.squeeze(-1)
                    neg_scores = neg_out.logits.squeeze(-1)

                    if loss_type == "margin":
                        # Margin ranking loss: encourage pos > neg + margin
                        target = torch.ones_like(pos_scores)
                        loss = torch.nn.functional.margin_ranking_loss(
                            pos_scores, neg_scores, target, margin=self.margin,
                            reduction="mean",
                        )
                    elif loss_type == "contrastive":
                        # Contrastive loss: -log(exp(pos) / (exp(pos) + exp(neg)))
                        log_denom = torch.logsumexp(
                            torch.stack([pos_scores, neg_scores], dim=-1), dim=-1,
                        )
                        loss = -(pos_scores - log_denom).mean()
                    else:
                        # Pointwise BCE (baseline)
                        logits = torch.cat([pos_scores, neg_scores])
                        targets = torch.cat([
                            torch.ones_like(pos_scores),
                            torch.zeros_like(neg_scores),
                        ])
                        loss = torch.nn.functional.binary_cross_entropy_with_logits(
                            logits, targets,
                        )

                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad()

                    epoch_loss += loss.item()
                    n_batches += 1
                    global_step += 1

                    # Pollable status + crash-safe checkpoint (laptop-friendly).
                    if output_dir and (global_step % status_every) == 0:
                        elapsed = time.time() - t_start
                        s_per_step = elapsed / max(global_step, 1)
                        write_training_status(
                            status_file, status="training", phase="training",
                            epoch=epoch + 1, epochs=epochs,
                            global_step=global_step, total_steps=total_steps,
                            train_loss=epoch_loss / max(n_batches, 1),
                            best_val_loss=best_val_loss,
                            peak_rss_mb=_peak_rss_mb(),
                            device=self.device, threads=n_threads,
                            elapsed_seconds=elapsed,
                            eta_seconds=max(
                                int(s_per_step * (total_steps - global_step)), 0
                            ),
                            last_checkpoint=last_save_ts,
                        )
                    if output_dir and (global_step % save_every) == 0:
                        last_save_ts = datetime.now(UTC).isoformat(timespec="seconds")
                        save_training_checkpoint(
                            checkpoint_path, model=self.model, optimizer=optimizer,
                            epoch=epoch, steps_in_epoch=min(j + batch_size, n_epoch),
                            global_step=global_step, best_val_loss=best_val_loss,
                            history=history, loss_type=loss_type,
                            curriculum=curriculum, epochs=epochs,
                            max_steps=max_steps,
                            torch_rng=torch.get_rng_state(),
                            python_rng=random.getstate(),
                            epoch_order=order, order_epoch=epoch,
                            device=self.device,
                        )

                    # Early break for calibration (--max_steps)
                    if max_steps is not None and global_step >= max_steps:
                        break

                avg_train_loss = epoch_loss / max(n_batches, 1)

                # Validation
                val_loss = self._validate(val_loader, loss_type)

                history["train_loss"].append(avg_train_loss)
                history["val_loss"].append(val_loss)
                history["epoch"].append(epoch + 1)

                elapsed = time.time() - t0
                peak_rss = _peak_rss_mb()
                history["peak_rss_mb"].append(round(peak_rss, 1))

                # Save best
                if val_loss < best_val_loss:
                    best_val_loss = val_loss
                    if output_dir:
                        output_dir.mkdir(parents=True, exist_ok=True)
                        self.model.save_pretrained(output_dir.as_posix())
                        self.tokenizer.save_pretrained(output_dir.as_posix())
                        best_state = {k: v.clone() for k, v in self.model.state_dict().items()}

                # Epoch-end status + checkpoint (resume point = next epoch, batch 0).
                last_save_ts = datetime.now(UTC).isoformat(timespec="seconds")
                write_training_status(
                    status_file, status="training", phase="validated",
                    epoch=epoch + 1, epochs=epochs,
                    global_step=global_step, total_steps=total_steps,
                    train_loss=avg_train_loss, val_loss=val_loss,
                    best_val_loss=best_val_loss, peak_rss_mb=peak_rss,
                    device=self.device, threads=n_threads,
                    elapsed_seconds=time.time() - t_start,
                    last_checkpoint=last_save_ts,
                )
                if checkpoint_path is not None:
                    save_training_checkpoint(
                        checkpoint_path, model=self.model, optimizer=optimizer,
                        epoch=epoch + 1, steps_in_epoch=0,
                        global_step=global_step, best_val_loss=best_val_loss,
                        history=history, loss_type=loss_type,
                        curriculum=curriculum, epochs=epochs,
                        max_steps=max_steps,
                        torch_rng=torch.get_rng_state(),
                        python_rng=random.getstate(),
                        epoch_order=order, order_epoch=epoch,
                        device=self.device,
                    )

                # Early break if we hit the calibration step cap
                if max_steps is not None and global_step >= max_steps:
                    break
        except KeyboardInterrupt:
            # Ctrl+C is crash-safe too: persist the state so the next run
            # resumes from here instead of losing the whole run.
            if checkpoint_path is not None:
                with contextlib.suppress(Exception):
                    save_training_checkpoint(
                        checkpoint_path, model=self.model, optimizer=optimizer,
                        epoch=epoch, steps_in_epoch=min(j + batch_size, n_epoch),
                        global_step=global_step, best_val_loss=best_val_loss,
                        history=history, loss_type=loss_type,
                        curriculum=curriculum, epochs=epochs,
                        max_steps=max_steps,
                        torch_rng=torch.get_rng_state(),
                        python_rng=random.getstate(),
                        epoch_order=order, order_epoch=epoch,
                        device=self.device,
                    )
            write_training_status(
                status_file, status="interrupted", phase="interrupted",
                epoch=epoch + 1, epochs=epochs,
                global_step=global_step, total_steps=total_steps,
                best_val_loss=best_val_loss, peak_rss_mb=_peak_rss_mb(),
                device=self.device, threads=n_threads,
                elapsed_seconds=time.time() - t_start,
            )
            raise

        # Restore best
        if best_state is not None:
            self.model.load_state_dict(best_state)

        # Final status.  Note: the *last real* checkpoint (epoch-end or
        # mid-epoch save) is deliberately left in place rather than replaced
        # by a fake 'all epochs done' state — that way a later run with a
        # larger step/epoch budget can continue from the exact stopping
        # point, while a same-budget re-run hits the 'already complete'
        # guard and exits immediately.
        last_save_ts = datetime.now(UTC).isoformat(timespec="seconds")
        write_training_status(
            status_file, status="done", phase="complete",
            epoch=epochs, epochs=epochs,
            global_step=global_step, total_steps=total_steps,
            train_loss=history["train_loss"][-1] if history["train_loss"] else None,
            val_loss=history["val_loss"][-1] if history["val_loss"] else None,
            best_val_loss=best_val_loss,
            peak_rss_mb=_peak_rss_mb(),
            device=self.device, threads=n_threads,
            elapsed_seconds=time.time() - t_start,
            last_checkpoint=last_save_ts,
        )

        return {
            "epochs": epochs,
            "loss_type": loss_type,
            "curriculum": curriculum,
            "best_val_loss": best_val_loss,
            "history": history,
            "n_train": len(train_pairs),
            "n_val": len(val_pairs),
            "max_steps": max_steps,
            "total_steps_target": total_steps,
            "global_steps_completed": global_step,
            "peak_rss_mb": round(max(history.get("peak_rss_mb", [0.0]), default=0.0), 1),
            "resumed": resumed,
            "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
            "status_file": str(status_file) if status_file else None,
        }

    def _validate(self, loader, loss_type: str) -> float:
        """Compute validation loss."""
        import torch

        self.model.eval()
        total_loss = 0.0
        n = 0
        with torch.no_grad():
            for batch in loader:
                pos_out = self.model(
                    input_ids=batch["pos_input_ids"].to(self.device),
                    attention_mask=batch["pos_attention_mask"].to(self.device),
                )
                neg_out = self.model(
                    input_ids=batch["neg_input_ids"].to(self.device),
                    attention_mask=batch["neg_attention_mask"].to(self.device),
                )
                pos_scores = pos_out.logits.squeeze(-1)
                neg_scores = neg_out.logits.squeeze(-1)

                if loss_type == "margin":
                    target = torch.ones_like(pos_scores)
                    loss = torch.nn.functional.margin_ranking_loss(
                        pos_scores, neg_scores, target, margin=self.margin,
                    )
                elif loss_type == "contrastive":
                    log_denom = torch.logsumexp(
                        torch.stack([pos_scores, neg_scores], dim=-1), dim=-1
                    )
                    loss = -(pos_scores - log_denom).mean()
                else:
                    logits = torch.cat([pos_scores, neg_scores])
                    targets = torch.cat([
                        torch.ones_like(pos_scores),
                        torch.zeros_like(neg_scores),
                    ])
                    loss = torch.nn.functional.binary_cross_entropy_with_logits(logits, targets)

                total_loss += loss.item()
                n += 1

        self.model.train()
        return total_loss / max(n, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ranking-loss CE trainer")
    parser.add_argument(
        "--variant",
        choices=["baseline", "model_a", "model_b", "model_c", "model_d", "all"],
        default="model_d",
        help="Which model variant to train",
    )
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--loss", choices=["margin", "contrastive", "pointwise"], default="margin")
    parser.add_argument("--curriculum", action="store_true", help="Use progressive curriculum training (T1->T2->T3) for model_d")
    parser.add_argument("--max-steps", type=int, default=None, help="Max training steps (for calibration; breaks early)")
    parser.add_argument("--device", type=str, default=None,
                        help="Override device auto-detection (e.g. 'cpu', 'privateuseone:0', 'cuda:0')")
    parser.add_argument("--threads", type=int, default=4,
                        help="Torch intra-op thread cap (default 4; the measured sweet spot)")
    args = parser.parse_args()

    # Configure threads before any torch import / parallel work starts.
    configure_threads(args.threads)

    pairs = load_pairs()
    if not pairs:
        return 1

    splits = load_splits()
    train_qids = set(splits.get("train_qids", []))
    val_qids = set(splits.get("val_qids", []))

    # Split by question_id
    train_pairs = [p for p in pairs if p["question_id"] in train_qids]
    val_pairs = [p for p in pairs if p["question_id"] in val_qids]

    if not train_pairs:
        # Fallback: 80/20 random split
        import random
        rng = random.Random(20260815)
        all_qids = list({p["question_id"] for p in pairs})
        rng.shuffle(all_qids)
        split_idx = int(0.8 * len(all_qids))
        train_qids = set(all_qids[:split_idx])
        val_qids = set(all_qids[split_idx:])
        train_pairs = [p for p in pairs if p["question_id"] in train_qids]
        val_pairs = [p for p in pairs if p["question_id"] in val_qids]

    variants = [args.variant] if args.variant != "all" else [
        "model_a", "model_b", "model_c", "model_d"
    ]

    results = {}
    for variant in variants:

        variant_pairs = filter_by_variant(train_pairs, variant)
        variant_val = filter_by_variant(val_pairs, variant)

        if not variant_pairs:
            continue

        output_dir = MODELS_DIR / f"legal_ce_v2_{variant}"
        trainer = MarginRankingLossTrainer(
            model_name=BASE_MODEL,
            max_len=MAX_LEN,
            margin=MARGIN,
            device=args.device,
        )

        use_curriculum = variant == "model_d" and args.curriculum
        result = trainer.train(
            train_pairs=variant_pairs,
            val_pairs=variant_val,
            epochs=args.epochs,
            lr=LR,
            batch_size=BATCH_SIZE,
            loss_type=args.loss,
            curriculum=use_curriculum,
            output_dir=output_dir,
            max_steps=args.max_steps,
        )

        result["variant"] = variant
        result["output_dir"] = output_dir.as_posix()
        results[variant] = result

        # Save variant summary
        summary_file = MODELS_DIR / f"ce_train_summary_{variant}.json"
        summary_file.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # Write combined summary
    (MODELS_DIR / "ce_train_results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) if results else "{}",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
import json
import sys
import time
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


class MarginRankingLossTrainer:
    """Train a cross-encoder with margin ranking loss.

    For each (query, positive, negative) triple, the loss encourages:
        score(query, positive) > score(query, negative) + margin
    """

    def __init__(self, model_name: str, max_len: int = 256, margin: float = 1.0):
        self.model_name = model_name
        self.max_len = max_len
        self.margin = margin
        self.model = None
        self.tokenizer = None

    def _load_model(self):
        if self.model is not None:
            return
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            self.model_name, num_labels=1
        )

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
        """
        import torch
        from torch.utils.data import DataLoader, Dataset

        torch.set_num_threads(4)
        self._load_model()

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

        # Sort by tier for curriculum training
        if curriculum:
            train_pairs_sorted = sorted(train_pairs, key=lambda x: x.get("tier", 1))
        else:
            train_pairs_sorted = list(train_pairs)

        train_ds = PairDataset(train_pairs_sorted, self.tokenizer, self.max_len)
        train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=not curriculum)

        val_ds = PairDataset(val_pairs, self.tokenizer, self.max_len)
        val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=lr)

        # Linear warmup
        total_steps = epochs * max(len(train_loader), 1)
        warmup_steps = int(0.1 * total_steps)
        scheduler = torch.optim.lr_scheduler.OneCycleLR(
            optimizer, max_lr=lr, total_steps=total_steps,
            pct_start=warmup_steps / max(total_steps, 1),
        )

        history = {"train_loss": [], "val_loss": [], "epoch": []}
        best_val_loss = float("inf")
        best_state = None

        self.model.train()
        for epoch in range(epochs):
            epoch_loss = 0.0
            n_batches = 0
            t0 = time.time()

            for batch in train_loader:
                pos_ids = batch["pos_input_ids"]
                pos_mask = batch["pos_attention_mask"]
                neg_ids = batch["neg_input_ids"]
                neg_mask = batch["neg_attention_mask"]

                # Score positive and negative
                pos_out = self.model(input_ids=pos_ids, attention_mask=pos_mask)
                neg_out = self.model(input_ids=neg_ids, attention_mask=neg_mask)

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
                        torch.stack([pos_scores, neg_scores], dim=-1), dim=-1
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

            avg_train_loss = epoch_loss / max(n_batches, 1)

            # Validation
            val_loss = self._validate(val_loader, loss_type)

            history["train_loss"].append(avg_train_loss)
            history["val_loss"].append(val_loss)
            history["epoch"].append(epoch + 1)

            elapsed = time.time() - t0
            print(
                f"  epoch {epoch+1}/{epochs} train_loss={avg_train_loss:.4f} "
                f"val_loss={val_loss:.4f} ({elapsed:.0f}s)",
                flush=True,
            )

            # Save best
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                if output_dir:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    self.model.save_pretrained(output_dir.as_posix())
                    self.tokenizer.save_pretrained(output_dir.as_posix())
                    best_state = {k: v.clone() for k, v in self.model.state_dict().items()}

        # Restore best
        if best_state is not None:
            self.model.load_state_dict(best_state)

        return {
            "epochs": epochs,
            "loss_type": loss_type,
            "curriculum": curriculum,
            "best_val_loss": best_val_loss,
            "history": history,
            "n_train": len(train_pairs),
            "n_val": len(val_pairs),
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
                    input_ids=batch["pos_input_ids"],
                    attention_mask=batch["pos_attention_mask"],
                )
                neg_out = self.model(
                    input_ids=batch["neg_input_ids"],
                    attention_mask=batch["neg_attention_mask"],
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
    parser.add_argument("--curriculum", action="store_true", help="Use curriculum training for model_d")
    args = parser.parse_args()

    pairs = load_pairs()
    if not pairs:
        print("[ranking_loss_trainer] No pairs found. Run: python -m evaluation.pairwise_dataset", file=sys.stderr)
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
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Training variant: {variant}", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)

        variant_pairs = filter_by_variant(train_pairs, variant)
        variant_val = filter_by_variant(val_pairs, variant)

        if not variant_pairs:
            print(f"  No pairs for {variant}, skipping", file=sys.stderr)
            continue

        output_dir = MODELS_DIR / f"legal_ce_v2_{variant}"
        trainer = MarginRankingLossTrainer(
            model_name=BASE_MODEL,
            max_len=MAX_LEN,
            margin=MARGIN,
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
        )

        result["variant"] = variant
        result["output_dir"] = output_dir.as_posix()
        results[variant] = result

        # Save variant summary
        summary_file = MODELS_DIR / f"ce_train_summary_{variant}.json"
        summary_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"  Saved: {output_dir}", file=sys.stderr)

    # Write combined summary
    (MODELS_DIR / "ce_train_results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) if results else "{}",
        encoding="utf-8",
    )
    print(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

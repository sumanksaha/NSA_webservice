"""Pairwise training dataset — three-tier negatives for CE fine-tuning.

Reads the hard-negative mining output and constructs pairwise training
examples following the master plan's three-tier negative hierarchy:

  Tier 1 (Random):    control — obvious negatives, no legal similarity
  Tier 2 (Semantic):  same topic/wording/Act, related provisions
  Tier 3 (Adversarial): same Act/chapter/section family — the model's
                       actual confusion points

Supports curriculum training modes:
  • uniform:     all tiers mixed equally
  • progressive: easy → hard (start with T1, anneal to T3)
  • hard_only:   only T2+T3

Output:
  evaluation/out/cache/pairwise_training_v2.jsonl — (query, pos, neg, tier)
  evaluation/out/cache/pairwise_training_v2_stats.json — dataset statistics
  evaluation/out/cache/pairwise_train_split.json — train/val/test split

Usage:
    python -m evaluation.pairwise_dataset
    python -m evaluation.pairwise_dataset --mode hard_only
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / "evaluation" / "out"
CACHE_DIR = OUT_DIR / "cache"
MINING_FILE = OUT_DIR / "ceiling_v5" / "hard_negative_mining.jsonl"
OUT_FILE = CACHE_DIR / "pairwise_training_v2.jsonl"
STATS_FILE = CACHE_DIR / "pairwise_training_v2_stats.json"
SPLIT_FILE = CACHE_DIR / "pairwise_train_split.json"

SEED = 20260815
TRAIN_RATIO = 0.7
VAL_RATIO = 0.15
TEST_RATIO = 0.15


def build_pairwise_examples(
    mode: str = "uniform",
    max_positives: int = 8,
    max_negatives_per_tier: int = 8,
    seed: int = SEED,
) -> list[dict]:
    """Build pairwise (query, positive, negative, tier) examples.

    Args:
        mode: 'uniform', 'progressive', or 'hard_only'
        max_positives: max positive examples per question
        max_negatives_per_tier: max negatives per tier per question
        seed: random seed for reproducibility

    Returns:
        List of pairwise training examples.
    """
    if not MINING_FILE.exists():
        print(f"[pairwise_dataset] Mining file not found: {MINING_FILE}", file=sys.stderr)
        print("  Run: python -m evaluation.hard_negative_miner --offline", file=sys.stderr)
        return []

    examples = []
    rng = random.Random(seed)

    with open(MINING_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            query = rec["query"]
            positives = rec.get("positives", [])[:max_positives]
            negatives = rec.get("negatives", [])

            if not positives or not negatives:
                continue

            # Group negatives by tier
            tier_negatives: dict[int, list] = {1: [], 2: [], 3: []}
            for neg in negatives:
                t = neg.get("tier", 1)
                tier_negatives[t].append(neg)

            # Build pairs for each positive
            for pos in positives:
                pos_text = pos.get("text", "")
                if not pos_text:
                    continue

                for tier in (1, 2, 3):
                    tier_negs = tier_negatives[tier]
                    if not tier_negs:
                        continue

                    # For uniform mode: take from all tiers
                    # For progressive: weight by tier
                    # For hard_only: skip tier 1
                    if mode == "hard_only" and tier == 1:
                        continue

                    # Sample negatives for this tier
                    n_sample = min(max_negatives_per_tier, len(tier_negs))
                    sampled = rng.sample(tier_negs, n_sample) if len(tier_negs) > n_sample else tier_negs

                    for neg in sampled:
                        neg_text = neg.get("text", "")
                        if not neg_text:
                            continue

                        examples.append({
                            "query": query,
                            "positive": pos_text,
                            "negative": neg_text,
                            "tier": tier,
                            "tier_label": {
                                1: "random",
                                2: "semantic_hard",
                                3: "adversarial_legal",
                            }.get(tier, "unknown"),
                            "question_id": rec["question_id"],
                            "gold_unit": pos.get("gold_unit", ""),
                            "neg_chunk_id": neg.get("chunk_id", ""),
                            "neg_features": neg.get("features", {}),
                        })

    # Shuffle
    rng.shuffle(examples)
    return examples


def split_dataset(
    examples: list[dict],
    seed: int = SEED,
) -> dict[str, list[dict]]:
    """Split into train/val/test by question_id (no data leakage).

    Questions are assigned to splits, not individual examples, so all
    pairs from one question stay in the same split.
    """
    rng = random.Random(seed)

    # Group by question_id
    by_qid: dict[str, list[dict]] = {}
    for ex in examples:
        qid = ex["question_id"]
        by_qid.setdefault(qid, []).append(ex)

    qids = list(by_qid.keys())
    rng.shuffle(qids)

    n = len(qids)
    n_train = int(n * TRAIN_RATIO)
    n_val = int(n * VAL_RATIO)

    train_qids = qids[:n_train]
    val_qids = qids[n_train:n_train + n_val]
    test_qids = qids[n_train + n_val:]

    splits = {
        "train": [],
        "val": [],
        "test": [],
    }
    for qid in train_qids:
        splits["train"].extend(by_qid[qid])
    for qid in val_qids:
        splits["val"].extend(by_qid[qid])
    for qid in test_qids:
        splits["test"].extend(by_qid[qid])

    split_info = {
        "train_questions": len(train_qids),
        "val_questions": len(val_qids),
        "test_questions": len(test_qids),
        "train_pairs": len(splits["train"]),
        "val_pairs": len(splits["val"]),
        "test_pairs": len(splits["test"]),
        "train_qids": sorted(train_qids),
        "val_qids": sorted(val_qids),
        "test_qids": sorted(test_qids),
    }

    return splits, split_info


def main() -> int:
    parser = argparse.ArgumentParser(description="Build pairwise training dataset")
    parser.add_argument("--mode", choices=["uniform", "progressive", "hard_only"], default="uniform")
    parser.add_argument("--max-positives", type=int, default=8)
    parser.add_argument("--max-negatives-per-tier", type=int, default=8)
    args = parser.parse_args()

    examples = build_pairwise_examples(
        mode=args.mode,
        max_positives=args.max_positives,
        max_negatives_per_tier=args.max_negatives_per_tier,
    )

    if not examples:
        print("[pairwise_dataset] No examples generated", file=sys.stderr)
        return 1

    # Split
    splits, split_info = split_dataset(examples)

    # Write training data (all examples for the training script to split)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    # Write splits
    SPLIT_FILE.write_text(json.dumps(split_info, indent=2), encoding="utf-8")

    # Stats
    tier_counts = {1: 0, 2: 0, 3: 0}
    for ex in examples:
        tier_counts[ex["tier"]] = tier_counts.get(ex["tier"], 0) + 1

    stats = {
        "mode": args.mode,
        "total_pairs": len(examples),
        "positive_pairs": len([e for e in examples if "positive" in e]),
        "negative_pairs": len(examples),
        "tier_distribution": {
            "tier_1_random": tier_counts.get(1, 0),
            "tier_2_semantic": tier_counts.get(2, 0),
            "tier_3_adversarial": tier_counts.get(3, 0),
        },
        "unique_questions": len({e["question_id"] for e in examples}),
        "split": split_info,
    }
    STATS_FILE.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

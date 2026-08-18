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

CV2 P1 (2026-08-18): each example carries the *authoritative* legal identity
of its positive/negative chunk (``section_number`` / ``clause_number`` /
``act_name``), joined from the frozen payload index — NOT the mining record's
``section``, which predates the noise strip and the L5/L6/L7 propagation.
``--section-prefix`` bakes ``§<section> <text>`` (``§<clause>`` for
regulation chunks, per ``app/rag/retrieval/section_prefix.py``) into the
positive/negative text so the trainer's tokenized-cache hash invalidates
automatically (Option A) and the served model sees the identical signal
(G2 parity).  ``--domain-balanced`` oversamples underrepresented domains so
fssai does not dominate the loss (P4).

Output:
  evaluation/out/cache/pairwise_training_v2.jsonl — (query, pos, neg, tier)
  evaluation/out/cache/pairwise_training_v2_stats.json — dataset statistics
  evaluation/out/cache/pairwise_train_split.json — train/val/test split

Usage:
    python -m evaluation.pairwise_dataset
    python -m evaluation.pairwise_dataset --mode hard_only
    python -m evaluation.pairwise_dataset --section-prefix
    python -m evaluation.pairwise_dataset --domain-balanced
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / "evaluation" / "out"
CACHE_DIR = OUT_DIR / "cache"
MINING_FILE = OUT_DIR / "ceiling_v5" / "hard_negative_mining.jsonl"
PAYLOAD_INDEX_FILE = CACHE_DIR / "payload_index.jsonl"
OUT_FILE = CACHE_DIR / "pairwise_training_v2.jsonl"
STATS_FILE = CACHE_DIR / "pairwise_training_v2_stats.json"
SPLIT_FILE = CACHE_DIR / "pairwise_train_split.json"

SEED = 20260815
TRAIN_RATIO = 0.7
VAL_RATIO = 0.15
TEST_RATIO = 0.15


def load_payload_identity_index() -> dict[str, dict[str, Any]]:
    """chunk_id → authoritative payload identity (section/clause/act).

    The frozen payload index post-dates the noise strip and L5/L6/L7
    propagation, so it is the single source of truth for P1 identity — the
    mining record's ``section`` field was written pre-strip and is stale.
    """
    index: dict[str, dict[str, Any]] = {}
    if PAYLOAD_INDEX_FILE.exists():
        with PAYLOAD_INDEX_FILE.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                payload = rec.get("payload") or {}
                index[str(rec.get("id") or "")] = {
                    "section_number": payload.get("section_number"),
                    "clause_number": payload.get("clause_number"),
                    "act_name": payload.get("act_name") or payload.get("document_title") or "",
                }
    return index


def build_pairwise_examples(
    mode: str = "uniform",
    max_positives: int = 8,
    max_negatives_per_tier: int = 8,
    seed: int = SEED,
    section_prefix: bool = False,
    domain_balanced: bool = False,
    payload_index: dict[str, dict[str, Any]] | None = None,
) -> list[dict]:
    """Build pairwise (query, positive, negative, tier) examples.

    Args:
        mode: 'uniform', 'progressive', or 'hard_only'
        max_positives: max positive examples per question
        max_negatives_per_tier: max negatives per tier per question
        seed: random seed for reproducibility
        section_prefix: bake ``§<identity> <text>`` into pos/neg text (P1)
        domain_balanced: oversample underrepresented domains (P4)
        payload_index: authoritative chunk-id → identity map (P1); loaded
            from the frozen cache when ``None`` and the file exists

    Returns:
        List of pairwise training examples.
    """
    if not MINING_FILE.exists():
        return []

    if payload_index is None:
        payload_index = load_payload_identity_index()

    from app.rag.retrieval.section_prefix import prefix_passage

    examples = []
    rng = random.Random(seed)

    def _identity(cid: str, fallback_section: Any, fallback_act: Any) -> dict[str, Any]:
        """Authoritative identity for a chunk, falling back to mining fields."""
        rec = payload_index.get(str(cid)) if payload_index else None
        return {
            "section": rec["section_number"] if rec else fallback_section,
            "clause": rec["clause_number"] if rec else None,
            "act": rec["act_name"] if rec else (fallback_act or ""),
        }

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
                pos_id = _identity(
                    pos.get("chunk_id", ""), pos.get("section"), pos.get("act_name")
                )

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
                        neg_id = _identity(
                            neg.get("chunk_id", ""), neg.get("section"), neg.get("act_name")
                        )

                        if section_prefix:
                            pos_text_out = prefix_passage(pos_text, pos_id["section"], pos_id["clause"])
                            neg_text_out = prefix_passage(neg_text, neg_id["section"], neg_id["clause"])
                        else:
                            pos_text_out = pos_text
                            neg_text_out = neg_text

                        examples.append({
                            "query": query,
                            "positive": pos_text_out,
                            "negative": neg_text_out,
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
                            # P1 identity metadata (authoritative, post-cleanup)
                            "positive_section": pos_id["section"],
                            "positive_clause": pos_id["clause"],
                            "positive_act": pos_id["act"],
                            "negative_section": neg_id["section"],
                            "negative_clause": neg_id["clause"],
                            "negative_act": neg_id["act"],
                        })

    if domain_balanced:
        examples = _balance_domains(examples, rng)

    # Shuffle
    rng.shuffle(examples)
    return examples


def _balance_domains(examples: list[dict], rng: random.Random) -> list[dict]:
    """Oversample underrepresented domains toward the largest domain's count.

    P4: fssai is 57% of the dataset (8,340/14,629); the tier-3 curriculum
    amplifies it further.  Each example's domain is its positive gold-unit
    family prefix (``fssai:s16(1)`` → ``fssai``), matching ``domain_of`` in
    ``ce_v2_eval``.  Underrepresented domains are duplicated (with
    replacement) up to the largest domain's count, so the loss is no longer
    fssai-dominated.  Returns a new list; the split-by-question invariant is
    unaffected (duplicates stay within their question's split).
    """
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for ex in examples:
        gu = str(ex.get("gold_unit") or "")
        dom = gu.split(":", 1)[0] if ":" in gu else (gu or "?")
        by_domain[dom].append(ex)

    target = max(len(v) for v in by_domain.values())
    balanced: list[dict] = []
    for dom, group in by_domain.items():
        balanced.extend(group)
        missing = target - len(group)
        if missing > 0:
            for _ in range(missing):
                balanced.append(rng.choice(group))
    return balanced


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
    parser.add_argument(
        "--section-prefix",
        action="store_true",
        help="Bake \u00a7<identity> <text> into positive/negative text (CV2 P1; "
        "keeps train/serve parity via app.rag.retrieval.section_prefix).",
    )
    parser.add_argument(
        "--domain-balanced",
        action="store_true",
        help="Oversample underrepresented domains toward the largest domain's "
        "count (CV2 P4; fssai is 57% of the dataset).",
    )
    args = parser.parse_args()

    examples = build_pairwise_examples(
        mode=args.mode,
        max_positives=args.max_positives,
        max_negatives_per_tier=args.max_negatives_per_tier,
        section_prefix=args.section_prefix,
        domain_balanced=args.domain_balanced,
    )

    if not examples:
        return 1

    # Split
    _splits, split_info = split_dataset(examples)

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
        # P1: identity-coverage stats (authoritative payload-join)
        "section_prefix": args.section_prefix,
        "domain_balanced": args.domain_balanced,
        "positive_section_coverage": round(
            sum(1 for e in examples if e.get("positive_section")) / max(len(examples), 1), 4
        ),
        "positive_clause_coverage": round(
            sum(1 for e in examples if e.get("positive_clause")) / max(len(examples), 1), 4
        ),
        "prefix_coverage": round(
            sum(
                1 for e in examples
                if (e.get("positive_section") or e.get("positive_clause"))
            ) / max(len(examples), 1), 4
        ) if args.section_prefix else None,
        "domain_distribution": dict(
            sorted(
                Counter(
                    str(ex.get("gold_unit") or "").split(":", 1)[0] if ":" in str(ex.get("gold_unit") or "") else "?"
                    for ex in examples
                ).items()
            )
        ) if args.domain_balanced else None,
    }
    STATS_FILE.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

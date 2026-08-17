"""FETCH CE V2 GATE ASSETS - download models + data for a real gate run.

Mirrors what the CI ``real-gate`` job does: download the two cross-encoder
checkpoints and the four gate data files from the HF Hub into the gitignored
layout the harness expects:

    models:  evaluation/out/models/legal_ce_v1, legal_ce_v2_K500
    data:    evaluation/out/cache/pairwise_training_v2.jsonl
             evaluation/out/cache/pairwise_train_split.json
             evaluation/out/cache/payload_index.jsonl
             evaluation/out/ceiling_v5/hard_negative_mining.jsonl

After fetching, run the gate:

    python -m evaluation.ce_v2_gate --force --label ci-real

Usage:

    python -m evaluation.fetch_ce_v2_gate_assets --dry-run
    python -m evaluation.fetch_ce_v2_gate_assets
    python -m evaluation.fetch_ce_v2_gate_assets --model-v2-repo sumanksaha/Foodmultidomain
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / "evaluation" / "out"
CACHE_DIR = OUT_DIR / "cache"
MODELS_DIR = OUT_DIR / "models"
CEILING_V5 = OUT_DIR / "ceiling_v5"

#: (source path in repo, destination path in the harness layout)
DATA_FILES: list[tuple[str, Path]] = [
    ("pairwise_training_v2.jsonl", CACHE_DIR / "pairwise_training_v2.jsonl"),
    ("pairwise_train_split.json", CACHE_DIR / "pairwise_train_split.json"),
    ("payload_index.jsonl", CACHE_DIR / "payload_index.jsonl"),
    ("hard_negative_mining.jsonl", CEILING_V5 / "hard_negative_mining.jsonl"),
]

#: (hub repo id, destination dir name)
MODEL_REPOS: list[tuple[str, str, str]] = [
    ("sumanksaha/legal-ce-v1", "legal_ce_v1", "model_v1_repo"),
    ("sumanksaha/Foodmultidomain", "legal_ce_v2_K500", "model_v2_repo"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Download CE-v2 gate assets from the HF Hub")
    parser.add_argument("--model-v1-repo", default="sumanksaha/legal-ce-v1",
                        help="Control checkpoint repo (default: sumanksaha/legal-ce-v1)")
    parser.add_argument("--model-v2-repo", default="sumanksaha/Foodmultidomain",
                        help="Candidate checkpoint repo (default: sumanksaha/Foodmultidomain)")
    parser.add_argument("--assets-repo", default="sumanksaha/ce-v2-gate-assets",
                        help="Gate data repo (default: sumanksaha/ce-v2-gate-assets)")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and download nothing")
    args = parser.parse_args()

    model_repos = [
        (args.model_v1_repo, "legal_ce_v1"),
        (args.model_v2_repo, "legal_ce_v2_K500"),
    ]

    print("plan:")
    for repo, dest in model_repos:
        print(f"  model  {repo:<40} -> evaluation/out/models/{dest}/")
    for src, dest in DATA_FILES:
        print(f"  data   {args.assets_repo}/{src:<32} -> {dest.relative_to(PROJECT_ROOT)}")
    if args.dry_run:
        print("\ndry-run: nothing downloaded")
        return 0

    from huggingface_hub import snapshot_download

    token = os.environ.get("HF_TOKEN")
    for repo, dest_name in model_repos:
        dest = MODELS_DIR / dest_name
        dest.mkdir(parents=True, exist_ok=True)
        print(f"downloading {repo} -> {dest} ...")
        # snapshot into the destination so the harness finds config.json at root
        snapshot_download(repo_id=repo, local_dir=str(dest), token=token)

    snap = snapshot_download(repo_id=args.assets_repo, token=token)
    snap_dir = Path(snap)
    for src, dest in DATA_FILES:
        src_path = snap_dir / src
        if not src_path.exists():
            print(f"warning: {src} not found in {args.assets_repo} - skipping")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"copying {src} -> {dest.relative_to(PROJECT_ROOT)}")
        shutil.copy2(src_path, dest)

    print("\nfetch complete - run: python -m evaluation.ce_v2_gate --force --label ci-real")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

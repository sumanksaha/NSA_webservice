"""PUBLISH CE V2 GATE ASSETS - upload the regression-gate data to the HF Hub.

The CE-v2 gate harness reads four data files that live in the gitignored
``evaluation/out/`` tree.  Publishing them to a Hugging Face dataset repo lets
the CI ``real-gate`` job (``.github/workflows/ce-v2-regression.yml``) download
them and run the gate on GitHub runners - no local machine needed.

Published layout (``<org>/ce-v2-gate-assets``):

    README.md                     manifest (sha256 hashes + regeneration notes)
    pairwise_training_v2.jsonl    14,629 pairwise records (~17 MB)
    pairwise_train_split.json     question-id train/val/test split
    hard_negative_mining.jsonl    150-question K=500 mining output (~2.4 MB)
    payload_index.jsonl           27,345 chunk payloads (~36 MB)

Usage:

    python -m evaluation.publish_ce_v2_assets --dry-run
    python -m evaluation.publish_ce_v2_assets                 # HF_TOKEN env
    python -m evaluation.publish_ce_v2_assets --repo sumanksaha/ce-v2-gate-assets
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / "evaluation" / "out"
CACHE_DIR = OUT_DIR / "cache"

#: Local source files -> published filenames (must match the harness paths).
ASSETS: dict[str, str] = {
    str(CACHE_DIR / "pairwise_training_v2.jsonl"): "pairwise_training_v2.jsonl",
    str(CACHE_DIR / "pairwise_train_split.json"): "pairwise_train_split.json",
    str(OUT_DIR / "ceiling_v5" / "hard_negative_mining.jsonl"): "hard_negative_mining.jsonl",
    str(CACHE_DIR / "payload_index.jsonl"): "payload_index.jsonl",
}

DEFAULT_REPO = "sumanksaha/ce-v2-gate-assets"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest() -> dict[str, Any]:
    entries = []
    for src, name in ASSETS.items():
        p = Path(src)
        entries.append({
            "filename": name,
            "sha256": _sha256(p),
            "size_bytes": p.stat().st_size,
            "source": str(p.relative_to(PROJECT_ROOT)),
            "regen": _regen_hint(name),
        })
    return {
        "purpose": "CE-v2 regression gate inputs (evaluation/ce_v2_gate.py)",
        "published_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "harness": "evaluation/ce_v2_eval.py + evaluation/ce_v2_error_analysis.py",
        "gate": "evaluation/ce_v2_gate.py",
        "baseline": "evaluation/ce_v2_baseline.json (separate, committed)",
        "files": entries,
    }


def _regen_hint(filename: str) -> str:
    hints = {
        "pairwise_training_v2.jsonl": "python -m evaluation.pairwise_dataset",
        "pairwise_train_split.json": "python -m evaluation.pairwise_dataset (split step)",
        "hard_negative_mining.jsonl": "python -m evaluation.hard_negative_miner --offline",
        "payload_index.jsonl": "python -m evaluation.report_ceiling (payload index cache)",
    }
    return hints.get(filename, "see evaluation/CV2_IMPROVEMENT_PLAN.md")


def _readme(manifest: dict[str, Any]) -> str:
    lines = [
        "---",
        "license: other",
        "---",
        "# CE-v2 regression gate assets",
        "",
        "Inputs for the CE-v2 regression gate (`evaluation/ce_v2_gate.py`): the",
        "test pairs, question split, hard-negative mining output and chunk payload",
        "index used to score a candidate cross-encoder against the frozen baseline",
        "(`evaluation/ce_v2_baseline.json`, committed in the repo).",
        "",
        "The checkpoints are published separately:",
        "",
        "- control  `sumanksaha/legal-ce-v1`",
        "- candidate `sumanksaha/Foodmultidomain` (legal_ce_v2_K500)",
        "",
        "| file | sha256 | bytes | source | regen |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for f in manifest["files"]:
        lines.append(
            f"| {f['filename']} | `{f['sha256'][:12]}...` | {f['size_bytes']:,} | "
            f"`{f['source']}` | `{f['regen']}` |"
        )
    lines += [
        "",
        "## Usage (CI)",
        "",
        "```bash",
        "huggingface_hub.snapshot_download('<repo>')  # then copy files into",
        "evaluation/out/cache/ + evaluation/out/ceiling_v5/ as the harness expects",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish CE-v2 gate data assets to the HF Hub")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"Target repo (default: {DEFAULT_REPO})")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and upload nothing")
    parser.add_argument("--token", default=None, help="HF token (defaults to HF_TOKEN env)")
    args = parser.parse_args()

    missing = [src for src in ASSETS if not Path(src).exists()]
    if missing:
        print("error: missing source files:", missing)
        return 2

    manifest = _manifest()
    print(f"target repo: {args.repo}")
    print(f"files ({sum(f['size_bytes'] for f in manifest['files']) / 1e6:.1f} MB total):")
    for f in manifest["files"]:
        print(f"  {f['filename']:<32} {f['size_bytes'] / 1e6:6.1f} MB  sha256={f['sha256'][:12]}...")

    if args.dry_run:
        print("\ndry-run: nothing uploaded")
        return 0

    token = args.token or os.environ.get("HF_TOKEN")
    if not token:
        print("error: HF_TOKEN not set (pass --token or export HF_TOKEN)")
        return 2

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(repo_id=args.repo, repo_type="dataset", exist_ok=True)

    for src, name in ASSETS.items():
        print(f"uploading {name} ...")
        api.upload_file(
            path_or_fileobj=str(Path(src)),
            path_in_repo=name,
            repo_id=args.repo,
            repo_type="dataset",
        )

    readme = _readme(manifest)
    api.upload_file(
        path_or_fileobj=readme.encode("utf-8"),
        path_in_repo="README.md",
        repo_id=args.repo,
        repo_type="dataset",
    )
    print(f"published {len(ASSETS)} files + README to {args.repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

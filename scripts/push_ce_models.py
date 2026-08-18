"""Push the fine-tuned legal cross-encoders to the Hugging Face Hub (M0).

Uploads ``evaluation/out/models/legal_ce_v1`` and ``legal_ce_v2_K500`` as
sentence-transformers CrossEncoder repos so they can be served by TEI
(text-embeddings-inference) — see ``docs/HF_HOSTING_LANGGRAPH_INTEGRATION_PLAN.md``
Part A (§3.1).

**Why ``CrossEncoder.push_to_hub`` and not hand-rolled files:**
the Hub CrossEncoder repo layout needs ``modules.json`` and
``config_sentence_transformers.json`` in a specific format.  The library
generates these itself from the loaded model — never hand-roll them.
Only ``config.json`` / ``model.safetensors`` / tokenizer files are read from
the local checkpoint dir, so training artifacts (``tokenized_cache.pt``,
``train_state.pt``) are never uploaded.

Usage::

    HF_TOKEN=hf_xxx python scripts/push_ce_models.py                  # push all
    HF_TOKEN=hf_xxx python scripts/push_ce_models.py --repo legal-ce-v2-k500
    python scripts/push_ce_models.py --dry-run                        # no upload
    HF_TOKEN=hf_xxx python scripts/push_ce_models.py --private --force
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make the repo root importable — ``python scripts/push_ce_models.py`` puts
# ``scripts/`` on sys.path, not the repo root, so ``app.rag.*`` fails otherwise.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Local checkpoint dir name → Hub repo suffix (under ``--org``).
MODEL_KEYS: dict[str, str] = {
    "legal_ce_v1": "legal-ce-v1",
    "legal_ce_v2_K500": "legal-ce-v2-k500",
}

#: Relative path from the repo root to the local checkpoints.
LOCAL_MODELS_DIR = Path("evaluation") / "out" / "models"

#: Sanity pairs scored before upload and re-checked after (parity check).
_SANITY_PAIRS = [
    ("penalty for selling substandard food", "Section 50: General penalty for unsafe food"),
    ("who appoints the Food Safety Officer", "Section 9: Officer of the Food Authority"),
    ("prohibition on sale of adulterated food", "Section 21: Prohibition of misleading claims"),
]


def _resolve_token(token: str | None) -> str | None:
    """Resolve the HF token from ``--token`` or ``HF_TOKEN`` env."""
    token = token or os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit(2)
    return token


def _load_ce(local_path: Path):
    """Load a local CrossEncoder with the torch thread cap applied."""
    try:
        from sentence_transformers import CrossEncoder

        from app.rag.torch_runtime import cap_torch_threads
    except ImportError:  # pragma: no cover - dev-env guard
        raise SystemExit(2) from None
    cap_torch_threads()
    return CrossEncoder(str(local_path))


def _score(ce, pairs: list[tuple[str, str]]) -> list[float]:
    """Score sanity pairs with a loaded encoder."""
    return [float(s) for s in ce.predict(pairs)]


def _parity_ok(local_scores: list[float], hub_scores: list[float], tol: float = 0.05) -> bool:
    """Hub reload must score the sanity pairs close to the local copy."""
    if len(local_scores) != len(hub_scores):
        return False
    return all(abs(a - b) <= tol for a, b in zip(local_scores, hub_scores, strict=False))


def _push_one(
    model_key: str,
    repo_id: str,
    token: str | None,
    *,
    private: bool,
    force: bool,
    skip_validate: bool,
    dry_run: bool,
) -> bool:
    local_path = LOCAL_MODELS_DIR / model_key
    if not local_path.is_dir():
        return False
    if not (local_path / "model.safetensors").is_file():
        return False

    (local_path / "model.safetensors").stat().st_size / 1e6

    if dry_run:
        return True

    ce = _load_ce(local_path)
    local_scores = _score(ce, _SANITY_PAIRS)

    ce.push_to_hub(
        repo_id,
        token=token,
        private=private,
        exist_ok=force,
        commit_message=f"push {model_key} (fine-tuned legal cross-encoder)",
    )

    if skip_validate:
        return True

    try:
        hub_ce = _load_ce(repo_id)  # type: ignore[arg-type] - hub id or local path
    except Exception:
        return False
    hub_scores = _score(hub_ce, _SANITY_PAIRS)
    ok = _parity_ok(local_scores, hub_scores)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Push fine-tuned legal cross-encoders to the HF Hub")
    parser.add_argument("--org", default="nsa-webservice", help="Hub org/namespace (default: nsa-webservice)")
    parser.add_argument("--repo", help="Hub repo suffix to push (default: all known pairs); requires --local")
    parser.add_argument("--local", choices=list(MODEL_KEYS), help="Local checkpoint to push when --repo is given")
    parser.add_argument("--token", help="HF token (defaults to HF_TOKEN env)")
    parser.add_argument("--private", action="store_true", help="Create private repos")
    parser.add_argument("--force", action="store_true", help="exist_ok - upload into an existing repo (e.g. one created in the web UI)")
    parser.add_argument("--skip-validate", action="store_true", help="Skip Hub-reload parity check")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be pushed, upload nothing")
    args = parser.parse_args()

    if args.repo and args.local is None:
        parser.error("--repo requires --local (which local checkpoint to push)")
    if args.local and args.repo is None:
        parser.error("--local requires --repo (the Hub repo suffix)")


    token = None if args.dry_run else _resolve_token(args.token)

    selected = {args.local: args.repo} if args.repo else dict(MODEL_KEYS)
    results: dict[str, bool] = {}
    for model_key, suffix in selected.items():
        repo_id = f"{args.org}/{suffix}"
        results[model_key] = _push_one(
            model_key,
            repo_id,
            token,
            private=args.private,
            force=args.force,
            skip_validate=args.skip_validate,
            dry_run=args.dry_run,
        )

    ok_count = sum(results.values())
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

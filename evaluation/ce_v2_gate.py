"""CE V2 GATE - automated regression gate for cross-encoder retrains (2026-08-17).

Runs the Step-0 harness (``evaluation.ce_v2_eval`` + ``evaluation.ce_v2_error_analysis``)
against the candidate model and compares the results with the frozen baseline
(``evaluation/ce_v2_baseline.json``).  Wired into:

* a local pre-commit hook (``.pre-commit-config.yaml``) that skips fast when
  nothing was retrained since the baseline freeze, and
* a CI job (``.github/workflows/ce-v2-regression.yml``) that exercises the gate
  logic in torch-free fixture mode (the models/training data live in the
  gitignored ``evaluation/out/`` tree, so the real gate runs on the machine
  that owns the checkpoints).

Gates enforced (each row = name / reference / direction):

  HARD - no regression vs baseline v2 (fail always):
    R@1, R@5, R@10, R@20, MRR@10, nDCG@10, pairwise accuracy
    per-domain pairwise accuracy for epa / contract (the domains v2 gained)
  TARGET - plan P1/P2 improvement goals (fail only with --strict-targets):
    hierarchy_version failures <= 4, same_section_hard_neg <= 1,
    total failures <= 12

Exit codes:
  0  pass (or skipped: nothing retrained / models unavailable + --skip-if-unavailable)
  1  gate breached
  2  configuration error (baseline/data missing without --skip-if-unavailable)

Usage:
    python -m evaluation.ce_v2_gate                          # gate v2 vs baseline
    python -m evaluation.ce_v2_gate --model-v2 evaluation/out/models/legal_ce_v2_K500_p1
    python -m evaluation.ce_v2_gate --strict-targets         # also enforce P1/P2 targets
    python -m evaluation.ce_v2_gate --skip-if-unavailable    # pre-commit: skip on fresh checkouts
    python -m evaluation.ce_v2_gate --eval-json X.json --error-json Y.json --baseline Z.json
                                                             # pure comparison (CI / re-check)
    python -m evaluation.ce_v2_gate --force                  # ignore the staleness check

After an accepted retrain, re-freeze the baseline:
    python -m evaluation.ce_v2_eval --freeze-baseline
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.config import CACHE_DIR

from evaluation.ce_v2_eval import (
    EVAL_OUT,
    MODELS,
    PAIRS_FILE,
    SPLIT_FILE,
    _model_path,
)

ERR_OUT = CACHE_DIR / "ce_v2_error_analysis.json"
BASELINE_FILE = PROJECT_ROOT / "evaluation" / "ce_v2_baseline.json"
REPORT_OUT = CACHE_DIR / "ce_v2_gate_report.json"

#: Hard gates: (label, dotted-path into eval JSON, kind) - current >= baseline.
HARD_METRICS = [
    ("R@1", "ranking.v2.r_at.1"),
    ("R@5", "ranking.v2.r_at.5"),
    ("R@10", "ranking.v2.r_at.10"),
    ("R@20", "ranking.v2.r_at.20"),
    ("MRR@10", "ranking.v2.mrr"),
    ("nDCG@10", "ranking.v2.ndcg"),
    ("pairwise accuracy", "pairwise.v2.acc"),
]

#: Per-domain pairwise accuracy that must not regress vs baseline v2.
HARD_DOMAINS = ["epa", "contract"]

#: Target gates: (label, dotted-path, limit, direction) - fail only with --strict-targets.
#: Paths are resolved on the error-analysis section (ce_v2_error_analysis.json),
#: whose top-level keys are `categories` / `failures_total` / `per_query`.
TARGET_METRICS = [
    ("hierarchy_version failures", "categories.hierarchy_version", 4, "<="),
    ("same_section_hard_neg failures", "categories.same_section_hard_neg", 1, "<="),
    ("total failures", "failures_total", 12, "<="),
]


# --------------------------------------------------------------------------- #
# Pure comparison (torch-free - the CI-testable surface)
# --------------------------------------------------------------------------- #
def _dig(doc: dict, dotted: str) -> Any:
    """Resolve 'a.b.c' on a nested dict; return None for missing paths."""
    cur: Any = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def compare(
    eval_data: dict[str, Any],
    err_data: dict[str, Any],
    baseline: dict[str, Any],
    strict_targets: bool = False,
) -> tuple[bool, list[dict[str, Any]]]:
    """Compare a harness run against the baseline.

    Returns ``(passed, checks)`` where each check is
    ``{name, current, reference, ok, kind, direction}``.
    """
    b_eval = baseline.get("eval", {})
    checks: list[dict[str, Any]] = []

    for label, path in HARD_METRICS:
        cur = _dig(eval_data, path)
        ref = _dig(b_eval, path)
        ok = cur is not None and ref is not None and float(cur) >= float(ref)
        checks.append({"name": label, "current": cur, "reference": ref,
                       "ok": bool(ok), "kind": "hard", "direction": ">="})

    for dom in HARD_DOMAINS:
        cur = next((d.get("v2") for d in eval_data.get("per_domain", []) if d.get("domain") == dom), None)
        ref = next((d.get("v2") for d in b_eval.get("per_domain", []) if d.get("domain") == dom), None)
        ok = cur is not None and ref is not None and float(cur) >= float(ref)
        checks.append({"name": f"{dom} pairwise accuracy", "current": cur, "reference": ref,
                       "ok": bool(ok), "kind": "hard", "direction": ">="})

    for label, path, limit, direction in TARGET_METRICS:
        cur = _dig(err_data, path)
        ok = cur is not None and (float(cur) <= float(limit) if direction == "<=" else float(cur) >= float(limit))
        checks.append({"name": label, "current": cur, "reference": limit,
                       "ok": bool(ok), "kind": "target", "direction": direction})

    effective = [c for c in checks if c["kind"] == "hard" or strict_targets]
    passed = all(c["ok"] for c in effective)
    return passed, checks


# --------------------------------------------------------------------------- #
# Pre-flight: availability + staleness
# --------------------------------------------------------------------------- #
def _model_dirs() -> list[Path]:
    """Resolve the v1/v2 model directories (honoring --model-v1/--model-v2)."""
    return [_model_path(key) for key in MODELS]


def _models_available() -> bool:
    return all((d / "config.json").exists() for d in _model_dirs())


def _data_available() -> bool:
    return PAIRS_FILE.exists() and SPLIT_FILE.exists()


def _dir_newest(d: Path) -> float:
    """Newest file mtime inside a directory (0.0 when missing/empty)."""
    try:
        return max(p.stat().st_mtime for p in d.iterdir())
    except (OSError, ValueError):
        return 0.0


def _retrained_since(baseline: dict[str, Any]) -> bool:
    """True when a model checkpoint or the training data is newer than the freeze."""
    frozen = baseline.get("frozen_at")
    if not frozen:
        return True
    try:
        frozen_ts = datetime.fromisoformat(str(frozen)).timestamp()
    except ValueError:
        return True
    newest = 0.0
    for path in (PAIRS_FILE, SPLIT_FILE, *_model_dirs()):
        newest = max(newest, path.stat().st_mtime if path.exists() else 0.0, _dir_newest(path))
    return newest > frozen_ts


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _fmt(x: Any) -> str:
    return "-" if x is None else f"{float(x):.4f}"


def render_report(passed: bool, checks: list[dict[str, Any]], label: str, strict: bool) -> str:
    lines = [f"=== CE-v2 Regression Gate {label} ===\n"]
    lines.append(f"{'Check':<32} {'Current':>10} {'Reference':>10} {'Status':>8}")
    lines.append("-" * 64)
    for c in checks:
        kind = "HARD" if c["kind"] == "hard" else "TGT"
        status = "PASS" if c["ok"] else "FAIL"
        lines.append(
            f"{c['name']:<32} {_fmt(c['current']):>10} {_fmt(c['reference']):>10} "
            f"{status + ' (' + kind + ')':>16}"
        )
    lines.append("-" * 64)
    if strict:
        lines.append("Target gates enforced (--strict-targets).")
    else:
        lines.append("Target gates reported only - pass --strict-targets to enforce (P1/P2 goals).")
    lines.append("GATE " + ("PASS" if passed else "FAIL"))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="CE v2 regression gate")
    parser.add_argument("--baseline", type=Path, default=BASELINE_FILE,
                        help="Frozen baseline JSON (default: evaluation/ce_v2_baseline.json)")
    parser.add_argument("--eval-json", type=Path, default=None,
                        help="Skip the harness run and compare these eval JSONs instead (CI fixture mode)")
    parser.add_argument("--error-json", type=Path, default=None,
                        help="Skip the harness run and compare these error-analysis JSONs instead")
    parser.add_argument("--model-v1", type=Path, default=None,
                        help="Override the v1 (frozen control) model directory")
    parser.add_argument("--model-v2", type=Path, default=None,
                        help="Override the v2 (candidate) model directory")
    parser.add_argument("--label", default="run", help="Label for the report")
    parser.add_argument("--strict-targets", action="store_true",
                        help="Fail on the P1/P2 improvement targets (hierarchy <= 4, same-section <= 1)")
    parser.add_argument("--skip-if-unavailable", action="store_true",
                        help="Exit 0 (skip) when models or training data are absent (pre-commit on fresh checkouts)")
    parser.add_argument("--force", action="store_true",
                        help="Run even when nothing was retrained since the baseline freeze")
    args = parser.parse_args()

    from evaluation.ce_v2_eval import _MODEL_OVERRIDES

    for key, arg in (("v1", args.model_v1), ("v2", args.model_v2)):
        if arg is not None:
            _MODEL_OVERRIDES[key] = arg

    if not args.baseline.exists():
        print(f"error: baseline not found: {args.baseline} (run: python -m evaluation.ce_v2_eval --freeze-baseline)")
        return 2

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))

    # -------- fixture mode (pure comparison, no models) --------
    if args.eval_json or args.error_json:
        if not (args.eval_json and args.error_json):
            print("error: --eval-json and --error-json must be provided together")
            return 2
        eval_data = json.loads(args.eval_json.read_text(encoding="utf-8"))
        err_data = json.loads(args.error_json.read_text(encoding="utf-8"))
        return _report_and_exit(eval_data, err_data, baseline, args)

    # -------- full mode: run the harness, then compare --------
    if not (_models_available() and _data_available()):
        if args.skip_if_unavailable:
            print("CE-v2 gate skipped: models or training data absent (fresh checkout?) - run it on the training machine.")
            return 0
        print("error: models or training data missing (evaluation/out tree not present)")
        return 2

    if not args.force and not _retrained_since(baseline):
        print(f"CE-v2 gate skipped: nothing retrained since baseline freeze ({baseline.get('frozen_at')}). "
              "Use --force to re-check anyway.")
        return 0

    cmd = [sys.executable, "-m", "evaluation.ce_v2_eval"]
    for key, arg in (("v1", args.model_v1), ("v2", args.model_v2)):
        if arg is not None:
            cmd += [f"--model-{key}", str(arg)]
    subprocess.run(cmd, check=True, cwd=PROJECT_ROOT)  # noqa: S603 - argv[0] is sys.executable; args are module names + paths the caller supplied on its own CLI

    cmd_ea = [sys.executable, "-m", "evaluation.ce_v2_error_analysis"]
    for key, arg in (("v1", args.model_v1), ("v2", args.model_v2)):
        if arg is not None:
            cmd_ea += [f"--model-{key}", str(arg)]
    subprocess.run(cmd_ea, check=True, cwd=PROJECT_ROOT)  # noqa: S603 - same trust level as the caller's own command line

    eval_data = json.loads(EVAL_OUT.read_text(encoding="utf-8"))
    err_data = json.loads(ERR_OUT.read_text(encoding="utf-8"))
    return _report_and_exit(eval_data, err_data, baseline, args)


def _report_and_exit(
    eval_data: dict[str, Any],
    err_data: dict[str, Any],
    baseline: dict[str, Any],
    args: argparse.Namespace,
) -> int:
    passed, checks = compare(eval_data, err_data, baseline, strict_targets=args.strict_targets)
    report = render_report(passed, checks, args.label, args.strict_targets)
    print("\n" + report)

    payload = {
        "label": args.label,
        "passed": passed,
        "strict_targets": args.strict_targets,
        "baseline": str(args.baseline),
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "checks": checks,
    }
    REPORT_OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nReport: {REPORT_OUT}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Experiment E2 vs B -- the paired comparison (0 LLM calls).

Spec: docs/specs/experiment_e2_query_type_directives_spec.md sections 4-6.

Reads two cached checkpoints and compares them question-by-question:

    treatment  evaluation/out/ceiling_v5_e2/experiment_E2_llm_checkpoint.jsonl
    control    evaluation/out/ceiling_v5/experiment_B_llm_checkpoint.jsonl

Primary metric is **grader-weighted** = ``grader_score / 2`` (0.0 / 0.5 / 1.0)
from ``evaluation.grading.grade_answer``'s 2/1/0 rubric, reported as the effect
size (mean delta per K).

The **significance test** is a McNemar *exact* test on the paired binary
``grader_score >= 1``.  That split is deliberate and is not a convenience:

  * grader-weighted is 3-level ordinal, and McNemar is not defined for it;
  * the pre-registered MDE (``docs/RAG_EVALUATION_ENLARGEMENT.md``: 5.11pp at
    n=150, f=5%) is a *paired-binary* quantity, so only the binary contrast is
    comparable to it.

Effect size and test are always reported together; neither is reported alone.

Secondary (descriptive, never gated): token-overlap -- ``answer_coverage``,
``answer_jaccard``, ``answer_correctness`` -- via Wilcoxon signed-rank with a
bootstrap CI.

Holm-Bonferroni across the 4 K values within the primary family.

Usage:
    python -m evaluation.experiment_e2_vs_b
    python -m evaluation.experiment_e2_vs_b --treat <path> --control <path>
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scipy import stats  # noqa: E402

K_VALUES = [10, 20, 30, 50]
PRIMARY_ALPHA = 0.05

DEFAULT_TREAT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5_e2" / "experiment_E2_llm_checkpoint.jsonl"
DEFAULT_CONTROL = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "experiment_B_llm_checkpoint.jsonl"
OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5_e2"
OUT_JSON = OUT_DIR / "experiment_E2_vs_B.json"
OUT_MD = OUT_DIR / "experiment_E2_vs_B.md"

TOKEN_METRICS = ("answer_coverage", "answer_jaccard", "answer_correctness")


# ---------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------
def mde(n: int, f: float = 0.05) -> float:
    """Smallest detectable paired-binary effect at ~80% power, alpha=0.05.

    delta = (z_.975 + z_.80) * sqrt(f / n) = 2.8016 * sqrt(f / n)

    Validated: n=150, f=5% -> 5.11pp, matching
    ``docs/RAG_EVALUATION_ENLARGEMENT.md``.
    """
    if n <= 0:
        return float("inf")
    return 2.8016 * math.sqrt(f / n) * 100.0


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_checkpoint(path: Path) -> tuple[dict[tuple[str, int], dict], int]:
    """(question_id, k) -> record, keeping only error-free records.

    Error records are dropped rather than scored: a rate-limited or empty-answer
    call has no answer to grade, so counting it as a zero would bias the arm
    downward.  The dropped count is reported alongside every result.

    Returns ``({}, 0)`` when the file is absent so the caller can report a
    not-yet-run arm instead of raising.
    """
    if not path.exists():
        return {}, 0
    out: dict[tuple[str, int], dict] = {}
    n_err = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("error"):
                n_err += 1
                continue
            k = rec.get("k")
            if k is None:
                continue
            out[(rec["question_id"], int(k))] = rec
    return out, n_err


def holm(pvals: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni step-down adjusted p-values."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    adj: dict[str, float] = {}
    running = 0.0
    for i, (key, p) in enumerate(items):
        val = min(1.0, (m - i) * p)
        running = max(running, val)
        adj[key] = running
    return adj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def mcnemar_exact(treat_vals: list[int], ctrl_vals: list[int]) -> dict:
    """Exact (binomial) McNemar on paired binary outcomes.

    b = treat correct & control wrong; c = control correct & treat wrong.
    Under H0, b ~ Binomial(b + c, 0.5).
    """
    b = sum(1 for t, c in zip(treat_vals, ctrl_vals) if t == 1 and c == 0)
    c = sum(1 for t, c in zip(treat_vals, ctrl_vals) if t == 0 and c == 1)
    n_disc = b + c
    if n_disc == 0:
        return {"b": b, "c": c, "n_discordant": 0, "p": 1.0,
                "delta_bin": 0.0, "note": "no discordant pairs"}
    p = float(stats.binomtest(b, n_disc, 0.5, alternative="two-sided").pvalue)
    return {"b": b, "c": c, "n_discordant": n_disc, "p": p,
            "delta_bin": (b - c) / len(treat_vals)}


def bootstrap_ci(diffs: np.ndarray, n_boot: int = 10_000, alpha: float = 0.05):
    """Percentile bootstrap CI for the mean paired difference."""
    if len(diffs) == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(20261018)
    idx = rng.integers(0, len(diffs), size=(n_boot, len(diffs)))
    means = diffs[idx].mean(axis=1)
    lo, hi = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return (float(lo), float(hi))


# ---------------------------------------------------------------------------
# Core comparison
# ---------------------------------------------------------------------------
def compare(treat: dict, ctrl: dict, k_values=K_VALUES) -> dict:
    per_k: dict[str, dict] = {}
    raw_p: dict[str, float] = {}

    for k in k_values:
        keys = [key for key in treat if key[1] == k and key in ctrl]
        keys.sort()
        if not keys:
            per_k[str(k)] = {"n_pairs": 0, "note": "no paired records"}
            continue

        t_gw = np.array([treat[key].get("grader_score", 0) / 2 for key in keys])
        c_gw = np.array([ctrl[key].get("grader_score", 0) / 2 for key in keys])
        gw_diff = t_gw - c_gw

        t_bin = [1 if treat[key].get("grader_score", 0) >= 1 else 0 for key in keys]
        c_bin = [1 if ctrl[key].get("grader_score", 0) >= 1 else 0 for key in keys]
        mc = mcnemar_exact(t_bin, c_bin)
        raw_p[str(k)] = mc["p"]

        lo, hi = bootstrap_ci(gw_diff)
        n = len(keys)
        entry = {
            "n_pairs": n,
            "n_treated_questions": sum(
                1 for key in keys
                if (treat[key].get("query_type") or "general_qa") != "general_qa"
            ),
            "grader_weighted": {
                "treat_mean": float(t_gw.mean()),
                "control_mean": float(c_gw.mean()),
                "delta_pp": float(gw_diff.mean() * 100),
                "ci95_pp": [lo * 100, hi * 100],
                "mde_pp": mde(n),
            },
            "binary": {
                "treat_rate": float(np.mean(t_bin)),
                "control_rate": float(np.mean(c_bin)),
                "delta_pp": mc["delta_bin"] * 100,
                "b_treat_only": mc["b"],
                "c_control_only": mc["c"],
                "n_discordant": mc["n_discordant"],
            },
            "mcnemar_p": mc["p"],
            "token_overlap": {},
        }
        if mc.get("note"):
            entry["mcnemar_note"] = mc["note"]

        for metric in TOKEN_METRICS:
            t_v = np.array([float(treat[key].get(metric) or 0.0) for key in keys])
            c_v = np.array([float(ctrl[key].get(metric) or 0.0) for key in keys])
            d = t_v - c_v
            nz = d[d != 0]
            if len(nz) == 0:
                entry["token_overlap"][metric] = {
                    "median_delta": 0.0, "wilcoxon_p": 1.0, "note": "no non-zero differences"
                }
                continue
            lo, hi = bootstrap_ci(d)
            try:
                w_p = float(stats.wilcoxon(t_v, c_v).pvalue) if len(nz) else 1.0
            except ValueError:
                w_p = 1.0
            entry["token_overlap"][metric] = {
                "treat_mean": float(t_v.mean()),
                "control_mean": float(c_v.mean()),
                "median_delta": float(np.median(d)),
                "mean_delta": float(d.mean()),
                "ci95": [lo, hi],
                "wilcoxon_p": w_p,
            }
        per_k[str(k)] = entry

    adj = holm(raw_p) if raw_p else {}
    for k, e in per_k.items():
        if k in adj:
            e["mcnemar_p_holm"] = adj[k]
            e["significant_holm"] = bool(adj[k] < PRIMARY_ALPHA)

    return {
        "primary_metric": "grader_weighted (grader_score/2)",
        "significance_test": "McNemar exact on paired binary grader_score>=1",
        "multiplicity": "Holm-Bonferroni across K",
        "alpha": PRIMARY_ALPHA,
        "per_k": per_k,
        "note": (
            "Null results are reported as inconclusive, not as evidence of no "
            "effect: temp=0.1 means even control-rendered questions flip between "
            "runs (score_significance.py documents 8/89 flips between replicates "
            "of the unchanged system, f~9% -> noise-floor MDE ~6.9pp at n=150)."
        ),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def render_markdown(result: dict, treat_path: Path, ctrl_path: Path,
                    treat_dropped: int, ctrl_dropped: int) -> str:
    lines = [
        "# Experiment E2 vs Experiment B -- paired comparison",
        "",
        f"- treatment: `{treat_path.relative_to(PROJECT_ROOT)}`",
        f"  (error records dropped: {treat_dropped})",
        f"- control: `{ctrl_path.relative_to(PROJECT_ROOT)}`",
        f"  (error records dropped: {ctrl_dropped})",
        "",
        f"**Primary metric:** {result['primary_metric']}  ",
        f"**Test:** {result['significance_test']}  ",
        f"**Multiplicity:** {result['multiplicity']} (alpha={result['alpha']})",
        "",
        "## Primary -- grader-weighted",
        "",
        "| K | n pairs | treated | E2 mean | B mean | delta (pp) | 95% CI (pp) | MDE (pp) | McNemar p | Holm p | sig |",
        "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|:--:|",
    ]
    for k, e in result["per_k"].items():
        gw = e.get("grader_weighted")
        if not gw:
            lines.append(f"| {k} | {e.get('n_pairs',0)} | - | - | - | - | - | - | - | - | n/a |")
            continue
        ci = gw["ci95_pp"]
        sig = "yes" if e.get("significant_holm") else "no"
        lines.append(
            f"| {k} | {e['n_pairs']} | {e['n_treated_questions']} | "
            f"{gw['treat_mean']:.4f} | {gw['control_mean']:.4f} | "
            f"{gw['delta_pp']:+.2f} | [{ci[0]:+.2f}, {ci[1]:+.2f}] | "
            f"{gw['mde_pp']:.2f} | {e['mcnemar_p']:.4f} | "
            f"{e.get('mcnemar_p_holm', float('nan')):.4f} | {sig} |"
        )

    lines += [
        "",
        "**Reading the gate.** A K is positive only if the 95% CI excludes 0 AND",
        "the effect exceeds the MDE for that n. Holm-adjusted p is reported but",
        "the MDE comparison is the pre-registered criterion.",
        "",
        "## Secondary -- token-overlap (descriptive, not gated)",
        "",
    ]
    for metric in TOKEN_METRICS:
        lines += [
            f"### `{metric}`",
            "",
            "| K | E2 mean | B mean | median delta | mean delta | Wilcoxon p |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for k, e in result["per_k"].items():
            m = (e.get("token_overlap") or {}).get(metric)
            if not m or "treat_mean" not in m:
                lines.append(f"| {k} | - | - | - | - | - |")
                continue
            lines.append(
                f"| {k} | {m['treat_mean']:.4f} | {m['control_mean']:.4f} | "
                f"{m['median_delta']:+.4f} | {m['mean_delta']:+.4f} | "
                f"{m['wilcoxon_p']:.4f} |"
            )
        lines.append("")

    lines += ["## Caveats", "", f"- {result['note']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--treat", default=str(DEFAULT_TREAT))
    ap.add_argument("--control", default=str(DEFAULT_CONTROL))
    args = ap.parse_args(argv)

    treat_path, ctrl_path = Path(args.treat), Path(args.control)
    if not treat_path.exists():
        print(f"[E2 vs B] treatment arm not found: {treat_path}")
        print("           Run the experiment first:")
        print("             python -m evaluation.experiment_e2_reasoning_eval --phase llm")
        print("           (requires RAG_QUERY_TYPE_DIRECTIVES=true, else it reproduces B)")
        return 1
    if not ctrl_path.exists():
        print(f"[E2 vs B] control arm not found: {ctrl_path}")
        print("           Experiment B's checkpoint is the control; check it exists.")
        return 1

    treat, treat_dropped = load_checkpoint(treat_path)
    ctrl, ctrl_dropped = load_checkpoint(ctrl_path)

    print("=" * 70)
    print("Experiment E2 vs B -- paired comparison (0 LLM)")
    print("=" * 70)
    print(f"  treatment: {len(treat)} clean records ({treat_dropped} error records dropped)")
    print(f"  control  : {len(ctrl)} clean records ({ctrl_dropped} error records dropped)")
    by_k_t = {k: sum(1 for key in treat if key[1] == k) for k in K_VALUES}
    by_k_c = {k: sum(1 for key in ctrl if key[1] == k) for k in K_VALUES}
    print(f"  treatment per K: {by_k_t}")
    print(f"  control   per K: {by_k_c}")

    paired = sum(
        1 for key in treat
        if key[1] in K_VALUES and key in ctrl
    )
    print(f"  paired (question, K) records: {paired}")
    if not paired:
        print("\n  No paired records -- the treatment arm has not been run yet.")
        print(f"  Run: python -m evaluation.experiment_e2_reasoning_eval --phase llm")
        return 1

    result = compare(treat, ctrl)
    result["provenance"] = {
        "treat": str(treat_path), "control": str(ctrl_path),
        "treat_clean": len(treat), "control_clean": len(ctrl),
        "treat_error_dropped": treat_dropped, "control_error_dropped": ctrl_dropped,
        "paired_records": paired,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    md = render_markdown(result, treat_path, ctrl_path, treat_dropped, ctrl_dropped)
    OUT_MD.write_text(md, encoding="utf-8")

    print("\n" + md)
    print(f"\n  wrote {OUT_JSON}")
    print(f"  wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
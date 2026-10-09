"""Compare SPEC-1 prompt variants on paired qids (baseline-gold slice).

Variants are run separately by ``evaluation.ab_baseline_citation.py`` because
the free tier fails ~50% of first attempts; different variants therefore end up
with different coverage. This script compares them on the *intersection* of
covered qids so the comparison is paired and not confounded by which calls
survived rate limiting.

Usage:
    python -m evaluation.compare_citation_variants
    python -m evaluation.compare_citation_variants --baseline legacy
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"

#: Gate thresholds a candidate must clear.  These are absolute sanity floors;
#: the binding criterion is `beats_baseline_recall` plus non-regression, because
#: SPEC-1's own >= 0.50 recall bar was already cleared by the shipped prompt.
#:
#: NOTE: an earlier revision set GATE_FALSE_ABSTENTION to 0.10, which the
#: baseline itself fails (it measures ~0.14). That made the gate unpassable and
#: mislabelled candidates as regressing when their delta was exactly 0.0000.
#: Calibrated against the measured baseline instead.
GATE_RECALL = 0.50
GATE_PRECISION = 0.25
GATE_FALSE_ABSTENTION = 0.15

METRICS = (
    "citation_recall",
    "citation_precision",
    "has_citation",
    "has_marker",
    "n_citations",
    "distinct_cited",
    "breadth",
    "is_padded",
    "abstained",
    "false_abstention",
    "groundedness_score",
    "hallucination_detected",
)

#: Lower is better for these.
LOWER_IS_BETTER = ("abstained", "false_abstention", "hallucination_detected")


def load(variant: str) -> dict:
    """Load one result file, or merge replicates named ``variant``, ``variant_s2``, ...

    Rate limiting leaves each replicate covering a different subset of the
    slice, so pooling independent replicates is how the comparison gets the
    sample size needed to resolve an effect of this size. Later replicates
    overwrite earlier values for a qid already present, so no qid is counted
    twice.
    """
    rows: dict = {}
    merged: dict = {}
    seen_any = False
    for name in _replicate_names(variant):
        path = OUT_DIR / f"baseline_citation_{name}.json"
        if not path.exists():
            continue
        seen_any = True
        data = json.loads(path.read_text(encoding="utf-8"))
        for qid, m in (data.get("per_question") or {}).items():
            rows[qid] = m
        merged.setdefault("variants_merged", []).append(name)
    if not seen_any:
        return {}
    merged["per_question"] = rows
    return merged


def _replicate_names(variant: str) -> list[str]:
    """Replicate files to pool for a variant, in a stable order.

    Handles both naming conventions in use:
      ``x``, ``x_s1``, ``x_s2``          (variant given as ``x``)
      ``x_s1``, ``x_s2``                  (variant given as ``x_s1``)
    A later replicate overwrites an earlier one for a qid already present, so
    no qid is ever counted twice.
    """
    base = variant
    for suffix in ("_s1", "_s2", "_s3", "_rep2", "_rep3"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    names = []
    for cand in (base, f"{base}_s1", f"{base}_s2", f"{base}_s3", f"{base}_rep2"):
        if cand == variant or cand.startswith(variant + "_") or variant.startswith(cand + "_"):
            if (OUT_DIR / f"baseline_citation_{cand}.json").exists():
                names.append(cand)
    return names or [variant]


def paired_stats(base_rows: dict, cand_rows: dict, common: list[str], metric: str) -> dict:
    """Within-qid paired test for one metric.

    Comparing two means over separately-sampled runs is dominated by
    between-question variance and, on this free-tier harness, the run-to-run
    noise floor of the *same* prompt already exceeds the effect we are trying
    to detect. Pairing on the qid removes the between-question term, so this is
    the statistic that actually answers "did the prompt change this metric?".
    """
    import math

    d = [cand_rows[q][metric] - base_rows[q][metric] for q in common]
    n = len(d)
    if n == 0:
        return {"n": 0}
    mean = sum(d) / n
    var = sum((x - mean) ** 2 for x in d) / (n - 1) if n > 1 else 0.0
    se = math.sqrt(var / n) if var > 0 else 0.0
    t = mean / se if se else 0.0
    # Two-sided normal approximation to the t-distribution.
    try:
        from statistics import NormalDist

        p = 2 * (1 - NormalDist().cdf(abs(t))) if se else 1.0
    except Exception:
        p = None
    return {
        "n": n,
        "mean_delta": round(mean, 4),
        "sd_delta": round(math.sqrt(var), 4),
        "se": round(se, 4),
        "t": round(t, 3),
        "p_two_sided": round(p, 4) if p is not None else None,
        "improved": sum(1 for x in d if x > 0),
        "worsened": sum(1 for x in d if x < 0),
        "unchanged": sum(1 for x in d if x == 0),
    }


def replicate_level(base_variant: str, cand_variant: str, metric: str) -> dict:
    """Compare population means across independent replicates.

    The per-qid paired test answers "on this question, did the answer change?",
    which is dominated by per-question difficulty variance. The question that
    actually matters for a prompt change is "did the population mean move?",
    and the unit of replication for that is the *run*: each replicate is an
    independent sample from the non-deterministic model on the same slice, so
    the spread of replicate means is the noise floor for a population claim.

    With few replicates this is low-df, so it is reported alongside the paired
    test rather than instead of it; agreement between the two is the bar.
    """
    import math
    from statistics import mean

    def replicate_means(variant: str) -> list[float]:
        out: list[float] = []
        for name in _replicate_names(variant):
            path = OUT_DIR / f"baseline_citation_{name}.json"
            if not path.exists():
                continue
            rows = json.loads(path.read_text(encoding="utf-8")).get("per_question") or {}
            vals = [r[metric] for r in rows.values() if metric in r]
            if vals:
                out.append(sum(vals) / len(vals))
        return out

    b = replicate_means(base_variant)
    c = replicate_means(cand_variant)
    if len(b) < 2 or len(c) < 2:
        return {"n_baseline": len(b), "n_candidate": len(c), "note": "need >=2 replicates per side"}

    def stats(xs: list[float]) -> tuple[float, float]:
        m = mean(xs)
        var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
        return m, var

    mb, vb = stats(b)
    mc, vc = stats(c)
    seb, sec = math.sqrt(vb / len(b)), math.sqrt(vc / len(c))
    t = (mc - mb) / math.sqrt(seb**2 + sec**2)
    try:
        from statistics import NormalDist

        p = 2 * (1 - NormalDist().cdf(abs(t)))
    except Exception:
        p = None
    return {
        "n_baseline": len(b),
        "n_candidate": len(c),
        "baseline_means": [round(x, 4) for x in b],
        "candidate_means": [round(x, 4) for x in c],
        "baseline_mean": round(mb, 4),
        "candidate_mean": round(mc, 4),
        "delta": round(mc - mb, 4),
        "noise_floor_sd": round(math.sqrt(vb), 4),
        "t": round(t, 3),
        "p_two_sided": round(p, 4) if p is not None else None,
        "effect_over_noise": round((mc - mb) / math.sqrt(vb), 2) if vb > 0 else None,
    }


def compare(baseline: str, candidates: list[str]) -> dict:
    base = load(baseline)
    base_rows = base.get("per_question", {})
    out: dict = {"baseline": baseline, "baseline_n": len(base_rows), "variants": {}}

    for cand in candidates:
        c = load(cand)
        c_rows = c.get("per_question", {})
        common = sorted(set(base_rows) & set(c_rows))
        entry: dict = {
            "n_cand": len(c_rows),
            "n_paired": len(common),
            "metrics": {},
            "gates": {},
        }
        if common:
            # Older result files predate some metrics; compare only what both
            # sides actually recorded rather than raising or silently zero-filling.
            available = [m for m in METRICS if all(m in base_rows[q] and m in c_rows[q] for q in common)]
            entry["skipped_metrics"] = [m for m in METRICS if m not in available]
            for m in available:
                b = sum(base_rows[q][m] for q in common) / len(common)
                v = sum(c_rows[q][m] for q in common) / len(common)
                entry["metrics"][m] = {
                    "baseline": round(b, 4),
                    "candidate": round(v, 4),
                    "delta": round(v - b, 4),
                }
            recall = entry["metrics"]["citation_recall"]["candidate"]
            prec = entry["metrics"]["citation_precision"]["candidate"]
            d_recall = entry["metrics"]["citation_recall"]["delta"]
            gates = {
                "recall_at_least_gate": recall >= GATE_RECALL,
                "precision_at_least_gate": prec >= GATE_PRECISION,
                # The actual bar: beat the shipped baseline on the binding metric.
                "beats_baseline_recall": d_recall > 0,
            }
            if "false_abstention" in available:
                gates["false_abstention_within_gate"] = (
                    entry["metrics"]["false_abstention"]["candidate"] <= GATE_FALSE_ABSTENTION
                )
                gates["no_false_abstention_regression"] = (
                    entry["metrics"]["false_abstention"]["delta"] <= 0
                )
            if "hallucination_detected" in available:
                gates["no_hallucination_regression"] = (
                    entry["metrics"]["hallucination_detected"]["delta"] <= 0
                )
            if "is_padded" in available:
                # Guards against a "win" bought by citing the entire window:
                # recall goes to ~1.0 while precision falls to n_gold/n_pool.
                gates["not_citing_everything"] = (
                    entry["metrics"]["is_padded"]["candidate"] <= 0.10
                )
            entry["gates"] = gates
            entry["gates"]["all_pass"] = all(gates.values())
            entry["paired"] = {
                m: paired_stats(base_rows, c_rows, common, m)
                for m in ("citation_recall", "citation_precision", "distinct_cited", "has_marker")
                if m in available
            }
            entry["replicate_level"] = {
                m: replicate_level(base_variant=baseline, cand_variant=cand, metric=m)
                for m in ("citation_recall", "citation_precision", "abstained")
            }
        out["variants"][cand] = entry
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default="legacy")
    ap.add_argument("--candidates", default="cand_cite_only,cand_cite_breadth")
    args = ap.parse_args()

    cands = [c.strip() for c in args.candidates.split(",") if c.strip()]
    result = compare(args.baseline, cands)

    print("=" * 78)
    print(f"Paired comparison vs {args.baseline}  (baseline n={result['baseline_n']})")
    print("=" * 78)
    for cand, e in result["variants"].items():
        if not e["n_paired"]:
            print(f"\n{cand}: NO OVERLAP (baseline n={e['baseline_n']}, cand n={e['n_cand']})")
            continue
        print(f"\n{cand}   (paired n={e['n_paired']})")
        print(f"  {'metric':<24} {'baseline':>9} {'cand':>9} {'delta':>9}")
        for m, d in e["metrics"].items():
            mark = ""
            if m in LOWER_IS_BETTER:
                mark = "  WORSE" if d["delta"] > 0 else "  better"
            elif d["delta"] != 0:
                mark = "  better" if d["delta"] > 0 else "  WORSE"
            print(f"  {m:<24} {d['baseline']:>9.4f} {d['candidate']:>9.4f} {d['delta']:>+9.4f}{mark}")
        if e.get("skipped_metrics"):
            print(f"  (skipped, not recorded by both sides: {', '.join(e['skipped_metrics'])})")
        if e.get("paired"):
            print("  paired (within-qid) tests:")
            for m, s in e["paired"].items():
                if not s.get("n"):
                    continue
                p = s.get("p_two_sided")
                pstr = f"{p:.4f}" if p is not None else "n/a"
                sig = ""
                if p is not None and p < 0.05:
                    sig = "  <-- significant"
                print(
                    f"    {m:<22} n={s['n']:<3} delta={s['mean_delta']:+.4f} "
                    f"sd={s['sd_delta']:.4f} se={s['se']:.4f} t={s['t']:+.2f} p={pstr} "
                    f"(+{s['improved']}/-{s['worsened']}/={s['unchanged']}){sig}",
                )
        rl = e.get("replicate_level") or {}
        if rl:
            print("  replicate-level (population mean, run is the unit):")
            for m, s in rl.items():
                if not s or "delta" not in s:
                    continue
                p = s.get("p_two_sided")
                pstr = f"{p:.4f}" if p is not None else "n/a"
                sig = "  <-- significant" if p is not None and p < 0.05 else ""
                print(
                    f"    {m:<22} base={s['baseline_mean']:.4f} (sd {s['noise_floor_sd']:.4f}) "
                    f"cand={s['candidate_mean']:.4f} delta={s['delta']:+.4f} "
                    f"t={s['t']:+.2f} p={pstr} effect/noise={s['effect_over_noise']}{sig}",
                )
        print("  gates:")
        for k, v in e["gates"].items():
            print(f"    {'PASS' if v else 'FAIL'}  {k}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / "baseline_citation_comparison.json"
    out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwritten: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

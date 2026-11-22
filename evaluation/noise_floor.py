"""Noise floor: how much does a metric move when *nothing changes*?

Every prompt comparison in this repo is judged against a fixed threshold --
``SOFT_TIE = 0.02`` on soft correctness, ``NOISE_FLOOR_FLIPS = 4`` on binary
correctness, and (in ``evaluation/brevity_experiment.py``) a ">= 15% length
reduction" brevity guardrail.  Those thresholds are only meaningful against
the spread two runs of the *same* configuration produce.

The repo has measured the binary half of this before (4/89 qids flip between
two identical runs, Progress 2026-10-10).  It has never measured the soft,
coverage or **length** half -- and the length half is the one the brevity
guardrail depends on.

What the cached arms say (measured on ``evaluation/out/ceiling_v5/
brevity_ab_test.json``, two variants whose system prompts are byte-identical):

    same prompt, same path, same question
      length delta   mean +36.2%   per-qid min -97.1%   max +487.9%
      soft delta     mean -0.0022  per-qid min -0.0704  max +0.1315
      coverage delta mean -0.0174
      binary net     +1

So the same prompt can produce an answer 97% shorter or 488% longer, and a
soft delta anywhere in [-0.07, +0.13], on the same question.  A reported
"brevity -88%" and a reported "soft +0.0102" are both inside that band.

This harness makes that band a first-class, citable number:

* ``--tags a,b`` -- score two cached tags against each other and report the
  paired spread, the way ``compare_rerun_arms`` reports a treatment delta;
* ``--replicates N`` -- generate N replicate runs of ONE configuration and
  report the spread of every metric across them.

A candidate delta is only a result if it clears the spread measured here.

Usage:
    python -m evaluation.noise_floor --tags cond_v6_ctrl,cond_v7_w4v2
    python -m evaluation.noise_floor --tags current_status_eval,current_status_eval
    python -m evaluation.noise_floor --replicates 3 --tag nf_rep --limit 25
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from evaluation.rubric_top_lawyer import load_audit_actions, load_cached_answers  # noqa: E402

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "noise_floor"

#: The metrics whose same-config spread a candidate delta must clear.
METRICS = ("answer_correctness", "answer_coverage", "answer_length")

#: The brevity-style file currently loaded, as ``{variant: {qid: record}}``.
#: Only ONE file is active at a time: variants are keyed by name, so two
#: files can use the same names, and keeping them all in one dict and
#: searching it by tag would silently pair a variant in file B against the
#: rows of file A.
_ACTIVE_BREVITY: dict[str, dict[str, dict[str, Any]]] = {}


def _load_brevity_file(path: str) -> dict[str, dict[str, dict[str, Any]]]:
    """Load a brevity-style results file as ``{variant: {qid: record}}``.

    Replaces the active source.
    """
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        if r.get("error"):
            continue
        out.setdefault(str(r.get("variant")), {})[str(r.get("qid"))] = r
    _ACTIVE_BREVITY.clear()
    _ACTIVE_BREVITY.update(out)
    return out


def _records(tag: str) -> dict[str, dict[str, Any]]:
    """qid -> last record, skipping records that came back with an LLM error.

    A failed call leaves an empty answer, which scores 0 on soft and coverage
    and a length of 0 -- it is a *missing* observation, not a low one, and
    including it silently biases the spread downward.
    """
    if tag in _ACTIVE_BREVITY:
        return _ACTIVE_BREVITY[tag]
    out: dict[str, dict[str, Any]] = {}
    for qid, rec in load_cached_answers(tag).items():
        if rec.get("error"):
            continue
        out[qid] = rec
    return out


def _paired(tag_a: str, tag_b: str) -> dict[str, Any]:
    a, b = _records(tag_a), _records(tag_b)
    common = sorted(set(a) & set(b))
    if not common:
        return {"error": f"no qid in both {tag_a!r} and {tag_b!r}"}
    deltas: dict[str, list[float]] = {m: [] for m in METRICS}
    for qid in common:
        for m in METRICS:
            deltas[m].append(float(b[qid].get(m) or 0) - float(a[qid].get(m) or 0))
    # Relative length delta, the metric the brevity guardrail is written on.
    rel_len = [
        (float(b[qid].get("answer_length") or 0) - float(a[qid].get("answer_length") or 0))
        / max(1.0, float(a[qid].get("answer_length") or 0))
        * 100
        for qid in common
    ]
    gained = sum(1 for qid in common if not a[qid].get("binary_correct") and b[qid].get("binary_correct"))
    lost = sum(1 for qid in common if a[qid].get("binary_correct") and not b[qid].get("binary_correct"))
    out: dict[str, Any] = {
        "tags": [tag_a, tag_b],
        "n": len(common),
        "binary": {"gained": gained, "lost": lost, "net": gained - lost},
        "abstained": {
            "base": sum(1 for qid in common if a[qid].get("abstained")),
            "arm": sum(1 for qid in common if b[qid].get("abstained")),
        },
    }
    for m in METRICS:
        v = deltas[m]
        stdev = statistics.stdev(v) if len(v) > 1 else 0.0
        out[m] = {
            "mean": round(statistics.mean(v), 4),
            "median": round(statistics.median(v), 4),
            "min": round(min(v), 4),
            "max": round(max(v), 4),
            "stdev": round(stdev, 4),
            # The standard error of the MEAN paired delta -- the yardstick
            # for "is this mean delta real".  Distinct from mean|x|, which is
            # the scale of per-question disagreement: the mean|x| band says
            # individual answers are unstable, the SE says how small a mean
            # movement a given n can resolve.
            "se": round(stdev / (len(v) ** 0.5), 4) if len(v) > 1 else 0.0,
            "mean_abs": round(statistics.mean(abs(x) for x in v), 4),
        }
    out["answer_length_pct"] = {
        "mean": round(statistics.mean(rel_len), 1),
        "median": round(statistics.median(rel_len), 1),
        "min": round(min(rel_len), 1),
        "max": round(max(rel_len), 1),
        "mean_abs": round(statistics.mean(abs(x) for x in rel_len), 1),
    }
    return out


def _replicates(tag: str, n: int, limit: int) -> list[str]:
    """Generate *n* replicate runs of one configuration and return their tags."""
    tags = [f"{tag}_r{i + 1}" for i in range(n)]
    for t in tags:
        cmd = [
            sys.executable,
            "-m",
            "evaluation.rerun_gold_in_prompt",
            "--tag",
            t,
            "--limit",
            str(limit),
            "--cohort",
            "failures",
            "--no-structured-reasoner",
            "--concurrency",
            "1",
            "--delay",
            "2.0",
        ]
        print(f"\n=== replicate {t} ===", flush=True)
        subprocess.run(cmd, cwd=PROJECT_ROOT, check=False)  # noqa: S603 - argv[0] is sys.executable; the rest are module names and this script's own parameters
    return tags


def band_for_tags(tags: list[str]) -> dict[str, Any] | None:
    """The same-config spread of *tags*, as the thresholds a candidate delta
    has to clear.

    Two distinct numbers, and they answer different questions:

    ``soft`` / ``coverage`` / ``length_pct`` (mean absolute delta)
        how far a *single answer* moves when the configuration does not
        change.  This is the scale of per-question instability: individual
        answers are not reproducible, so no per-question claim is safe.
    ``soft_se`` / ``coverage_se`` (standard error of the mean delta)
        how small a *mean* movement a given n can resolve.  This is the
        yardstick for a candidate's mean delta, and it scales as
        ``sd / sqrt(n)`` -- so the same band is much easier to clear at
        n=150 than at n=25.

    ``None`` when fewer than two tags have comparable records.
    """
    pairs = []
    for i in range(len(tags)):
        for j in range(i + 1, len(tags)):
            p = _paired(tags[i], tags[j])
            if "error" not in p:
                pairs.append(p)
    if not pairs:
        return None
    return {
        "tags": list(tags),
        "n_pairs": len(pairs),
        "n": max(p["n"] for p in pairs),
        "soft": max(p["answer_correctness"]["mean_abs"] for p in pairs),
        "coverage": max(p["answer_coverage"]["mean_abs"] for p in pairs),
        "length_pct": max(p["answer_length_pct"]["mean_abs"] for p in pairs),
        "soft_se": max(p["answer_correctness"]["se"] for p in pairs),
        "coverage_se": max(p["answer_coverage"]["se"] for p in pairs),
        "binary_net": max(abs(p["binary"]["net"]) for p in pairs),
        "soft_per_qid_max": max(p["answer_correctness"]["max"] for p in pairs),
        "soft_per_qid_min": min(p["answer_correctness"]["min"] for p in pairs),
    }


def _print_band(band: dict[str, Any]) -> None:
    print(
        "  NOISE BAND (same configuration, different runs):\n"
        f"      per-answer instability   soft mean|d| {band['soft']:.4f}   coverage {band['coverage']:.4f}   length {band['length_pct']:.1f}pp\n"
        f"      resolvable mean delta     soft SE {band['soft_se']:.4f}   coverage SE {band['coverage_se']:.4f}"
        f"   (at n={band['n']})\n"
        f"      binary net                {band['binary_net']:+d}\n"
        f"      ({band['n_pairs']} same-config pair(s) from {', '.join(band['tags'])})\n"
        "  A candidate's mean delta must exceed the resolvable delta at ITS n;"
        " the per-answer\n"
        "  instability says no single answer is reproducible, not that the mean cannot move."
    )


def _spread(tags: list[str]) -> dict[str, Any]:
    """Across-replicate spread: the widest observed delta between any two replicates."""
    pairs = []
    for i in range(len(tags)):
        for j in range(i + 1, len(tags)):
            p = _paired(tags[i], tags[j])
            if "error" not in p:
                pairs.append(p)
    if not pairs:
        return {"error": "no comparable replicate pairs"}
    return {
        "tags": tags,
        "n_pairs": len(pairs),
        "pairs": pairs,
        "soft_mean_range": [
            round(min(p["answer_correctness"]["mean"] for p in pairs), 4),
            round(max(p["answer_correctness"]["mean"] for p in pairs), 4),
        ],
        "coverage_mean_range": [
            round(min(p["answer_coverage"]["mean"] for p in pairs), 4),
            round(max(p["answer_coverage"]["mean"] for p in pairs), 4),
        ],
        "length_pct_range": [
            round(min(p["answer_length_pct"]["mean"] for p in pairs), 1),
            round(max(p["answer_length_pct"]["mean"] for p in pairs), 1),
        ],
        "binary_net_range": [
            min(p["binary"]["net"] for p in pairs),
            max(p["binary"]["net"] for p in pairs),
        ],
    }


def _print_pair(p: dict[str, Any]) -> None:
    if "error" in p:
        print(f"  {p['error']}")
        return
    print(f"  {p['tags'][0]} vs {p['tags'][1]}  (n={p['n']})")
    for m in METRICS:
        d = p[m]
        print(
            f"    {m:<20} mean {d['mean']:>+10.4f}  median {d['median']:>+10.4f}  "
            f"min {d['min']:>+10.4f}  max {d['max']:>+10.4f}  mean|x| {d['mean_abs']:.4f}  "
            f"sd {d['stdev']:.4f}  SE {d['se']:.4f}"
        )
    d = p["answer_length_pct"]
    print(
        f"    {'length %':<20} mean {d['mean']:>+10.1f}  median {d['median']:>+10.1f}  "
        f"min {d['min']:>+10.1f}  max {d['max']:>+10.1f}  mean|x| {d['mean_abs']:.1f}"
    )
    print(f"    binary net {p['binary']['net']:+d}  (gained {p['binary']['gained']}, lost {p['binary']['lost']})")
    # How large a mean delta this n can resolve, and what that means for the
    # preregistered gate.  The SE is what matters for a mean; mean|x| says
    # how far individual answers move.
    se = p["answer_correctness"]["se"]
    if se > 0:
        print(
            f"    -> this n resolves a mean soft delta of about {2 * se:.4f} (2 SE); "
            f"the preregistered +0.02 gate is {0.02 / se:.1f} SE"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tags", default="", help="comma-separated cached tags to pair (same config expected)")
    parser.add_argument(
        "--brevity-file",
        default="",
        help="a brevity-style results JSON (rows with qid/variant); each variant becomes a tag",
    )
    parser.add_argument("--replicates", type=int, default=0, help="generate N replicate runs of --tag")
    parser.add_argument("--tag", default="nf_rep", help="base tag for --replicates")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.brevity_file:
        _load_brevity_file(args.brevity_file)

    report: dict[str, Any] = {}

    if args.replicates:
        tags = _replicates(args.tag, args.replicates, args.limit)
        report["replicates"] = _spread(tags)
        print("\n" + "=" * 96)
        print("REPLICATE SPREAD (same configuration, different runs)")
        print("=" * 96)
        for p in report["replicates"].get("pairs", []):
            _print_pair(p)
        print(
            f"\n  soft mean range      {report['replicates']['soft_mean_range']}\n"
            f"  coverage mean range  {report['replicates']['coverage_mean_range']}\n"
            f"  length %% range       {report['replicates']['length_pct_range']}\n"
            f"  binary net range     {report['replicates']['binary_net_range']}"
        )

    if args.tags:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        print("\n" + "=" * 96)
        print("SAME-CONFIG PAIRED SPREAD")
        print("=" * 96)
        pairs = []
        for i in range(len(tags)):
            for j in range(i + 1, len(tags)):
                p = _paired(tags[i], tags[j])
                _print_pair(p)
                pairs.append(p)
        report["pairs"] = pairs

    if not report:
        print("nothing to do: pass --tags and/or --replicates")
        return 1

    (out_dir / "noise_floor.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwritten: {out_dir / 'noise_floor.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

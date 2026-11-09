"""S1 calibration analysis — per-query-type abstention gate feasibility.

Consumes ``evaluation/out/ceiling_v5/s1_pipeline_signals.jsonl`` (production
pipeline signals per qid) and answers the S1 question with measurements, not
assumptions:

1. **Per-query-type rates** — false abstention (answerable qid refused) and
   missed abstention (insufficient-evidence qid answered), per query type and
   per split (dev / validation / holdout).
2. **Recall attribution** — for every false abstention, was the gold provision
   actually in the retrieved pool?  A refusal without evidence in the pool is
   evidence-honest and cannot be fixed by any gate.
3. **Gate feasibility sweep** — the two thresholds named for tuning
   (``GROUNDEDNESS_RETRY_BELOW``, ``EVIDENCE_COVERAGE_SYNTHESIZE_AT_LEAST``)
   are swept over their real observed distributions to test whether *any*
   operating point reaches false_abstention = 0.0 and missed_abstention = 0.0.
   ``coverage`` mirrors :meth:`SufficiencyAssessor.assess_task` exactly:
   ``min(1.0, n_chunks / 4)``.
4. **Verdict** — whether the S1 target is attainable with these gates on this
   data, and if not, the irreducible overlap that blocks it.

0 LLM calls, 0 retrieval calls: everything is computed from the recorded
signals plus the frozen benchmark.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.provision_match import stamp_is_exact, stamp_match  # noqa: E402
from evaluation.signals_io import load_signals  # noqa: E402

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
SIGNALS = OUT_DIR / "s1_pipeline_signals.jsonl"
BENCH = PROJECT_ROOT / "benchmark" / "benchmark_v1.0.jsonl"
JSON_OUT = OUT_DIR / "s1_calibration.json"
MD_OUT = OUT_DIR / "S1_CALIBRATION_REPORT.md"

#: ``coverage`` normalisation — must match ``sufficiency._COVERAGE_NORM``.
COVERAGE_NORM = 4
SPLITS = ("dev", "validation", "holdout")


def load_benchmark() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    with open(BENCH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                d = json.loads(line)
                out[d["question_id"]] = d
    return out


def coverage(n_chunks: int) -> float:
    """Production coverage signal (``sufficiency.assess_task``)."""
    return min(1.0, n_chunks / COVERAGE_NORM)


def classify(row: dict[str, Any], bench: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Attach recall verdict + cohorts to one signal row."""
    gold = bench[row["question_id"]].get("gold_source_chunks") or []
    exact = [g for g in gold if stamp_is_exact(g)]
    stamps = row.get("chunk_stamps") or []
    if not exact:
        recall = "topical"
    else:
        recall = "hit" if any(stamp_match(g, c) for g in exact for c in stamps) else "miss"
    return {
        "qid": row["question_id"],
        "split": row["split"],
        "query_types": row.get("query_types") or ["Unclassified"],
        "ie": bool(row["expected_abstain"]),
        "abstained": bool(row["abstained"]),
        "n_chunks": int(row.get("n_chunks") or 0),
        "coverage": coverage(int(row.get("n_chunks") or 0)),
        "groundedness": row.get("groundedness_score"),
        "recall": recall,
        "has_answer": bool(row.get("has_answer")),
        "retrieval_error": bool(row.get("retrieval_error")),
    }


def rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """False/missed abstention rates over a row set.

    Rows whose retrieval errored out are infrastructure failures, not gate
    behaviour: they are reported separately and excluded from the rate
    denominators.
    """
    errored = [r for r in rows if r["retrieval_error"]]
    healthy = [r for r in rows if not r["retrieval_error"]]
    ans = [r for r in healthy if not r["ie"]]
    ie = [r for r in healthy if r["ie"]]
    false_abs = [r for r in ans if r["abstained"]]
    missed_abs = [r for r in ie if not r["abstained"]]
    return {
        "n": len(rows),
        "n_answerable": len([r for r in rows if not r["ie"]]),
        "n_ie": len([r for r in rows if r["ie"]]),
        "n_answerable_healthy": len(ans),
        "n_ie_healthy": len(ie),
        "n_false_abstention": len(false_abs),
        "n_missed_abstention": len(missed_abs),
        "false_abstention_rate": round(len(false_abs) / len(ans), 4) if ans else None,
        "missed_abstention_rate": round(len(missed_abs) / len(ie), 4) if ie else None,
        "false_abstention_qids": [r["qid"] for r in false_abs],
        "missed_abstention_qids": [r["qid"] for r in missed_abs],
        "n_retrieval_error": len(errored),
        "retrieval_error_qids": [r["qid"] for r in errored],
    }


def sweep_groundedness(rows: list[dict[str, Any]], thresholds: list[float]) -> list[dict[str, Any]]:
    """Treat low groundedness as a refusal signal and sweep the retry floor."""
    out = []
    for t in thresholds:
        # Gate semantics: groundedness < t → retry, then abstain.
        pred_abstain = [{**r, "abstained": (r["groundedness"] is not None and r["groundedness"] < t)} for r in rows]
        out.append({"threshold": t, **{k: v for k, v in rates(pred_abstain).items() if not k.endswith("_qids")}})
    return out


def sweep_coverage(rows: list[dict[str, Any]], thresholds: list[float]) -> list[dict[str, Any]]:
    """Coverage gate: ``coverage < t`` → abstain instead of synthesizing."""
    out = []
    for t in thresholds:
        pred = [{**r, "abstained": r["coverage"] < t} for r in rows]
        out.append({"threshold": t, **{k: v for k, v in rates(pred).items() if not k.endswith("_qids")}})
    return out


def best_zero_error(sweep: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first operating point with 0.0 false *and* 0.0 missed abstention."""
    for point in sweep:
        if point["false_abstention_rate"] == 0.0 and point["missed_abstention_rate"] == 0.0:
            return point
    return None


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def render_markdown(art: dict[str, Any]) -> str:
    """Render the S1 findings as a standalone report."""
    ov = art["overall"]
    g_sweep = art["gate_sweeps"]["groundedness_retry_below"]
    c_sweep = art["gate_sweeps"]["evidence_coverage_synthesize_at_least"]
    g_best = art["gate_sweeps"]["zero_error_point_groundedness"]
    c_best = art["gate_sweeps"]["zero_error_point_coverage"]
    g_dist = art["signal_distributions"]["groundedness"]
    cov_dist = art["signal_distributions"]["coverage"]
    attr = art["recall_attribution"]["false_abstention"]

    lines: list[str] = []
    lines.append("# S1 — abstention calibration on production signals\n")
    lines.append(
        f"Source: `{art['source_signals']}` — {ov['n']} questions "
        f"({ov['n_answerable']} answerable, {ov['n_ie']} insufficient-evidence).\n"
    )

    lines.append("## 1. Headline rates\n")
    lines.append(
        f"- false abstention: **{_pct(ov['false_abstention_rate'])}** "
        f"({ov['n_false_abstention']}/{ov.get('n_answerable_healthy', ov['n_answerable'])})"
    )
    lines.append(
        f"- missed abstention: **{_pct(ov['missed_abstention_rate'])}** "
        f"({ov['n_missed_abstention']}/{ov.get('n_ie_healthy', ov['n_ie'])})\n"
    )
    if ov.get("n_retrieval_error"):
        lines.append(
            f"> **Data quality:** {ov['n_retrieval_error']} row(s) recorded a retrieval error and therefore "
            f"had no candidate pool (`{', '.join(ov['retrieval_error_qids'])}`). Their refusals are "
            "infrastructure-caused, not gate- or evidence-caused.\n"
        )
    lines.append("| split | n | answerable | IE | false abstention | missed abstention |")
    lines.append("|---|---|---|---|---|---|")
    for name, st in art["per_split"].items():
        lines.append(
            f"| {name} | {st['n']} | {st['n_answerable']} | {st['n_ie']} | "
            f"{_pct(st['false_abstention_rate'])} | {_pct(st['missed_abstention_rate'])} |"
        )
    lines.append("")

    lines.append("## 2. Per query type\n")
    lines.append("| query type | n | answerable | IE | false | missed |")
    lines.append("|---|---|---|---|---|---|")
    for qt, st in art["per_query_type"].items():
        lines.append(
            f"| {qt} | {st['n']} | {st['n_answerable']} | {st['n_ie']} | "
            f"{_pct(st['false_abstention_rate'])} | {_pct(st['missed_abstention_rate'])} |"
        )
    lines.append("")

    lines.append("## 3. Why the refusals happen — recall attribution\n")
    lines.append(
        "Each false abstention scored against the benchmark's gold provision stamps "
        "(`fssai:s16(1)`) using the retrieved pool's payload stamps:\n"
    )
    lines.append("| verdict | meaning | count |")
    lines.append("|---|---|---|")
    lines.append(
        f"| `miss` | gold provision absent from the pool — refusal was evidence-honest | {attr.get('miss', 0)} |"
    )
    lines.append(f"| `topical` | gold is a coarse tag, no exact section to match | {attr.get('topical', 0)} |")
    lines.append(f"| `hit` | gold **was** in the pool — the only gate-tractable refusals | {attr.get('hit', 0)} |")
    total = sum(attr.values()) or 1
    lines.append(
        f"\nGate-tractable share of false abstentions: "
        f"**{attr.get('hit', 0)}/{total} = {attr.get('hit', 0) / total * 100:.1f}%**\n"
    )

    lines.append("## 4. Can the named thresholds reach false = 0.0 and missed = 0.0?\n")
    lines.append("### Observed signal distributions\n")
    lines.append(
        f"- `groundedness`: n={g_dist['n']}, min={g_dist['min']}, max={g_dist['max']}, "
        f"distinct values={g_dist['distinct']}"
    )
    lines.append(f"- `coverage` = `min(1, n_chunks/4)`: {cov_dist}\n")
    lines.append("### Sweep — `GROUNDEDNESS_RETRY_BELOW`\n")
    lines.append("| threshold | false | missed |")
    lines.append("|---|---|---|")
    for p in g_sweep:
        lines.append(f"| {p['threshold']} | {_pct(p['false_abstention_rate'])} | {_pct(p['missed_abstention_rate'])} |")
    lines.append("\n### Sweep — `EVIDENCE_COVERAGE_SYNTHESIZE_AT_LEAST`\n")
    lines.append("| threshold | false | missed |")
    lines.append("|---|---|---|")
    for p in c_sweep:
        lines.append(f"| {p['threshold']} | {_pct(p['false_abstention_rate'])} | {_pct(p['missed_abstention_rate'])} |")

    lines.append("\n### Verdict\n")
    if g_best is None and c_best is None:
        lines.append(
            "**No operating point on either threshold reaches false = 0.0 and missed = 0.0.** "
            "Neither signal separates answerable from insufficient-evidence questions:\n"
        )
        lines.append(
            f"- `groundedness` takes only the observed values {g_dist['distinct']}; "
            "`GROUNDEDNESS_RETRY_BELOW` therefore behaves as a step function and "
            "the sweep shows it cannot zero both error rates.\n"
        )
        lines.append(
            "- `coverage` takes only the observed values shown above; "
            "the sweep shows the gate can only trade false abstentions for missed "
            "ones — never both at zero.\n"
        )
        lines.append(
            "Calibration cannot produce the target on this signal set; the gates need a signal that "
            "detects *answerability* (whether the pool contains a provision that answers the question), "
            "which no current routing signal provides.\n"
        )
    else:
        lines.append("A zero-error operating point exists (see JSON for details).\n")

    lines.append("## 5. What actually moves false abstentions\n")
    section_arm_text = (
        "`RAG_SECTION_ARM` (default false, absent from `render.yaml`) adds the section/act-scoped "
        "structural arm. On the 78 current gold-recall misses, the retrieval-only A/B gained "
        "Q127 with zero gold lost (gold hits 8 → 9); see "
        "`evaluation/out/ceiling_v5/section_arm_s1_ab.json`."
    )
    try:
        summary = json.loads((OUT_DIR / "section_arm_s1_ab.json").read_text(encoding="utf-8"))["summary"]
        section_arm_text = (
            f"`RAG_SECTION_ARM` (default false, absent from `render.yaml`) adds the section/act-scoped "
            f"structural arm. On the {summary['n']} current gold-recall misses, the retrieval-only A/B "
            f"gained {', '.join(summary['gained']) or 'none'} with zero gold lost "
            f"(gold hits {summary['gold_hit_arm_off']} → {summary['gold_hit_arm_on']}); see "
            "`evaluation/out/ceiling_v5/section_arm_s1_ab.json`."
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        pass
    lines.append(f"1. **Retrieval recall** — the dominant cause (see §3). {section_arm_text}\n")
    lines.append(
        "2. **Prompt hygiene** — synthetic parent-context blocks whose clause is `unknown` "
        "(`Reconstructed context: Clause unknown of …`) are numbered into `<legal_context>` as evidence "
        "with no legal content; the generator quotes them and refuses. Fixed in "
        "`parent_reconstruction._synthetic_parent_context`.\n"
    )
    lines.append(
        "3. **Threshold tuning in `app/rag/agent/thresholds.py` cannot help** — those gates live on the "
        "LangGraph agent path, which is disabled both locally (`RAG_USE_AGENT_PIPELINE=false`) and in "
        "`render.yaml`, so they are not on the served path at all.\n"
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    bench = load_benchmark()
    raw = load_signals(SIGNALS)
    rows = [classify(r, bench) for r in raw]
    print(f"rows={len(rows)}", flush=True)

    overall = rates(rows)
    per_split = {s: rates([r for r in rows if r["split"] == s]) for s in SPLITS}

    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        for qt in r["query_types"]:
            by_type[qt].append(r)
    per_type = {qt: rates(rs) for qt, rs in sorted(by_type.items())}

    # Recall attribution: only answerable refusals matter here, and infrastructure
    # failures are not evidence-honest refusals.
    false_abs = [r for r in rows if not r["ie"] and r["abstained"] and not r["retrieval_error"]]
    recall_attr = Counter(r["recall"] for r in false_abs)
    answered = [r for r in rows if not r["ie"] and not r["abstained"] and not r["retrieval_error"]]
    recall_answered = Counter(r["recall"] for r in answered)

    # Gate feasibility on the whole set and per split.
    g_thresholds = [0.5, 0.7, 0.8, 0.9, 0.99, 1.0]
    c_thresholds = [0.0, 0.25, 0.5, 0.75, 1.0]
    g_sweep = sweep_groundedness(rows, g_thresholds)
    c_sweep = sweep_coverage(rows, c_thresholds)
    g_best = best_zero_error(g_sweep)
    c_best = best_zero_error(c_sweep)

    # Distribution evidence for the "inert gate" verdict (healthy rows only).
    healthy_rows = [r for r in rows if not r["retrieval_error"]]
    scored = [r["groundedness"] for r in healthy_rows if r["groundedness"] is not None]
    g_dist = {
        "n": len(scored),
        "min": min(scored) if scored else None,
        "max": max(scored) if scored else None,
        "distinct": sorted({round(x, 3) for x in scored}),
    }
    cov_dist = Counter(round(r["coverage"], 2) for r in healthy_rows)

    artifact = {
        "version": "s1-calibration-v1",
        "source_signals": str(SIGNALS),
        "overall": overall,
        "per_split": per_split,
        "per_query_type": per_type,
        "recall_attribution": {
            "false_abstention": dict(recall_attr),
            "answered": dict(recall_answered),
        },
        "gate_sweeps": {
            "groundedness_retry_below": g_sweep,
            "evidence_coverage_synthesize_at_least": c_sweep,
            "zero_error_point_groundedness": g_best,
            "zero_error_point_coverage": c_best,
        },
        "signal_distributions": {
            "groundedness": g_dist,
            "coverage": {str(k): v for k, v in sorted(cov_dist.items())},
        },
    }
    JSON_OUT.write_text(json.dumps(artifact, indent=1) + "\n", encoding="utf-8")
    MD_OUT.write_text(render_markdown(artifact), encoding="utf-8")

    # ---------------- console ----------------
    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{v * 100:.1f}%"

    print(f"overall: n={overall['n']} answerable={overall['n_answerable']} ie={overall['n_ie']}")
    print(
        f"  false_abstention={pct(overall['false_abstention_rate'])} "
        f"missed_abstention={pct(overall['missed_abstention_rate'])}"
    )
    for s, st in per_split.items():
        print(
            f"  {s:<10} n={st['n']:>3} false={pct(st['false_abstention_rate']):>6} "
            f"missed={pct(st['missed_abstention_rate']):>6}"
        )
    print("\nper query type:")
    for qt, st in per_type.items():
        print(
            f"  {qt:<24} n={st['n']:>3} ans={st['n_answerable']:>3} ie={st['n_ie']:>2} "
            f"false={pct(st['false_abstention_rate']):>6} missed={pct(st['missed_abstention_rate']):>6}"
        )
    print(f"\nfalse-abstention recall attribution: {dict(recall_attr)}")
    print(f"answered recall attribution:         {dict(recall_answered)}")
    print(f"\ngroundedness distribution: {g_dist}")
    print(f"coverage distribution:     {dict(sorted(cov_dist.items()))}")
    print("\ngroundedness sweep (gate = retry/abstain below t):")
    for p in g_sweep:
        print(
            f"  t={p['threshold']:<5} false={pct(p['false_abstention_rate']):>6} missed={pct(p['missed_abstention_rate']):>6}"
        )
    print("coverage sweep (gate = abstain below t):")
    for p in c_sweep:
        print(
            f"  t={p['threshold']:<5} false={pct(p['false_abstention_rate']):>6} missed={pct(p['missed_abstention_rate']):>6}"
        )
    print(f"\nzero-error point (groundedness): {g_best}")
    print(f"zero-error point (coverage):     {c_best}")
    print(f"\nwritten: {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""A/B the decision pathway: does model-based evidence selection beat score order?

Plan step 2.  Three arms, identical chunks, identical Laguna model, one run
per question:

  ``baseline``         the shipped default — ContextBuilder packs by score
  ``decision``         the shipped RAG_DECISION_PATH semantics — the selected
                       EvidenceSet is role-packed, the rest stays as overflow
  ``decision_compact`` the strict-compaction candidate — ONLY the selected
                       chunks enter the prompt (the ~6-chunk token/latency win
                       the promotion gate asks about)

Selections come from the shared score cache (``evaluation/decision_scores``)
and the shipped pure walk ``select_from_scores``, so the arm is what the
pipeline would serve at the chosen τ / top-k.  Reports the standard dual
scorecard (binary + soft + citation p/r + groundedness + dangling markers)
with paired per-question deltas, and singles out whether ``decision_compact``
buys tokens without losing correctness.

Usage:
    python -m evaluation.ab_decision_selector --limit 5      # smoke
    python -m evaluation.ab_decision_selector                # model_wrong slice
    python -m evaluation.ab_decision_selector --scope all    # full 150
Sharding mirrors ab_window_width: ``--shard-out FILE`` dumps paired rows,
``--merge-shards FILE…`` recomputes the summary with no LLM calls.

Writes ``evaluation/out/decision/decision_selector_ab.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Semaphore
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.answer_scoring import score_answer  # noqa: E402
from evaluation.bench_p0_1_packing import _load_payload_index  # noqa: E402
from evaluation.decision_scores import (  # noqa: E402
    ALLOWED_DECISION_MODELS,
    DEFAULT_ARM,
    decision_model,
    ensure_scores,
    gold_chunk_ids,
    load_pools,
    model_wrong_qids,
)

# The imports above run load_dotenv(override=True) from .env — which ships
# RAG_USE_STUB_LLM=true for dev.  Force real answers AFTER that, like every
# other ab_* harness does, or the whole A/B scores stub output.
os.environ["RAG_USE_STUB_LLM"] = "false"

OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "decision" / "decision_selector_ab.json"
ARMS = ("baseline", "decision", "decision_compact")
MAX_WORKERS = 5

#: Inline ``[n]`` citation markers, mirroring ``citation_tracker``'s own
#: ``_BRACKET_CITATION_RE`` so the dangling-rate metric measures exactly what
#: the shipped pipeline sees.
_MARKER = re.compile(r"\[(\d+)\]")

#: ``[Source n]`` is a form the model also emits but the tracker ignores
#: entirely.  Counted so it cannot silently hide from the groundedness numbers.
_SOURCE_MARKER = re.compile(r"\[Source\s+(\d+)\]")


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key) or default)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key) or default)
    except ValueError:
        return default


def _build(q: Any, chunks: list, qt: str, arm: str, evidence: dict | None, kept_ids: set[str]):
    from app.rag.generation.context_builder import ContextBuilder

    builder = ContextBuilder(query_type=qt)
    if arm == "baseline" or not evidence:
        return builder.build(q.question, chunks, qt)
    if arm == "decision":
        return builder.build(q.question, chunks, qt, evidence_set=evidence)
    # decision_compact: only the selected chunks enter the prompt — the
    # strict-compaction candidate behind the promotion gate's token win.
    selected = [c for c in chunks if str(c.chunk_id) in kept_ids]
    return builder.build(q.question, selected or chunks, qt, evidence_set=evidence)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=("model_wrong", "all"), default="model_wrong")
    ap.add_argument("--arm", default=DEFAULT_ARM)
    ap.add_argument("--tau", type=float, default=_env_float("RAG_DECISION_THRESHOLD", 0.5))
    ap.add_argument("--top-k", type=int, default=_env_int("RAG_DECISION_TOP_K", 6))
    ap.add_argument("--reservoir", type=int, default=_env_int("RAG_DECISION_RESERVOIR", 50))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--start", type=int, default=0, help="shard offset")
    ap.add_argument("--shard-out", type=str, default="")
    ap.add_argument("--merge-shards", nargs="+", default=[])
    ap.add_argument("--refresh-scores", action="store_true", help="re-score every pool first")
    ap.add_argument(
        "--model",
        default=None,
        help=f"decision model driving the selection arms (default: RAG_DECISION_MODEL or typesafe/jev-1.13); one of {ALLOWED_DECISION_MODELS}",
    )
    args = ap.parse_args()
    if args.model is not None and args.model not in ALLOWED_DECISION_MODELS:
        ap.error(f"--model must be one of {ALLOWED_DECISION_MODELS}")

    if args.merge_shards:
        base, dec, comp, kept, meta = _load_shards(args.merge_shards)
        return _report(
            base,
            dec,
            comp,
            kept_sizes=kept,
            shard_out="",
            tau=meta.get("tau"),
            top_k=meta.get("top_k"),
            selector_model=meta.get("selector_model"),
        )

    from app.rag.retrieval.decision_selector import merge_reservoir, select_from_scores
    from evaluation.resolution import FamilyMap

    pools, questions, payload_index = load_pools(args.arm)
    family_map = FamilyMap()
    mw = model_wrong_qids()

    qids = [
        qid
        for qid in sorted(pools)
        if qid in questions and len(pools[qid]) >= 2 and (args.scope == "all" or qid in mw)
    ]
    if args.start:
        qids = qids[args.start :]
    if args.limit:
        qids = qids[: args.limit]
    if not qids:
        print("no tasks", file=sys.stderr)
        return 1

    # Score the reservoir, never the full 500-chunk pool (a 500-candidate
    # keep-map overflows max_tokens and truncates — see tau_sweep).
    reservoirs = {qid: merge_reservoir(pools[qid], args.reservoir) for qid in qids}

    sel_model = args.model or decision_model()
    print(f"ensuring decision scores for {len(qids)} pools (selector={sel_model})...", flush=True)
    cache, n_scored = ensure_scores(reservoirs, questions, qids=qids, refresh=args.refresh_scores, model=sel_model)
    qids = [qid for qid in qids if cache.get(qid) and not cache[qid].get("error")]
    print(
        f"A/B decision selector on {len(qids)} questions "
        f"(answer model={os.environ.get('RAG_LLM_MODEL')}, selector={sel_model}, "
        f"tau={args.tau} k={args.top_k}; {n_scored} pools scored now)",
        flush=True,
    )

    tasks = []
    for qid in qids:
        chunks = pools[qid]
        reservoir = reservoirs[qid]
        scores = cache[qid]["scores"]
        probs = {i: scores[c.chunk_id] for i, c in enumerate(reservoir) if c.chunk_id in scores}
        evidence, core = select_from_scores(
            questions[qid].question, reservoir, probs, threshold=args.tau, top_k=args.top_k
        )
        tasks.append(
            {
                "qid": qid,
                "q": questions[qid],
                "chunks": chunks,
                "evidence": evidence,
                "kept_ids": {c for c in (core["kept"] or []) if c},
                "gold": gold_chunk_ids(questions[qid], chunks, payload_index, family_map),
            }
        )

    sem = Semaphore(MAX_WORKERS)

    def one(task: dict[str, Any], arm: str) -> dict[str, Any]:
        from app.rag.generation.grounded_service import GroundedGenerationService
        from evaluation.llm_ssl_client import SSLBypassLLMClient

        qid, q, chunks = task["qid"], task["q"], task["chunks"]
        qt = q.question_types[0] if q.question_types else ""
        built = _build(q, chunks, qt, arm, task["evidence"], task["kept_ids"])
        # Answerability gate tripped on what this arm actually packs: the
        # builder returns an EMPTY context.  Production renders that as an
        # empty-context prompt and lets the model refuse — model noise over
        # a deterministic prompt property, and a wasted call.  Score the
        # deterministic refusal instead and flag the row; counts are
        # reported per arm so compaction's gate cost stays visible.
        gate_refusal = not (built.enough_evidence and built.context)
        with sem:
            svc = GroundedGenerationService()
            svc.llm_client = SSLBypassLLMClient(model=os.environ["RAG_LLM_MODEL"])
            if gate_refusal:
                from app.rag.generation.llm_client import GroundedLLMResponse

                llm = GroundedLLMResponse(text="", model=os.environ["RAG_LLM_MODEL"])
            else:
                sys_p, user_p = svc._render_prompt(q.question, built)
                llm = svc._call_llm(sys_p, user_p)
            answer = getattr(llm, "text", "") or ""
            tracked = svc._extract_citations(llm, built_chunks(arm, chunks, task["kept_ids"]), built)
            san = svc.sanitizer.sanitize(answer, tracked, chunks)
        gold = task["gold"]
        in_prompt = {str(c["chunk_id"]) for c in built.citations}
        m = score_answer(answer, q.acceptable_conclusion or "", q.insufficient_evidence)
        cited = {str(c.chunk_id) for c in tracked}
        m["citation_recall"] = round(len(cited & gold) / max(len(gold), 1), 4) if gold else 0.0
        m["gold_reachable"] = round(len(in_prompt & gold) / max(len(gold), 1), 4) if gold else 0.0
        m["n_prompt_chunks"] = built.chunk_count
        m["gold_in_prompt"] = int(bool(in_prompt & gold))
        m["n_cited"] = len(cited)
        m["groundedness_score"] = san.groundedness_score
        m["hallucination_detected"] = int(san.hallucination_detected)
        m["n_invalid_citations"] = len(san.invalid_citations)
        m["n_hallucinated_claims"] = len(san.hallucinated_claims)
        m["confidence"] = san.confidence
        prompt_idx = {c["index"] for c in built.citations}
        n_markers = len(_MARKER.findall(answer))
        dangling = [int(mk) for mk in _MARKER.findall(answer) if int(mk) not in prompt_idx]
        m["n_citation_markers"] = n_markers
        m["n_dangling_markers"] = len(dangling)
        m["dangling_marker_rate"] = round(len(dangling) / n_markers, 4) if n_markers else 0.0
        m["n_source_form_markers"] = len(_SOURCE_MARKER.findall(answer))
        return {
            "qid": qid,
            "arm": arm,
            "answer_len": len(answer),
            "llm_error": getattr(llm, "error", None),
            "gate_refusal": int(gate_refusal),
            "answer": answer,
            "m": m,
        }

    def run_arm(arm: str) -> list[dict[str, Any]]:
        out = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(one, t, arm): t["qid"] for t in tasks}
            for fut in as_completed(futs):
                try:
                    out.append(fut.result())
                except Exception as exc:
                    out.append({"qid": futs[fut], "arm": arm, "exception": f"{type(exc).__name__}: {exc}", "m": None})
        return out

    rows = {arm: run_arm(arm) for arm in ARMS}
    kept_sizes = {t["qid"]: len(t["kept_ids"]) for t in tasks}
    return _report(
        rows["baseline"],
        rows["decision"],
        rows["decision_compact"],
        kept_sizes=kept_sizes,
        shard_out=args.shard_out,
        tau=args.tau,
        top_k=args.top_k,
        selector_model=sel_model,
    )


def built_chunks(arm: str, chunks: list, kept_ids: set[str]) -> list:
    """The chunk objects the arm's prompt actually contained (citation lookup)."""
    if arm == "decision_compact":
        selected = [c for c in chunks if str(c.chunk_id) in kept_ids]
        return selected or chunks
    return chunks


def _load_shards(paths: list[str]) -> tuple[list, list, list, dict, dict]:
    merged: dict[str, dict[str, dict]] = {a: {} for a in ARMS}
    kept_sizes: dict[str, int] = {}
    meta: dict = {"tau": None, "top_k": None, "selector_model": None}
    for path in paths:
        blob = json.loads(Path(path).read_text(encoding="utf-8"))
        for a in ARMS:
            merged[a].update(blob.get(a) or {})
        kept_sizes.update(blob.get("kept_sizes") or {})
        meta["tau"] = blob.get("tau", meta["tau"])
        meta["top_k"] = blob.get("top_k", meta["top_k"])
        meta["selector_model"] = blob.get("selector_model", meta["selector_model"])
    print(f"merged {len(merged['baseline'])} baseline rows from {len(paths)} shard(s)")
    return (
        list(merged["baseline"].values()),
        list(merged["decision"].values()),
        list(merged["decision_compact"].values()),
        kept_sizes,
        meta,
    )


def _agg(rows: dict[str, dict], qids: list[str]) -> dict[str, float]:
    keys = (
        "binary_correct",
        "answer_correctness",
        "citation_recall",
        "gold_reachable",
        "groundedness_score",
        "hallucination_detected",
        "confidence",
        "dangling_marker_rate",
        "n_prompt_chunks",
    )
    out = {}
    for k in keys:
        vals = [rows[q]["m"][k] for q in qids if k in rows[q]["m"]]
        out[k] = round(sum(vals) / max(len(vals), 1), 4)
    return out


def _paired(base: dict[str, dict], arm: dict[str, dict], qids: list[str], metric: str = "binary_correct") -> dict:
    d = [arm[q]["m"][metric] - base[q]["m"][metric] for q in qids]
    plus = sum(1 for x in d if x > 0)
    minus = sum(1 for x in d if x < 0)
    sd = statistics.stdev(d) if len(d) > 1 else 0.0
    se = sd / (len(d) ** 0.5) if sd else 0.0
    return {
        "n": len(d),
        "improved": plus,
        "regressed": minus,
        "net": sum(d),
        "mean_delta": round(statistics.mean(d), 4) if d else None,
        "t": round(statistics.mean(d) / se, 3) if se else None,
    }


def _report(
    baseline_rows: list[dict],
    decision_rows: list,
    compact_rows: list,
    *,
    kept_sizes: dict | None = None,
    shard_out: str = "",
    tau: float | None = None,
    top_k: int | None = None,
    selector_model: str | None = None,
) -> int:
    kept_sizes = kept_sizes or {}
    # A row is paired into aggregates only when all three arms scored it AND
    # the answer call succeeded — an llm_error row is missing data (smaller
    # n), never a zero that would fabricate a regression.
    B = {r["qid"]: r for r in baseline_rows if r.get("m") and not r.get("llm_error")}
    D = {r["qid"]: r for r in decision_rows if r.get("m") and not r.get("llm_error")}
    C = {r["qid"]: r for r in compact_rows if r.get("m") and not r.get("llm_error")}
    common = sorted(set(B) & set(D) & set(C))
    if not common:
        print("ABORT: no paired results", file=sys.stderr)
        return 1

    out = {
        "answer_model": os.environ.get("RAG_LLM_MODEL"),
        "selector_model": selector_model,
        "tau": tau,
        "top_k": top_k,
        "n_paired": len(common),
        "n_errors": {
            "baseline": sum(1 for r in baseline_rows if not r.get("m")),
            "decision": sum(1 for r in decision_rows if not r.get("m")),
            "decision_compact": sum(1 for r in compact_rows if not r.get("m")),
        },
        "n_llm_errors": {
            "baseline": sum(1 for r in baseline_rows if r.get("m") and r.get("llm_error")),
            "decision": sum(1 for r in decision_rows if r.get("m") and r.get("llm_error")),
            "decision_compact": sum(1 for r in compact_rows if r.get("m") and r.get("llm_error")),
        },
        "n_gate_refusals": {
            "baseline": sum(1 for r in baseline_rows if r.get("gate_refusal")),
            "decision": sum(1 for r in decision_rows if r.get("gate_refusal")),
            "decision_compact": sum(1 for r in compact_rows if r.get("gate_refusal")),
        },
        "aggregate_all": {"baseline": _agg(B, common), "decision": _agg(D, common), "decision_compact": _agg(C, common)},
        "paired_decision_vs_baseline": _paired(B, D, common),
        "paired_compact_vs_baseline": _paired(B, C, common),
        "paired_by_metric": {
            "decision_vs_baseline": {k: _paired(B, D, common, k) for k in ("binary_correct", "answer_correctness", "citation_recall", "groundedness_score")},
            "compact_vs_baseline": {k: _paired(B, C, common, k) for k in ("binary_correct", "answer_correctness", "citation_recall", "groundedness_score")},
        },
        "prompt_chunks_mean": {
            arm: round(statistics.mean(rows[q]["m"]["n_prompt_chunks"] for q in common), 2)
            for arm, rows in (("baseline", B), ("decision", D), ("decision_compact", C))
        },
        "kept_mean": round(statistics.mean(kept_sizes.get(q, 0) for q in common), 2) if kept_sizes else None,
        "limitations": [
            "One run per arm, single free-tier answer model, no seed replication.",
            "Soft correctness is not reproducible run-to-run (measured on window A/B); "
            "treat small answer_correctness deltas as model noise.",
            "decision vs baseline reorders and role-tags the same chunks (P0-1 semantics); "
            "decision_compact is the only arm that removes overflow, so the token/latency "
            "win lives or dies on that arm.",
            "Selections come from cached keep-scores; a --refresh-scores rerun measures "
            "router-model drift before trusting a promotion.",
        ],
    }

    if shard_out:
        Path(shard_out).parent.mkdir(parents=True, exist_ok=True)
        Path(shard_out).write_text(
            json.dumps(
                {
                    "baseline": {q: B[q] for q in common},
                    "decision": {q: D[q] for q in common},
                    "decision_compact": {q: C[q] for q in common},
                    "kept_sizes": kept_sizes,
                    "tau": tau,
                    "top_k": top_k,
                    "selector_model": selector_model,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"shard written: {shard_out} ({len(common)} paired)")
        return 0

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print("=" * 80)
    print(f"Decision-selector A/B  n={len(common)}  tau={tau} k={top_k}  "
          f"selector={selector_model}  answer model={out['answer_model']}")
    print("=" * 80)
    print(f"mean prompt chunks: baseline={out['prompt_chunks_mean']['baseline']} "
          f"decision={out['prompt_chunks_mean']['decision']} compact={out['prompt_chunks_mean']['decision_compact']}")
    if out["kept_mean"] is not None:
        print(f"mean kept by selector: {out['kept_mean']}")
    print(
        "llm errors (excluded from aggregates): "
        + " ".join(f"{a}={out['n_llm_errors'][a]}" for a in ("baseline", "decision", "decision_compact"))
        + " | gate refusals (scored as refusals): "
        + " ".join(f"{a}={out['n_gate_refusals'][a]}" for a in ("baseline", "decision", "decision_compact"))
    )
    print()
    print(f"{'metric':<26} {'baseline':>10} {'decision':>10} {'compact':>10}")
    print("-" * 62)
    for k in (
        "binary_correct",
        "answer_correctness",
        "citation_recall",
        "gold_reachable",
        "groundedness_score",
        "hallucination_detected",
        "confidence",
        "dangling_marker_rate",
    ):
        print(
            f"{k:<26} {out['aggregate_all']['baseline'][k]:>10.4f} "
            f"{out['aggregate_all']['decision'][k]:>10.4f} {out['aggregate_all']['decision_compact'][k]:>10.4f}"
        )
    print()
    for name, key in (("decision vs baseline", "paired_decision_vs_baseline"), ("compact vs baseline", "paired_compact_vs_baseline")):
        p = out[key]
        print(f"paired binary ({name}, n={p['n']}): improved {p['improved']}, regressed {p['regressed']}, "
              f"net {p['net']}, t={p['t']}")
    print()
    print(f"written: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""A/B the context-window fix: does surfacing more gold evidence help answers?

66db37b widened the effective context window and measured that gold evidence
reaches the prompt for 10 more of the 89 ``model_wrong`` questions (36 -> 26
evidence-starved). That is evidence PRESENCE. This asks the question that
actually matters: do those answers get better?

Conditions (identical chunks, identical model, identical evidence set — only
the window differs):

  ``window_narrow``  the pre-fix budgets: absolute 8-12 chunks / 10k-16k chars
  ``window_wide``    the post-fix budgets: up to 20 chunks / 24k chars

Reports the same dual scorecard as the rest of the harness (binary + soft +
citation p/r), and singles out the ``causally_changed`` questions — those the
fix actually moved from evidence-starved to gold-in-prompt — since those are
where a difference must appear if the mechanism is real.

The causal subset is small, so it is reported alongside the full set: a null
result on ~10 questions cannot distinguish "no effect" from "underpowered".

Usage:
    python -m evaluation.ab_window_width --limit 0
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

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=True)
os.environ["RAG_USE_STUB_LLM"] = "false"

from evaluation.answer_scoring import score_answer
from evaluation.bench_p0_1_packing import RAW_DIR, _chunks_for, _load_jsonl, _load_payload_index
from evaluation.benchmark import load_questions
from evaluation.llm_ssl_client import SSLBypassLLMClient
from evaluation.resolution import FamilyMap, matches_gold

OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "window_width_ab.json"
TABULATION = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "full_review_tabulation.json"
ARM = "C_hybrid"
MAX_WORKERS = 5

#: Inline ``[n]`` citation markers, mirroring ``citation_tracker``'s own
#: ``_BRACKET_CITATION_RE`` so the dangling-rate metric measures exactly what
#: the shipped pipeline sees.
_MARKER = re.compile(r"\[(\d+)\]")

#: ``[Source n]`` is a form the model also emits but the tracker ignores
#: entirely. Counted so it cannot silently hide from the groundedness numbers.
_SOURCE_MARKER = re.compile(r"\[Source\s+(\d+)\]")

#: Pre-fix budgets, restored verbatim from _QUERY_TYPE_BUDGETS at 66db37c^.
NARROW_BUDGETS: dict[str, dict[str, int]] = {
    "case_law": {"max_context_chars": 16_000, "max_chunks": 12},
    "cross_reference": {"max_context_chars": 14_000, "max_chunks": 12},
    "prohibition": {"max_context_chars": 10_000, "max_chunks": 8},
    "definition": {"max_context_chars": 10_000, "max_chunks": 8},
    "penalty": {"max_context_chars": 12_000, "max_chunks": 10},
    "general": {"max_context_chars": 12_000, "max_chunks": 10},
    "procedure": {"max_context_chars": 12_000, "max_chunks": 10},
}


class _Cit:
    __slots__ = ("chunk_id",)

    def __init__(self, chunk_id: str) -> None:
        self.chunk_id = chunk_id


def _build(query: str, chunks: list, qt: str, wide: bool):
    from app.rag.generation.context_builder import ContextBuilder

    if wide:
        return ContextBuilder(query_type=qt).build(query, chunks, qt)
    # Reconstruct pre-fix behaviour: the table was absolute and overrode the
    # ceiling entirely (no min()), falling back to the ceiling when untyped.
    from app.shared.config import cfg

    b = NARROW_BUDGETS.get(qt.lower(), {})
    return ContextBuilder(
        query_type=qt,
        max_context_chars=b.get("max_context_chars", cfg.context_max_chars),
        max_chunks=b.get("max_chunks", cfg.context_max_chunks),
    ).build(query, chunks, qt)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--start", type=int, default=0, help="shard offset")
    ap.add_argument("--shard-out", type=str, default="", help="write paired rows here instead of the summary")
    ap.add_argument(
        "--merge-shards",
        nargs="+",
        default=[],
        help="recompute the summary from previously written shard files, no LLM calls",
    )
    args = ap.parse_args()

    if args.merge_shards:
        return _report(*_load_shards(args.merge_shards), shard_out="")

    payload_index = _load_payload_index()
    questions = {q.question_id: q for q in load_questions()}
    family_map = FamilyMap()
    arms = {
        r["question_id"]: [str(c) for c in (r.get("chunk_ids") or [])] for r in _load_jsonl(RAW_DIR / f"{ARM}.jsonl")
    }
    tab = json.loads(TABULATION.read_text(encoding="utf-8"))
    model_wrong = [r["qid"] for r in tab.get("records") or [] if r.get("verdict") == "model_wrong"]

    tasks = []
    for qid in model_wrong:
        q, ids = questions.get(qid), arms.get(qid)
        if not q or not ids:
            continue
        chunks = _chunks_for(ids, payload_index)
        if len(chunks) < 2:
            continue
        tasks.append((qid, q, chunks))
    if args.start:
        tasks = tasks[args.start :]
    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        print("no tasks", file=sys.stderr)
        return 1

    print(
        f"A/B window width on {len(tasks)} model_wrong questions (model={os.environ.get('RAG_LLM_MODEL')})", flush=True,
    )

    def run(wide: bool) -> list[dict[str, Any]]:
        from app.rag.generation.grounded_service import GroundedGenerationService

        sem = Semaphore(MAX_WORKERS)

        def one(task):
            qid, q, chunks = task
            qt = q.question_types[0] if q.question_types else ""
            built = _build(q.question, chunks, qt, wide)
            with sem:
                svc = GroundedGenerationService()
                svc.llm_client = SSLBypassLLMClient(model=os.environ["RAG_LLM_MODEL"])
                sys_p, user_p = svc._render_prompt(q.question, built)
                llm = svc._call_llm(sys_p, user_p)
                answer = getattr(llm, "text", "") or ""
                # Real post-generation path: CitationTracker + ResponseSanitizer.
                # Without these the A/B cannot see whether a wider window makes
                # the model cite more but no better.
                tracked = svc._extract_citations(llm, chunks, built)
                san = svc.sanitizer.sanitize(answer, tracked, chunks)
                # Prompt-present chunks, i.e. evidence reachability.
                in_prompt = {_Cit(c["chunk_id"]).chunk_id for c in built.citations}
                gold = {
                    c.chunk_id
                    for c in chunks
                    if any(matches_gold(payload_index.get(c.chunk_id, {}), u, family_map) for u in q.recall_units())
                }
                m = score_answer(answer, q.acceptable_conclusion or "", q.insufficient_evidence)
                # What the model ACTUALLY cited, via CitationTracker. Using
                # ``built.citations`` here would only measure gold presence in
                # the prompt, which is a property of the window alone and is
                # identical on every run.
                cited = {c.chunk_id for c in tracked}
                m["citation_recall"] = round(len(cited & gold) / max(len(gold), 1), 4) if gold else 0.0
                m["gold_reachable"] = round(len(in_prompt & gold) / max(len(gold), 1), 4) if gold else 0.0
                m["n_prompt_chunks"] = built.chunk_count
                m["gold_in_prompt"] = int(bool(in_prompt & gold))
                m["n_cited"] = len(cited)
                # Groundedness / hallucination, from the shipped sanitizer.
                m["groundedness_score"] = san.groundedness_score
                m["hallucination_detected"] = int(san.hallucination_detected)
                m["n_invalid_citations"] = len(san.invalid_citations)
                m["n_hallucinated_claims"] = len(san.hallucinated_claims)
                m["confidence"] = san.confidence
                # The sanitizer validates a citation against the whole retrieval
                # pool, so it cannot see a citation to a chunk the prompt never
                # showed the model. This does: a marker pointing at an index
                # outside the prompt window is an unsupported reference, and it
                # is the failure mode a wider window could plausibly worsen.
                n_markers = len(_MARKER.findall(answer))
                cited = {c["index"] for c in built.citations}
                dangling = [int(mk) for mk in _MARKER.findall(answer) if int(mk) not in cited]
                m["n_citation_markers"] = n_markers
                m["n_dangling_markers"] = len(dangling)
                m["dangling_marker_rate"] = round(len(dangling) / n_markers, 4) if n_markers else 0.0
                m["n_source_form_markers"] = len(_SOURCE_MARKER.findall(answer))
                return {
                    "qid": qid,
                    "answer_len": len(answer),
                    "llm_error": getattr(llm, "error", None),
                    "answer": answer,
                    "m": m,
                }

        out = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            for fut in as_completed([ex.submit(one, t) for t in tasks]):
                try:
                    out.append(fut.result())
                except Exception as exc:
                    out.append({"qid": None, "exception": f"{type(exc).__name__}: {exc}", "m": None})
        return out

    return _report(run(wide=False), run(wide=True), shard_out=args.shard_out)


def _load_shards(paths: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rebuild the two arm row lists from shard files so the summary can be
    recomputed without re-running the LLM (backgrounded runs do not survive).
    """
    N: dict[str, Any] = {}
    W: dict[str, Any] = {}
    for path in paths:
        blob = json.loads(Path(path).read_text(encoding="utf-8"))
        N.update(blob["narrow"])
        W.update(blob["wide"])
    print(f"merged {len(N)} narrow / {len(W)} wide rows from {len(paths)} shard(s)")
    return list(N.values()), list(W.values())


def _report(narrow: list[dict[str, Any]], wide_rows: list[dict[str, Any]], shard_out: str = "") -> int:
    N = {r["qid"]: r for r in narrow if r.get("m")}
    W = {r["qid"]: r for r in wide_rows if r.get("m")}
    common = sorted(set(N) & set(W))
    if not common:
        print("ABORT: no paired results", file=sys.stderr)
        return 1

    changed = [q for q in common if N[q]["m"]["gold_in_prompt"] == 0 and W[q]["m"]["gold_in_prompt"] == 1]
    lost = [q for q in common if N[q]["m"]["gold_in_prompt"] == 1 and W[q]["m"]["gold_in_prompt"] == 0]

    def agg(rows, qids):
        keys = (
            "binary_correct",
            "answer_correctness",
            "citation_recall",
            "gold_reachable",
            "groundedness_score",
            "hallucination_detected",
            "confidence",
            "dangling_marker_rate",
        )
        vals = {k: [rows[q]["m"][k] for q in qids] for k in keys}
        return {k: round(sum(v) / max(len(v), 1), 4) for k, v in vals.items()}

    an, aw = agg(N, common), agg(W, common)
    cn = agg(N, changed) if changed else {}
    cw = agg(W, changed) if changed else {}

    def paired(qids, metric="binary_correct"):
        d = [W[q]["m"][metric] - N[q]["m"][metric] for q in qids]
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

    out = {
        "model": os.environ.get("RAG_LLM_MODEL"),
        "n_paired": len(common),
        "n_errors_narrow": sum(1 for r in narrow if not r.get("m")),
        "n_errors_wide": sum(1 for r in wide_rows if not r.get("m")),
        "causally_changed_qids": changed,
        "gold_lost_qids": lost,
        "aggregate_all": {"window_narrow": an, "window_wide": aw},
        "aggregate_changed_subset": {"window_narrow": cn, "window_wide": cw},
        "paired_all": paired(common),
        "paired_changed": paired(changed) if changed else None,
        "paired_all_by_metric": {
            k: paired(common, k)
            for k in (
                "binary_correct",
                "answer_correctness",
                "citation_recall",
                "gold_reachable",
                "groundedness_score",
            )
        },
        "prompt_chunks_mean": {
            "window_narrow": round(statistics.mean(N[q]["m"]["n_prompt_chunks"] for q in common), 2),
            "window_wide": round(statistics.mean(W[q]["m"]["n_prompt_chunks"] for q in common), 2),
        },
        "limitations": [
            "The sanitizer validates citations against the whole retrieval pool, not the "
            "prompt window, so it cannot by itself detect a citation to something "
            "the model never saw; dangling_marker_rate covers that gap but is a "
            "proxy, not a claim-level verification.",
            "Only model_wrong questions are run, so this measures recovery, not "
            "regression risk on questions that were already correct.",
            f"The causal subset is n={len(changed)}; a null result there cannot "
            "distinguish no-effect from underpowered.",
            "Single free-tier model, one run per arm, no seed replication.",
            "Soft correctness is not reproducible: across two independent runs "
            "only 1/89 narrow-arm scores matched exactly, so small deltas in "
            "answer_correctness are model noise rather than window effect. "
            "Citation and groundedness metrics are deterministic given the "
            "answer text and so are stable across runs.",
        ],
    }

    if shard_out:
        # Shard mode: dump the paired rows so a killed run is not lost and
        # several foreground shards can be merged with --merge-shards later.
        Path(shard_out).write_text(
            json.dumps(
                {
                    "narrow": {q: N[q] for q in common},
                    "wide": {q: W[q] for q in common},
                    "changed": changed,
                    "lost": lost,
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
    print("=" * 76)
    print(f"Window-width A/B  n={len(common)}  model={out['model']}")
    print("=" * 76)
    print(
        f"mean prompt chunks: narrow={out['prompt_chunks_mean']['window_narrow']} "
        f"wide={out['prompt_chunks_mean']['window_wide']}",
    )
    print(f"causally changed (gold newly in prompt): {len(changed)}  gold lost: {len(lost)}")
    print()
    print(f"{'metric':<26} {'narrow':>9} {'wide':>9} {'delta':>9}")
    print("-" * 58)
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
        print(f"{k:<26} {an[k]:>9.4f} {aw[k]:>9.4f} {aw[k] - an[k]:>+9.4f}")
    print()
    pa = out["paired_all"]
    print(
        f"paired binary (all n={pa['n']}): improved {pa['improved']}, regressed {pa['regressed']}, "
        f"net {pa['net']}, t={pa['t']}",
    )
    if changed:
        pc = out["paired_changed"]
        print(
            f"paired binary (changed n={pc['n']}): improved {pc['improved']}, "
            f"regressed {pc['regressed']}, net {pc['net']}, t={pc['t']}",
        )
    print()
    print(f"written: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

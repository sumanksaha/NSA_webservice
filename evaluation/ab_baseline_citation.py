"""SPEC-1 A/B: does the quote-first prompt make the BASELINE arm cite?

Why this exists
---------------
The Stage-2 recovered-only population cannot measure baseline citation
recall.  Stage-1 defines ``recovered = retry_hit AND NOT baseline_hit``, so
every recovered qid has *no gold chunk in the baseline window*.  And

    citation_recall = |cited & gold| / max(|gold|, 1)

is undefined-to-zero when the baseline pool has no gold at all.  On that
population baseline ``citation_recall`` is structurally 0.00 no matter how
well the model cites, so SPEC-1's "baseline citation_recall >= 0.50 on the
20 paired qids" gate is unachievable by construction.

The population that *can* measure it is the general slice: on all cached
questions the baseline top-10 window contains gold ~59% of the time
(Stage-1 ``gold_in_pool.baseline``).  This script selects exactly those
qids — where the baseline window has gold — and asks the real generation
path whether the model quotes and cites it.

Population: qids where the baseline top-10 window contains >= 1 gold chunk
(default 60, i.e. >= 50 per SPEC-1).

Usage:
    python -m evaluation.ab_baseline_citation --variant new --limit 60
    python -m evaluation.ab_baseline_citation --variant legacy --limit 60
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

import evaluation.llm_ssl_client as _llm_ssl

_llm_ssl.MAX_ATTEMPTS = max(1, int(os.environ.get("RAG_LLM_MAX_ATTEMPTS", "3") or 3))

from evaluation.bench_p0_1_packing import RAW_DIR, _chunks_for, _load_jsonl, _load_payload_index
from evaluation.benchmark import load_questions
from evaluation.llm_ssl_client import SSLBypassLLMClient
from evaluation.resolution import FamilyMap

ARM = "C_hybrid"
BASELINE_K = 10
MAX_WORKERS = 3
OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"

#: The pre-SPEC-1 prompt, verbatim, so the citation-lift delta is measurable.
LEGACY_SYSTEM_PROMPT = (
    "You are a legal assistant specialised in the Food Safety and Standards "
    "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
    "Work in two steps. Step 1 — quote: first extract the short passages "
    "that bear on the question, quoting them verbatim. Step 2 — answer: "
    "give a concise, legally precise answer derived strictly from those quotes. "
    "Citation contract: (1) every material legal claim carries at least one "
    "[n] citation; (2) each [n] maps to a source shown in the context "
    "(e.g. [1], [2]) — never cite a source that is not shown; "
    "(3) never fabricate facts. If the answer is not in the context, state "
    "so clearly and qualify what is unknown instead of guessing."
)

LEGACY_USER_TEMPLATE = (
    "Relevant legal context:\n"
    "<legal_context>\n"
    "{context}\n"
    "</legal_context>\n\n"
    "First quote the passages that bear on the question, then answer "
    "the question using those quotes, citing specific sources with "
    "[n] markers.\n\n"
    "Question: {query}\n"
    "Answer:"
)

#: The reverted SPEC-1 wording, kept so the regression stays reproducible.
SPEC1_REVERTED_USER_TEMPLATE = (
    "Relevant legal context:\n"
    "<legal_context>\n"
    "{context}\n"
    "</legal_context>\n\n"
    "You MUST follow these steps exactly:\n"
    "1. Quote at least one verbatim passage from the <legal_context> that bears "
    "on the question. Copy the text exactly as it appears — do not paraphrase. "
    "If the context is thin, quote whatever is relevant, even if short. Only if "
    "you truly find ZERO relevant passages may you skip the quote step.\n"
    "2. Answer the question using the quoted passage(s), citing each source "
    "with [n] markers (e.g. [1], [2]). Every material claim must carry a "
    "citation to a shown source.\n"
    "3. If you cannot answer from the context even after quoting, say "
    '"I cannot find the answer in the provided context" — but only when you '
    "produced zero quotes in step 1.\n\n"
    "Question: {query}\n"
    "Answer:"
)

#: SPEC-1 candidates.  Each is an ADDITIVE citation instruction layered on the
#: legacy template.  None gate on quoting and none grant an abstention escape
#: hatch — those two clauses are what drove the reverted SPEC-1's abstention
#: blowup (0.073 -> 0.436 false abstention here), so they are deliberately
#: absent from every candidate.
#:
#: ``citation_recall`` is the binding constraint (precision has headroom:
#: 0.659 against a 0.25 target), and per-answer recall is
#: ``|cited & gold| / |gold|`` — so the lever is citation *breadth*: cite every
#: relied-upon source, not just the first one that comes to mind.
_CITE_BREADTH = (
    "Cite every passage you rely on, not only the first one: when your answer "
    "draws on more than one source, attach each one's label."
)

_CANDIDATES: dict[str, str] = {
    # Minimal additive pressure: per-claim citation, name the label shown.
    "cand_cite_only": (
        "Relevant legal context:\n"
        "<legal_context>\n"
        "{context}\n"
        "</legal_context>\n\n"
        "First quote the passages that bear on the question, then answer "
        "the question using those quotes, citing specific sources with "
        "[n] markers.\n"
        "Attach the source label shown in the context (for example [1] or "
        "[Source 1]) to every material claim you make.\n\n"
        "Question: {query}\n"
        "Answer:"
    ),
    # Breadth + section naming. ``Section <n>`` is a citation form the
    # CitationTracker already recognises, so naming the provision both sharpens
    # legal precision and adds a resolvable citation.
    "cand_cite_breadth": (
        "Relevant legal context:\n"
        "<legal_context>\n"
        "{context}\n"
        "</legal_context>\n\n"
        "First quote the passages that bear on the question, then answer "
        "the question using those quotes, citing specific sources with "
        "[n] markers.\n"
        "Name the section or rule you rely on and put its source label at the "
        "end of the same sentence (for example: Section 31(2) [3]).\n"
        + _CITE_BREADTH
        + "\n\n"
        "Question: {query}\n"
        "Answer:"
    ),
    # "Cite what you used" does not raise the distinct-source count: measured
    # n_citations barely moved (3.93 -> 4.07). Under-citing is a *scan* problem
    # — the model answers from the most obvious passage and never reconsiders
    # the rest of the window. This candidate makes the scan explicit.
    "cand_scan": (
        "Relevant legal context:\n"
        "<legal_context>\n"
        "{context}\n"
        "</legal_context>\n\n"
        "First quote the passages that bear on the question, then answer "
        "the question using those quotes, citing specific sources with "
        "[n] markers.\n"
        "Before answering, go through every numbered source in the context and "
        "note the ones that bear on the question, including any that add a "
        "condition, exception, threshold or definition the answer depends on.\n\n"
        "Question: {query}\n"
        "Answer:"
    ),
    # Same scan, plus a trailing enumeration. A structured closing line is a
    # format the model fills far more reliably than an inline instruction, so
    # this is the higher-ceiling variant of the breadth idea.
    "cand_scan_enumerate": (
        "Relevant legal context:\n"
        "<legal_context>\n"
        "{context}\n"
        "</legal_context>\n\n"
        "First quote the passages that bear on the question, then answer "
        "the question using those quotes, citing specific sources with "
        "[n] markers.\n"
        "Before answering, go through every numbered source in the context and "
        "note the ones that bear on the question, including any that add a "
        "condition, exception, threshold or definition the answer depends on.\n"
        "Finish with a line of the form 'Sources: [a], [b], [c]' listing every "
        "source you relied on.\n\n"
        "Question: {query}\n"
        "Answer:"
    ),
    # The enumerate variant bought recall with a 14.7% padding rate (answers
    # citing nearly the whole window). This keeps the scan but asks for the
    # sources that actually bear on the question, with an explicit selectivity
    # cue, so breadth rises without degenerate "cite everything" behaviour.
    "cand_scan_capped": (
        "Relevant legal context:\n"
        "<legal_context>\n"
        "{context}\n"
        "</legal_context>\n\n"
        "First quote the passages that bear on the question, then answer "
        "the question using those quotes, citing specific sources with "
        "[n] markers.\n"
        "Before answering, go through every numbered source in the context and "
        "note the ones that bear on the question, including any that add a "
        "condition, exception, threshold or definition the answer depends on.\n"
        "Cite the sources that bear on the question and leave out those that do "
        "not, even when they are on related legal points.\n\n"
        "Question: {query}\n"
        "Answer:"
    ),
}

#: All variants this harness can render: name -> (system, user_template).
VARIANTS: dict[str, tuple[str, str]] = {
    "legacy": (LEGACY_SYSTEM_PROMPT, LEGACY_USER_TEMPLATE),
    "spec1_reverted": (LEGACY_SYSTEM_PROMPT, SPEC1_REVERTED_USER_TEMPLATE),
    **{name: (LEGACY_SYSTEM_PROMPT, tpl) for name, tpl in _CANDIDATES.items()},
}

#: Abstention phrasing SPEC-1 forbids when quote-able text is in the window.
_ABSTAIN_RE = re.compile(
    r"cannot find the answer|not (?:in|within) the (?:provided )?context|"
    r"does not contain|no (?:relevant )?(?:information|provision|text)",
    re.IGNORECASE,
)
_MARKER_RE = re.compile(r"\[(\d+)\]")


def _select_population(payload_index: dict, questions: dict, recs: dict, limit: int) -> list[tuple[str, str, list, set]]:
    """Qids whose BASELINE window already contains gold (the measurable slice)."""
    from evaluation.resolution import matches_gold as _mg

    picked: list[tuple[str, str, list, set]] = []
    for qid in sorted(recs):
        if qid not in questions:
            continue
        q = questions[qid]
        chunks = _chunks_for([str(c) for c in (recs[qid].get("chunk_ids") or [])], payload_index)
        if len(chunks) < 2:
            continue
        units = q.recall_units()
        if not units:
            continue
        family_map = FamilyMap()
        pool = chunks[:BASELINE_K]
        gold = {
            c.chunk_id
            for c in pool
            if any(_mg(payload_index.get(c.chunk_id, {}), u, family_map) for u in units)
        }
        if not gold:
            continue  # citation_recall undefined for this qid
        picked.append((qid, recs[qid].get("query") or q.question, pool, gold))
        if len(picked) >= limit:
            break
    return picked


def _run_one(task, payload_index, questions, template: str | None) -> dict[str, Any]:
    from app.rag.generation.grounded_service import GroundedGenerationService

    qid, qtext, pool, gold = task
    svc = GroundedGenerationService()
    svc.llm_client = SSLBypassLLMClient(model=os.environ.get("RAG_LLM_MODEL", ""))
    if template is not None:
        # Faithful variant render: identical assembled context (built.context),
        # only the user-template wording differs.
        def _render(query: str, built: Any) -> tuple[str, str]:
            return LEGACY_SYSTEM_PROMPT, template.format(context=built.context, query=query)

        svc._render_prompt = _render  # type: ignore[method-assign]

    qt = (questions[qid].question_types or ["general"])[0] if getattr(questions[qid], "question_types", None) else "general"
    try:
        built = svc.context_builder.build(qtext, pool, query_type=qt)
        sys_p, user_p = svc._render_prompt(qtext, built)
        llm = svc._call_llm(sys_p, user_p)
        answer = getattr(llm, "text", "") or ""
        tracked = svc._extract_citations(llm, pool, built)
        san = svc.sanitizer.sanitize(answer, tracked, pool)
        cited = {c.chunk_id for c in tracked}
        abstained = bool(_ABSTAIN_RE.search(answer))
        distinct_cited = len(cited)
        return {
            "qid": qid,
            "answer": answer,
            "llm_error": getattr(llm, "error", None),
            "m": {
                "citation_recall": round(len(cited & gold) / max(len(gold), 1), 4),
                "citation_precision": round(len(cited & gold) / max(len(cited), 1), 4) if cited else 0.0,
                "gold_in_prompt": 1,
                "n_gold": len(gold),
                "n_pool": len(pool),
                # Breadth levers. distinct_cited is the set-size that actually
                # drives recall; n_citations counts repeats.
                "distinct_cited": distinct_cited,
                "breadth": round(distinct_cited / max(len(pool), 1), 4),
                "gold_cited": len(cited & gold),
                # A candidate that pads with every source scores recall ~1.0 but
                # precision ~n_gold/n_pool. This flag catches that failure mode
                # so a recall "win" bought by indiscriminate citing is visible.
                "is_padded": int(distinct_cited >= max(len(pool) - 1, 3)),
                # Measured the way the scorer sees it: CitationTracker accepts
                # [n], [Source n] and bare "Section n", so a bare [n] regex
                # badly undercounts.
                "n_citations": len(tracked),
                "has_citation": int(bool(cited)),
                "has_marker": int(bool(_MARKER_RE.search(answer))),
                "abstained": int(abstained),
                # SPEC-1: abstaining while quote-able gold is in the window.
                "false_abstention": int(abstained and len(gold) > 0),
                "groundedness_score": san.groundedness_score,
                "hallucination_detected": int(san.hallucination_detected),
            },
        }
    except Exception as exc:
        return {"qid": qid, "exception": f"{type(exc).__name__}: {exc}", "m": None}


KEYS = (
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--variant", default="legacy", choices=sorted(VARIANTS) + ["shipped"])
    ap.add_argument("--qids", default="")
    ap.add_argument("--tag", default="", help="suffix the output file, for independent replicates")
    args = ap.parse_args()

    # "shipped" renders whatever prompt_template.py currently holds (no override);
    # every other name renders the frozen control or a named candidate.
    template = None if args.variant == "shipped" else VARIANTS[args.variant][1]

    print("LOAD questions", flush=True)
    questions = {q.question_id: q for q in load_questions()}
    payload_index = _load_payload_index()
    recs = {r["question_id"]: r for r in _load_jsonl(RAW_DIR / f"{ARM}.jsonl")}
    print(f"load done questions={len(questions)} recs={len(recs)}", flush=True)

    tasks = _select_population(payload_index, questions, recs, args.limit)
    if args.qids:
        want = {q.strip() for q in args.qids.split(",") if q.strip()}
        tasks = [t for t in tasks if t[0] in want]
    if not tasks:
        print("no baseline-gold qids found", file=sys.stderr)
        return 1
    print(f"population n={len(tasks)} (baseline window contains gold) variant={args.variant}", flush=True)

    gate = Semaphore(MAX_WORKERS)
    results: list[dict] = []

    def one(t):
        with gate:
            return _run_one(t, payload_index, questions, template)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for fut in as_completed([ex.submit(one, t) for t in tasks]):
            try:
                results.append(fut.result())
            except Exception as exc:
                results.append({"qid": None, "exception": str(exc), "m": None})

    good = [r for r in results if r.get("m") and (r.get("answer") or "").strip()]
    if not good:
        print("no successful generations (rate limited?)", file=sys.stderr)
        return 1

    agg = {k: round(statistics.mean(r["m"][k] for r in good), 4) for k in KEYS}
    # Per-qid citation_recall, the SPEC-1 gate metric (>= 0.50).
    mean_recall = agg["citation_recall"]
    recall_ge_half = sum(1 for r in good if r["m"]["citation_recall"] >= 0.5) / max(len(good), 1)
    out = {
        "benchmark": "baseline_citation_enforcement",
        "variant": args.variant,
        "population": "qids whose baseline top-10 window contains >=1 gold chunk",
        "n_requested": len(tasks),
        "n_ok": len(good),
        "aggregate": agg,
        "citation_recall_ge_0.5_share": round(recall_ge_half, 4),
        "per_question": {r["qid"]: r["m"] for r in good},
        "limitations": [
            "Live single-model run on the free tier; partial rate-limit losses skew coverage.",
            "citation_recall is only defined where the baseline window holds gold, so this "
            "slice deliberately excludes recovered qids (see module docstring).",
        ],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / f"baseline_citation_{args.variant}{('_' + args.tag) if args.tag else ''}.json"
    out_file.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 72)
    print(f"Baseline citation A/B  variant={args.variant}  n_ok={len(good)}/{len(tasks)}")
    print("=" * 72)
    for k in KEYS:
        print(f"{k:<24} {agg[k]:>9.4f}")
    print(f"{'citation_recall>=0.5 share':<24} {recall_ge_half:>9.4f}")
    print(f"written: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

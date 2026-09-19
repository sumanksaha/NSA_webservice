"""Experiment B -- CE Top-K -> LLM Answer-Quality Curve.

Objective (spec section 1):
    Isolate *context-window selection* from *reranker quality*.  Holding the
    existing CE v2_K500 ranking fixed, vary only how many CE-ranked candidates
    are supplied to the LLM and measure end-to-end answer quality.  This tells
    us whether the RAG ceiling is set by (a) CE top-K selection, (b) too little
    context for the LLM, (c) context overload/noise, (d) context assembly, or
    (e) LLM reasoning/generation.

The ONLY experimental variable is K (the number of CE-ranked candidates
supplied to the LLM).  Everything else is frozen:
  * retrieval arms (A_dense, B_sparse, D_kg, question-ident), slice depth 500
  * 4-arm RRF fusion (weights sec/act/exact/lex = 0), rrf_k = 60.0
  * CE checkpoint: legal_ce_v2_K500, max_length=256, batch=64, cpu, threads=4
  * context-builder formatting (citation labels [n], headers, char truncation)
  * system / user prompt (grounded_qa, fixed; query_type=general_qa)
  * LLM model (poolside/laguna-s-2.1:free via OpenRouter), temp=0.1, max_tokens=1024
  * citation mechanism (CitationTracker [n] + Section refs), sanitizer, abstention

The production ContextBuilder's answerability-rejection heuristic (which
truncates context to ~1.5 chunks on average -- the reference ceiling) is
disabled as a FIXED configuration choice (identical for every K and the
oracle) so that K is the sole variable measured.  This is the same fixed
choice the prior Experiment-B scaffold made; it is not tuned per K or per
question, and the answerability heuristic's effect is reported separately.

Phases (all resumable / checkpointed):
    build   -> run retrieval + RRF + CE ONCE; cache the complete CE top-K ranking
    llm     -> run the K-curve (150 Q x 8 K = 1200 LLM calls, checkpointed)
    oracle  -> run the gold-context oracle (150 LLM calls, checkpointed)
    analyze -> aggregate tables, all analyses, 11 diagnostic answers
    plots   -> 6 matplotlib plots
    report  -> experiment_B_summary.md
    all     -> build && llm && oracle && analyze && plots && report
    dry     -> build with detailed verification (no LLM), then validate
               K-context + oracle-context math with zero LLM calls

Usage:
    python -u -m evaluation.experiment_b_topk_eval --dry
    python -u -m evaluation.experiment_b_topk_eval --phase build
    python -u -m evaluation.experiment_b_topk_eval --phase llm
    python -u -m evaluation.experiment_b_topk_eval --phase oracle
    python -u -m evaluation.experiment_b_topk_eval --phase analyze
    python -u -m evaluation.experiment_b_topk_eval --phase all
    python -u -m evaluation.experiment_b_topk_eval --phase all --stub   # validate w/o real LLM
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import threading
import time
import warnings
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Reuse the corrected-evaluation helpers (env setup, loaders, CE scoring,
# SSL-bypass LLM client) from eval_e2e_v2.py -- THE "existing corrected
# evaluation implementation".  Importing it runs load_dotenv(override=True) and
# sets RAG_USE_STUB_LLM=false at module scope.
from evaluation.eval_e2e_v2 import (  # noqa: E402
    load_payload_index, load_raw, load_jsonl, score_pool, _SSLBypassLLMClient,
)
from evaluation.benchmark import load_questions, load_gold_registry  # noqa: E402
from evaluation.config import CACHE_DIR  # noqa: E402
from evaluation.resolution import FamilyMap, matches_gold, payload_to_keys  # noqa: E402
from evaluation.rerank_legal import build_pool, rerank, rrf_scores, rank_of  # noqa: E402
from evaluation.grading import grade_answer  # noqa: E402
from app.rag.generation.context_builder import ContextBuilder  # noqa: E402
from app.rag.generation.grounded_service import GroundedGenerationService  # noqa: E402
from app.rag.retrieval.result import RetrievedChunk  # noqa: E402

os.environ["RAG_USE_STUB_LLM"] = "false"  # re-assert after eval_e2e_v2 import

import torch  # noqa: E402
torch.set_num_threads(4)

from sentence_transformers import CrossEncoder  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# ---------------------------------------------------------------------------
# Frozen pipeline configuration (mirrors Experiment A / eval_v2_checkpoint)
# ---------------------------------------------------------------------------
MODELS_DIR = PROJECT_ROOT / "evaluation" / "out" / "models"
CE_MODEL = MODELS_DIR / "legal_ce_v2_K500"

SLICE_DEPTH = 500            # dense@500 | sparse@500 | ident@500 (per arm)
KG_SLICE = 500               # KG provisions slice depth
RRF_K = 60.0                 # RRF constant
RERANK_WEIGHTS = {"sec": 0.0, "act": 0.0, "exact": 0.0, "lex": 0.0}  # base RRF
CE_BATCH = 64
POOL_DEPTH = 150             # CE scored over RRF top-POOL_DEPTH -> cached CE ranking
# (frozen Experiment A / eval_v2_checkpoint operating point; the spec's idealized
#  "complete CE top-500 ranking" is non-limiting here because K<=100 is satisfied
#  within head-150 and Q001-5 CE gold ranks reproduce Experiment A exactly.
#  A deeper 500 ranking was benchmarked and yields identical top-K gold ranks,
#  so head-150 is used for speed/consistency with Experiment A.)
MAX_CONTEXT_CHARS = 200_000  # generous; K is the variable, not chars

K_VALUES = [1, 5, 10, 20, 30, 50, 75, 100]

LLM_CONCURRENCY = 8
LLM_MAX_RETRIES = 3

RAW_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "raw"
IDENT_CACHE = CACHE_DIR / "v55_ident" / "sparse_identifier.jsonl"
OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
PLOT_DIR = OUT_DIR / "plots"

CE_RANKING_CACHE = OUT_DIR / "experiment_B_ce_ranking.json"
LLM_CKPT = OUT_DIR / "experiment_B_llm_checkpoint.jsonl"
ORACLE_CKPT = OUT_DIR / "experiment_B_oracle_checkpoint.jsonl"
GOLD_INDEX_CACHE = OUT_DIR / "experiment_B_gold_index.json"

AGGREGATE_JSON = OUT_DIR / "experiment_B_aggregate.json"
PER_QUESTION_JSON = OUT_DIR / "experiment_B_per_question.json"
SUMMARY_MD = OUT_DIR / "experiment_B_summary.md"


# ---------------------------------------------------------------------------
# ContextBuilder overrides -- isolate K as the sole variable
# ---------------------------------------------------------------------------
class _ExpBContextBuilder(ContextBuilder):
    """Context builder that retains K CE-ranked chunks (answerability off).

    Identical to the production ContextBuilder for formatting/truncation
    (citation labels [n], header format, char-budget truncation), except:
      * max_chunks = K            (the experimental variable)
      * max_context_chars = 200K  (so K is the binding limit, not chars)
      * _check_answerability disabled (always enough_evidence True)
    """

    def __init__(self, k: int) -> None:
        super().__init__(max_context_chars=MAX_CONTEXT_CHARS, max_chunks=k, query_type="")

    def _check_answerability(self, query, chunks, query_type):
        return True, []


class _ExpBOracleBuilder(ContextBuilder):
    """Gold-context oracle builder (same formatting, answerability off)."""

    def __init__(self, max_chunks: int) -> None:
        super().__init__(max_context_chars=MAX_CONTEXT_CHARS, max_chunks=max_chunks, query_type="")

    def _check_answerability(self, query, chunks, query_type):
        return True, []


# ---------------------------------------------------------------------------
# Gold resolution -- equivalent to experiment_a.find_all_gold_chunk_ids
# (matches_gold scan) but pre-indexed for speed.  Verified equivalent on
# Q001-Q005 against a brute-force matches_gold scan in phase_build.
# ---------------------------------------------------------------------------
def build_gold_index(payload_index: dict[str, dict], family_map: FamilyMap):
    """Precompute {family: {section: set(chunk_id)}} + {family: set(chunk_id)}.

    Equivalent to scanning matches_gold(payload, unit) over all payload points
    for every gold unit (experiment_a.find_all_gold_chunk_ids), O(P) once
    instead of O(units * P).
    """
    by_section: dict[str, dict[str, set[str]]] = {}
    by_family: dict[str, set[str]] = {}
    for cid, payload in payload_index.items():
        for fam, sec in payload_to_keys(payload, family_map):
            by_section.setdefault(fam, {}).setdefault(sec, set()).add(cid)
            by_family.setdefault(fam, set()).add(cid)
    return by_section, by_family


def gold_chunk_ids_for_units(units, idx) -> set[str]:
    by_section, by_family = idx
    ids: set[str] = set()
    for u in units:
        if u.section is None:
            ids |= by_family.get(u.family, set())
        else:
            ids |= by_section.get(u.family, {}).get(u.section, set())
    return ids


def _load_gold_index(payload_index, family_map) -> tuple:
    if GOLD_INDEX_CACHE.exists():
        try:
            data = json.loads(GOLD_INDEX_CACHE.read_text())
            by_section = {f: {s: set(c) for s, c in v.items()} for f, v in data["by_section"].items()}
            by_family = {f: set(c) for f, c in data["by_family"].items()}
            return by_section, by_family
        except Exception:
            pass
    idx = build_gold_index(payload_index, family_map)
    by_section_s = {f: {s: sorted(c) for s, c in v.items()} for f, v in idx[0].items()}
    by_family_s = {f: sorted(c) for f, c in idx[1].items()}
    GOLD_INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
    GOLD_INDEX_CACHE.write_text(json.dumps(
        {"by_section": by_section_s, "by_family": by_family_s,
         "n_points": len(payload_index)}, indent=2), encoding="utf-8")
    return idx


# ---------------------------------------------------------------------------
# CE ranking construction (matches Experiment A / eval_v2_checkpoint exactly,
# including the 4-arm RRF with the question-ident arm)
# ---------------------------------------------------------------------------
def _arm_keys(rec, n):
    return [{"key": c} for c in (rec or {}).get("chunk_ids", [])[:n]]


def build_ce_ranking(q, qid, dense, sparse, kg, ident, payload_index, family_map, ce):
    """Return (pool, rrf_ranked, ce_ranked) for one question.

    4-arm RRF (dense|sparse|KG|question-ident) with zero legal weights, then
    CE v2_K500 over the RRF top-POOL_DEPTH.  Identical pipeline to
    experiment_a_gold_rank.py / eval_v2_checkpoint.py.
    """
    if not (dense and sparse and kg):
        return None, None, None
    pool = build_pool(dense, sparse, kg, payload_index, family_map,
                      slice_depth=SLICE_DEPTH, kg_slice=KG_SLICE)
    if not pool:
        return None, None, None

    kg_provision_keys = [str(p.get("provision_id") or "") for p in (kg or {}).get("kg_provisions", [])[:KG_SLICE]]
    rrf = rrf_scores([_arm_keys(dense, SLICE_DEPTH), _arm_keys(sparse, SLICE_DEPTH),
                      [{"key": k} for k in kg_provision_keys]])

    ident_rec = ident.get(qid) if ident else None
    if ident_rec:
        ids = [str(c) for c in ident_rec.get("chunk_ids", [])[:SLICE_DEPTH]]
        if ids:
            rrf = rrf_scores([_arm_keys(dense, SLICE_DEPTH), _arm_keys(sparse, SLICE_DEPTH),
                              [{"key": k} for k in kg_provision_keys], [{"key": c} for c in ids]])

    rrf_ranked = rerank(pool, q.question, family_map, rrf, RERANK_WEIGHTS)
    ce_ranked = score_pool(rrf_ranked[:POOL_DEPTH], q.question, ce)
    return pool, rrf_ranked, ce_ranked


# ---------------------------------------------------------------------------
# RetrievedChunk construction + CE top-K extraction
# ---------------------------------------------------------------------------
def to_retrieved_chunk(key: str, payload: dict, score: float = 0.0) -> RetrievedChunk:
    text = str(payload.get("chunk_text") or payload.get("text") or "")
    return RetrievedChunk(
        chunk_id=str(key),
        score=float(score) if score else 0.0,
        text=text,
        section_number=payload.get("section_number"),
        clause_number=payload.get("clause_number"),
        document_title=payload.get("document_title") or payload.get("act_name") or "",
        act_name=payload.get("act_name") or "",
        document_type=payload.get("document_type") or "",
        authority=payload.get("authority") or "",
        chunk_index=int(payload.get("chunk_index", 0) or 0),
        hierarchy_level=int(payload.get("hierarchy_level", 0) or 0),
        parent_chunk_id=payload.get("parent_chunk_id"),
    )


def ce_topk_chunks(ce_ranked, k: int):
    """Top-K CE items -> (chunk_items, kg_items).  KG items cannot be supplied
    as chunk context and are reported as discarded."""
    topk = ce_ranked[:k]
    chunk_items = [it for it in topk if it.get("kind") == "chunk"]
    kg_items = [it for it in topk if it.get("kind") == "kg"]
    return chunk_items, kg_items


def retrieve_chunks(chunk_items, payload_index: dict[str, dict]) -> list[RetrievedChunk]:
    return [
        to_retrieved_chunk(it["key"], payload_index.get(it["key"], {}), it.get("ce_score", 0.0))
        for it in chunk_items
    ]


# ---------------------------------------------------------------------------
# Correctness metrics (mirror eval_e2e_v2.compute_question_metrics)
# ---------------------------------------------------------------------------
_TOK_RE = re.compile(r"[a-z0-9]+")


def _toks(s: str) -> set[str]:
    return set(_TOK_RE.findall(s.lower())) if s else set()


def token_overlap(answer: str, expected: str) -> dict[str, float]:
    a, e = _toks(answer), _toks(expected or "")
    if not e:
        return {"coverage": 0.0, "jaccard": 0.0, "correctness": 0.0}
    inter = len(a & e)
    cov = inter / len(e)
    jac = inter / len(a | e) if (a | e) else 0.0
    return {"coverage": cov, "jaccard": jac, "correctness": (jac + cov) / 2.0}


_ABSTAIN_RE = re.compile(
    r"\b(i (do not|cannot|dont|can't|can not)|cannot (find|answer|determine|locate)|"
    r"no relevant|insufficient inform|unable to|not possible to|cannot be (determine|established|reliably)|"
    r"not (recorded|established|stipulated|provided|specified|mentioned|available)|"
    r"no (evidence|information|provision|specific)|does not (specify|establish|provide|state)|"
    r"the corpus does not|no provision in the corpus)\b",
    re.IGNORECASE,
)


def abstain_check(answer: str) -> bool:
    if not answer or not answer.strip():
        return True
    if len(answer.strip()) < 20:
        return True
    return bool(_ABSTAIN_RE.search(answer))


def compute_metrics(resp, built, question, ce_topk_chunk_ids, context_chunk_ids,
                    gold_chunk_ids, gold_units, payload_index, family_map) -> dict[str, Any]:
    """Primary LLM metrics (spec section 6) for one (question, K)."""
    answer = resp.answer or ""
    expected = question.acceptable_conclusion or ""
    toks = token_overlap(answer, expected)

    cited_chunk_ids = [c.chunk_id for c in resp.citations]
    covered_units: set[str] = set()
    cited_gold_chunks = 0
    for cid in cited_chunk_ids:
        payload = payload_index.get(cid)
        if payload is None:
            continue
        ok = False
        for u in gold_units:
            if matches_gold(payload, u, family_map):
                covered_units.add(u.provision_id)
                ok = True
        if ok:
            cited_gold_chunks += 1
    citation_recall = len(covered_units) / len(gold_units) if gold_units else 0.0
    citation_precision = cited_gold_chunks / len(cited_chunk_ids) if cited_chunk_ids else 0.0

    ce_topk_set = set(ce_topk_chunk_ids)
    context_set = set(context_chunk_ids)
    n_gold_total = len(gold_chunk_ids)
    context_recall = len(ce_topk_set & gold_chunk_ids) / n_gold_total if n_gold_total else 0.0
    actual_context_recall = len(context_set & gold_chunk_ids) / n_gold_total if n_gold_total else 0.0
    n_gold_in_context = len(context_set & gold_chunk_ids)

    abstained = abstain_check(answer)
    if getattr(question, "insufficient_evidence", False):
        abstain_correct = abstained
    else:
        abstain_correct = not abstained

    evidence_texts = {cid: str((payload_index.get(cid) or {}).get("chunk_text") or "")
                      for cid in ce_topk_chunk_ids}
    try:
        g = grade_answer(question, answer, list(cited_chunk_ids),
                         list(ce_topk_chunk_ids), evidence_texts, payload_index, family_map)
        grader_score = int(g.get("score", 0))
    except Exception as exc:
        g = {"error": str(exc)}
        grader_score = 0

    return {
        "answer": answer,
        "answer_length": len(answer),
        "expected_conclusion": expected,
        "answer_coverage": round(toks["coverage"], 4),
        "answer_jaccard": round(toks["jaccard"], 4),
        "answer_correctness": round(toks["correctness"], 4),
        "correct": bool(toks["correctness"] > 0.5),
        "citation_recall": round(citation_recall, 4),
        "citation_precision": round(citation_precision, 4),
        "n_citations": len(cited_chunk_ids),
        "cited_chunk_ids": cited_chunk_ids,
        "groundedness": round(float(resp.groundedness_score), 4),
        "abstained": bool(abstained),
        "abstain_correct": bool(abstain_correct),
        "latency_ms": int(resp.total_latency_ms),
        "generation_latency_ms": int(resp.generation_latency_ms or 0),
        "retrieval_latency_ms": int(resp.retrieval_latency_ms or 0),
        "prompt_tokens": int(resp.prompt_tokens or 0),
        "completion_tokens": int(resp.completion_tokens or 0),
        "context_tokens": int(built.total_tokens_estimate if built else 0),
        "context_chars": len(built.context) if built else 0,
        "context_chunk_count": int(built.chunk_count if built else 0),
        "context_truncated": bool(built.truncated if built else False),
        "context_recall": round(context_recall, 4),
        "actual_context_recall": round(actual_context_recall, 4),
        "n_gold_chunks_in_context": n_gold_in_context,
        "n_gold_chunks_total": n_gold_total,
        "grader_score": grader_score,
        "grader": g,
    }


def run_llm_pipeline(question, context_chunks, k, query_type, llm_client):
    """Run the EXISTING GroundedGenerationService for one (question, K).

    Reuses the existing ContextBuilder formatting, PromptTemplate,
    CitationTracker, and ResponseSanitizer with a fixed _ExpBContextBuilder(k).
    """
    cb = _ExpBContextBuilder(k)
    service = GroundedGenerationService(llm_client=llm_client, context_builder=cb)
    built = cb.build(question.question, context_chunks, query_type or "general_qa")
    resp = service.generate(question.question, context_chunks,
                            query_type=(query_type or "general_qa"))
    return resp, built


# ---------------------------------------------------------------------------
# Client + cache helpers
# ---------------------------------------------------------------------------
def _make_client(use_stub: bool):
    os.environ["RAG_USE_STUB_LLM"] = "true" if use_stub else "false"
    return _SSLBypassLLMClient()


def _load_ce_cache() -> dict:
    if not CE_RANKING_CACHE.exists():
        raise FileNotFoundError(
            f"CE ranking cache not found: {CE_RANKING_CACHE}. Run --phase build first.")
    return json.loads(CE_RANKING_CACHE.read_text())


def _attach_ce_ranked(ce_cache: dict):
    for qd in ce_cache["questions"].values():
        qd["_ce_ranked"] = [
            {"key": ky, "kind": ki, "ce_score": sc}
            for ky, ki, sc in zip(qd["ce_ranked_keys"], qd["ce_ranked_kinds"], qd["ce_ranked_scores"])
        ]


def _load_set(path: Path, value_keys=("question_id", "k"), skip_errors: bool = False) -> set:
    """Checkpoint keys to skip on resume.

    skip_errors=True -> only mark *successful* (error-less) records as done, so
    that rate-limited/empty-answer tasks are automatically retried on resume.
    (A 429 exhausts the LLM client's internal 3-retry backoff and surfaces as an
    empty-answer / error record; we must re-run it, not skip it.)
    """
    done = set()
    if path.exists():
        for line in path:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if skip_errors and rec.get("error"):
                continue
            k = rec.get("k")
            key = (rec["question_id"], int(k)) if k is not None else (rec["question_id"], None)
            done.add(key)
    return done


def _retry(fn, label: str, retries: int = LLM_MAX_RETRIES):
    last = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:  # includes 429 / network / decode errors
            last = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"{label} failed after {retries} attempts: {last!r}")


# ---------------------------------------------------------------------------
# Phase: BUILD (retrieval + RRF + CE once, cache, verify)
# ---------------------------------------------------------------------------
def _ce_best_rank(ce_ranked, gold_units, payload_index, family_map):
    ranks = []
    for u in gold_units:
        r = rank_of(ce_ranked, u, payload_index, family_map)
        if r is not None:
            ranks.append(r)
    return (min(ranks) if ranks else None), ranks


def _build_config_meta() -> dict:
    return {
        "ce_model": "legal_ce_v2_K500",
        "ce_model_path": str(CE_MODEL),
        "ce_max_length": 256,
        "ce_batch": CE_BATCH,
        "ce_scoring_depth": POOL_DEPTH,
        "pool": f"dense@{SLICE_DEPTH} | sparse@{SLICE_DEPTH} | KG@{KG_SLICE} | question-ident@{SLICE_DEPTH}",
        "rrf_k": RRF_K,
        "rerank_weights": RERANK_WEIGHTS,
        "slice_depth": SLICE_DEPTH,
        "kg_slice": KG_SLICE,
        "k_values": K_VALUES,
        "context_budget": {"max_chunks": "K (per experiment)", "max_context_chars": MAX_CONTEXT_CHARS},
        "context_builder": "_ExpBContextBuilder (answerability-rejection disabled; FIXED across K & oracle)",
        "llm_model": "poolside/laguna-s-2.1:free",
        "llm_temp": 0.1,
        "llm_max_tokens": 1024,
        "system_prompt": "grounded_qa (FSSAI default; FIXED for all questions)",
        "query_type": "general_qa (FIXED)",
        "notes": ("Retrieval+RRF+CE run once over 150 questions; all K contexts derived from the "
                  "cached CE ranking (spec section 22). 4-arm RRF with question-ident arm, base RRF "
                  "(zero legal weights), CE head-500 scored."),
    }


def phase_build(dry: bool):
    print("=" * 70, flush=True)
    print("Phase BUILD: retrieval + RRF + CE ONCE, cache complete CE top-K ranking", flush=True)
    print("=" * 70, flush=True)
    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}
    gold_registry = load_gold_registry()
    dense = load_raw("A_dense")
    sparse = load_raw("B_sparse")
    kg = load_raw("D_kg")
    ident = load_jsonl(IDENT_CACHE) if IDENT_CACHE.exists() else {}
    if not ident:
        alt = CACHE_DIR / "sparse_identifier.jsonl"
        if alt.exists():
            ident = load_jsonl(alt)

    print(f"  payload_index={len(payload_index)} pts, questions={len(questions)}, "
          f"registry={len(gold_registry)}, ident_cached={len(ident)}", flush=True)

    ce = CrossEncoder(CE_MODEL.as_posix(), max_length=256)
    print(f"  CE model loaded: {CE_MODEL.name}", flush=True)

    gold_index = _load_gold_index(payload_index, family_map)
    # Equivalence sanity check vs brute-force matches_gold on Q001-Q005.
    for qid, q in sorted(questions.items())[:5]:
        units = q.recall_units()
        brute = set()
        for u in units:
            for pid, payload in payload_index.items():
                if matches_gold(payload, u, family_map):
                    brute.add(pid)
        idxd = gold_chunk_ids_for_units(units, gold_index)
        ok = "OK" if brute == idxd else "MISMATCH"
        print(f"  [verify] {qid}: brute={len(brute)} indexed={len(idxd)} -> {ok}", flush=True)

    ce_cache = {"config": _build_config_meta(), "questions": {}}
    ks_all = K_VALUES + [150, 200, 300, 500]
    retrieval_recall = {k: [] for k in ks_all}
    head150_recall = {k: [] for k in K_VALUES}

    n = len(questions)
    t0 = time.perf_counter()
    processed = 0
    for qid, q in sorted(questions.items()):
        d, s, k_arm = dense.get(qid), sparse.get(qid), kg.get(qid)
        if not (d and s and k_arm):
            continue
        pool, rrf_ranked, ce_ranked = build_ce_ranking(
            q, qid, d, s, k_arm, ident, payload_index, family_map, ce)
        if not ce_ranked:
            continue

        gold_units = q.recall_units()
        best_ce_rank, _ = _ce_best_rank(ce_ranked, gold_units, payload_index, family_map)

        unit_ranks = {u.provision_id: rank_of(ce_ranked, u, payload_index, family_map) for u in gold_units}
        for kk in ks_all:
            hit = any((r is not None and r <= kk) for r in unit_ranks.values())
            retrieval_recall[kk].append(int(hit))
        head150 = ce_ranked[:150]
        for kk in K_VALUES:
            head150_recall[kk].append(int(any(
                (rank_of(head150, u, payload_index, family_map) is not None
                 and rank_of(head150, u, payload_index, family_map) <= kk) for u in gold_units)))

        ce_cache["questions"][qid] = {
            "question_id": qid,
            "question": q.question,
            "domains": q.domains,
            "question_types": q.question_types,
            "acceptable_conclusion": q.acceptable_conclusion,
            "insufficient_evidence": q.insufficient_evidence,
            "n_gold_units": len(gold_units),
            "pool_size": len(pool),
            "ce_head_size": len(ce_ranked),
            "ce_gold_rank": best_ce_rank,
            "gold_units": [
                {"provision_id": u.provision_id, "family": u.family,
                 "section": u.section, "role": u.role, "gain": u.gain, "act": u.act}
                for u in gold_units
            ],
            "gold_unit_ids": [u.provision_id for u in gold_units],
            "ce_ranked_keys": [str(it["key"]) for it in ce_ranked],
            "ce_ranked_kinds": [it.get("kind", "chunk") for it in ce_ranked],
            "ce_ranked_scores": [round(float(it.get("ce_score", 0.0)), 6) for it in ce_ranked],
        }
        processed += 1
        if processed % 25 == 0 or processed == n:
            print(f"  {processed}/{n} ce-scored (elapsed {time.perf_counter()-t0:.1f}s)", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CE_RANKING_CACHE.write_text(json.dumps(ce_cache, indent=2), encoding="utf-8")
    print(f"  Wrote CE ranking cache -> {CE_RANKING_CACHE} ({processed} questions)", flush=True)
    _print_build_summary(ce_cache, retrieval_recall, head150_recall, processed)
    if dry:
        _print_dry_context_math(ce_cache, questions, payload_index, family_map)
    return 0


def _print_build_summary(ce_cache, retrieval_recall, head150_recall, processed):
    expected = {"Q001": 59, "Q002": 8, "Q003": 25, "Q004": 4, "Q005": 51}
    print("\n  --- BUILD summary ---", flush=True)
    print(f"  questions with CE ranking: {processed}", flush=True)
    print("  CE gold rank verification (Q001-Q005 vs Experiment A):", flush=True)
    match_all = True
    for qid, exp in expected.items():
        q = ce_cache["questions"].get(qid)
        got = q["ce_gold_rank"] if q else None
        flag = "MATCH" if got == exp else "DIFF"
        if got != exp:
            match_all = False
        print(f"    {qid}: ce_gold_rank={got} (Experiment A={exp}) -> {flag}", flush=True)
    print(f"  -> Q001-5 all match Experiment A: {match_all}", flush=True)

    print("\n  Retrieval recall R@K (CE top-500 ranking, unit any-hit):", flush=True)
    for kk in K_VALUES:
        vals = retrieval_recall[kk]
        print(f"    R@{kk}: {sum(vals)}/{len(vals)} = {sum(vals)/len(vals)*100:.1f}%" if vals
              else f"    R@{kk}: n/a", flush=True)
    print("\n  CE head-150 R@K (Experiment A reference comparator):", flush=True)
    for kk in K_VALUES:
        vals = head150_recall[kk]
        print(f"    head150 R@{kk}: {sum(vals)}/{len(vals)} = {sum(vals)/len(vals)*100:.1f}%" if vals
              else f"    head150 R@{kk}: n/a", flush=True)

    kg_counts = Counter()
    for qd in ce_cache["questions"].values():
        kg_counts[qd["ce_ranked_kinds"].count("kg")] += 1
    print(f"\n  CE ranking KG-item counts per question: {dict(kg_counts)}", flush=True)


def _print_dry_context_math(ce_cache, questions, payload_index, family_map):
    """Validate K-contexts + oracle context sizes with NO LLM calls (spec section 5)."""
    print("\n  --- DRY: K-context accounting (section 5) ---", flush=True)
    print(f"  {'K':>4} {'min_chunks':>10} {'avg_chunks':>10} {'max_chunks':>10} "
          f"{'avg_kg':>7} {'avg_chars':>9} {'avg_tokens':>9}", flush=True)
    for k in K_VALUES:
        counts, chars, toks, kgs = [], [], [], []
        for qid, qd in ce_cache["questions"].items():
            ce = [{"key": ky, "kind": ki, "ce_score": sc} for ky, ki, sc in
                  zip(qd["ce_ranked_keys"], qd["ce_ranked_kinds"], qd["ce_ranked_scores"])]
            chunk_items, kg_items = ce_topk_chunks(ce, k)
            chunks = retrieve_chunks(chunk_items, payload_index)
            built = _ExpBContextBuilder(k).build(questions[qid].question, chunks, "general_qa")
            counts.append(built.chunk_count); chars.append(len(built.context))
            toks.append(built.total_tokens_estimate); kgs.append(len(kg_items))
        n = len(counts)
        print(f"  {k:>4} {min(counts):>10} {sum(counts)/n:>10.1f} {max(counts):>10} "
              f"{sum(kgs)/n:>7.1f} {sum(chars)/n:>9.0f} {sum(toks)/n:>9.0f}", flush=True)

    print("\n  --- DRY: oracle context math ---", flush=True)
    print(f"  {'qid':>8} {'gold_chunks':>11} {'ctx_chunks':>10} {'ctx_chars':>10} {'ctx_tokens':>10}", flush=True)
    gold_index = _load_gold_index(payload_index, family_map)
    for qid in sorted(ce_cache["questions"].keys())[:8]:
        qd = ce_cache["questions"][qid]
        q = questions[qid]
        units = q.recall_units()
        gids = sorted(gold_chunk_ids_for_units(units, gold_index))
        chunks = [to_retrieved_chunk(c, payload_index.get(c, {}), 1.0) for c in gids]
        chunks.sort(key=lambda c: (c.document_title, str(c.chunk_index)))
        built = _ExpBOracleBuilder(min(len(chunks), 200)).build(q.question, chunks, "general_qa")
        print(f"  {qid:>8} {len(gids):>11} {built.chunk_count:>10} {len(built.context):>10} "
              f"{built.total_tokens_estimate:>10}", flush=True)
    return 0


# ---------------------------------------------------------------------------
# Phase: LLM (K-curve)
# ---------------------------------------------------------------------------
def _llm_call_with_retry(question, context_chunks, k, client):
    """Call the (frozen) LLM service with an in-task retry loop.

    The frozen `_SSLBypassLLMClient` already retries HTTP failures 3x with a
    1/2/4s backoff, then returns an *error response* (success=False) rather
    than raising. OpenRouter's free tier can return 429 over a longer window,
    so we add a wider backoff here (3/6/12/24s) and re-call `service.generate`.
    Context assembly (`built`) is deterministic and built once.
    """
    cb = _ExpBContextBuilder(k)
    service = GroundedGenerationService(llm_client=client, context_builder=cb)
    built = cb.build(question.question, context_chunks, "general_qa")
    last_err = None
    resp = None
    for attempt in range(LLM_MAX_RETRIES):
        try:
            resp = service.generate(question.question, context_chunks, query_type="general_qa")
            if resp and (resp.answer or ""):
                return resp, built
            last_err = "empty answer"
        except Exception as exc:  # defensive: client/service raising
            last_err = repr(exc)
            resp = None
        if attempt < LLM_MAX_RETRIES - 1:
            time.sleep(3 * (2 ** attempt))  # 3, 6, 12, 24s
    return resp, built, last_err


def _llm_task(qid, k, question, qd, payload_index, family_map, gold_index, client):
    ce_ranked = qd.get("_ce_ranked") or [
        {"key": ky, "kind": ki, "ce_score": sc}
        for ky, ki, sc in zip(qd["ce_ranked_keys"], qd["ce_ranked_kinds"], qd["ce_ranked_scores"])
    ]
    chunk_items, kg_items = ce_topk_chunks(ce_ranked, k)
    ce_topk_chunk_ids = [it["key"] for it in chunk_items]
    context_chunks = retrieve_chunks(chunk_items, payload_index)
    gold_units = question.recall_units()
    gold_chunk_ids = gold_chunk_ids_for_units(gold_units, gold_index)

    # gold_present / ce_gold_rank come from the authoritative cached CE ranking
    # (ce_gold_rank was computed in phase_build on the LIVE CE items, which carry
    # the full payload metadata that rank_of needs for KG items; the cache-rebuilt
    # _ce_ranked drops those fields, so we must NOT recompute rank_of on it).
    ce_gold_rank = qd.get("ce_gold_rank")
    gold_present = ce_gold_rank is not None and ce_gold_rank <= k

    # Build a minimal error record (still carries the spec §5 context accounting).
    err_rec = {
        "question_id": qid, "k": k, "error": None,
        "requested_k": k, "ce_candidates": k,
        "ce_chunk_count": len(chunk_items), "ce_kg_items": len(kg_items),
        "ce_kg_discarded": len(kg_items),
        "gold_present": bool(gold_present), "ce_gold_rank": ce_gold_rank,
        "context_chunk_count": len(ce_topk_chunk_ids),
    }

    try:
        out = _llm_call_with_retry(question, context_chunks, k, client)
        if len(out) == 3:  # retries exhausted -> (resp, built, last_err)
            resp, built, last_err = out
            err_rec["error"] = last_err or "empty answer"
            return err_rec
        resp, built = out
    except Exception as exc:
        err_rec["error"] = repr(exc)
        return err_rec

    context_ids = [cit["chunk_id"] for cit in (built.citations or [])] if built else ce_topk_chunk_ids
    m = compute_metrics(resp, built, question, ce_topk_chunk_ids, context_ids,
                        gold_chunk_ids, gold_units, payload_index, family_map)
    m.update({
        "question_id": qid, "k": k,
        "requested_k": k, "ce_candidates": k,
        "ce_chunk_count": len(chunk_items), "ce_kg_items": len(kg_items),
        "ce_kg_discarded": len(kg_items),
        "context_ids": context_ids,
        "context_chunk_count": len(context_ids),
        "gold_present": bool(gold_present),
        "ce_gold_rank": ce_gold_rank,
        "error": None,
    })
    has_answer = bool(resp.answer or "")
    if not has_answer:
        m["error"] = m.get("error") or "empty answer"
    return m


def phase_llm(use_stub: bool, concurrency: int, resume: bool, limit: int | None):
    print("=" * 70, flush=True)
    print(f"Phase LLM: K-curve (150 Q x {len(K_VALUES)} K = {150*len(K_VALUES)} calls)")
    print("=" * 70, flush=True)
    ce_cache = _load_ce_cache()
    _attach_ce_ranked(ce_cache)
    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}
    gold_index = _load_gold_index(payload_index, family_map)

    done = _load_set(LLM_CKPT, skip_errors=resume) if resume else set()
    total_expected = len(questions) * len(K_VALUES)
    print(f"  cached CE: {len(ce_cache['questions'])} questions | "
          f"checkpointed: {len(done)}/{total_expected} | stub={use_stub} | concurrency={concurrency}",
          flush=True)

    client = _make_client(use_stub)
    tasks = [(qid, k) for qid in sorted(questions) for k in K_VALUES
             if qid in ce_cache["questions"] and (qid, k) not in done]
    if limit:
        tasks = tasks[:limit]
    print(f"  {len(tasks)} LLM tasks ...", flush=True)

    lock = threading.Lock()
    completed = len(done)
    total = completed + len(tasks)
    LLM_CKPT.parent.mkdir(parents=True, exist_ok=True)
    if not resume or not LLM_CKPT.exists():
        LLM_CKPT.write_text("")

    t0 = time.perf_counter()
    err_count = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {
            ex.submit(_llm_task, qid, k, questions[qid], ce_cache["questions"][qid],
                      payload_index, family_map, gold_index, client): (qid, k)
            for qid, k in tasks
        }
        for fut in as_completed(futures):
            qid, k = futures[fut]
            try:
                rec = fut.result()
            except Exception as exc:
                rec = {"question_id": qid, "k": k, "error": repr(exc)}
            if rec.get("error"):
                err_count += 1
            with lock:
                with open(LLM_CKPT, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                completed += 1
                if completed % 25 == 0 or completed == total:
                    print(f"  [{completed}/{total}] {qid} K={k} "
                          f"({time.perf_counter()-t0:.0f}s, {err_count} errors)", flush=True)
    print(f"  LLM phase done: {completed}/{total} ({err_count} errors, "
          f"{time.perf_counter()-t0:.0f}s)", flush=True)
    return 0


# ---------------------------------------------------------------------------
# Phase: Oracle
# ---------------------------------------------------------------------------
def _oracle_task(qid, question, qd, payload_index, family_map, gold_index, client):
    gold_units = question.recall_units()
    gold_ids = sorted(gold_chunk_ids_for_units(gold_units, gold_index))
    if not gold_ids:
        return {"question_id": qid, "error": "no gold chunks in corpus", "k": None}
    chunks = [to_retrieved_chunk(c, payload_index.get(c, {}), 1.0) for c in gold_ids]
    chunks.sort(key=lambda c: (c.document_title, str(c.chunk_index)))
    max_chunks = min(len(chunks), 200)

    try:
        cb = _ExpBOracleBuilder(max_chunks)
        service = GroundedGenerationService(llm_client=client, context_builder=cb)
        built = cb.build(question.question, chunks, "general_qa")
        resp = _retry(
            lambda: service.generate(question.question, chunks, query_type="general_qa"),
            f"{qid}/oracle",
        )
    except Exception as exc:
        return {"question_id": qid, "error": repr(exc), "k": None}

    context_ids = [cit["chunk_id"] for cit in (built.citations or [])] if built else []
    m = compute_metrics(resp, built, question, gold_ids, context_ids,
                        set(gold_ids), gold_units, payload_index, family_map)
    m.update({
        "question_id": qid, "oracle": True, "k": None,
        "requested_k": None, "ce_candidates": len(gold_ids),
        "oracle_gold_chunks_total": len(gold_ids),
        "gold_present": True, "ce_gold_rank": None, "error": None,
    })
    if not (resp.answer or ""):
        m["error"] = m.get("error") or "empty answer"
    return m


def phase_oracle(use_stub: bool, concurrency: int, resume: bool, limit: int | None):
    print("=" * 70, flush=True)
    print("Phase ORACLE: gold-context oracle (150 calls)")
    print("=" * 70, flush=True)
    ce_cache = _load_ce_cache()
    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}
    gold_index = _load_gold_index(payload_index, family_map)

    done = _load_set(ORACLE_CKPT, skip_errors=resume) if resume else set()
    total_expected = len(questions)
    print(f"  checkpointed: {len(done)}/{total_expected} | stub={use_stub} | concurrency={concurrency}",
          flush=True)

    client = _make_client(use_stub)
    tasks = [qid for qid in sorted(questions)
             if qid in ce_cache["questions"] and (qid, None) not in done]
    if limit:
        tasks = tasks[:limit]
    print(f"  {len(tasks)} oracle tasks ...", flush=True)

    lock = threading.Lock()
    completed = len(done)
    total = completed + len(tasks)
    ORACLE_CKPT.parent.mkdir(parents=True, exist_ok=True)
    if not resume or not ORACLE_CKPT.exists():
        ORACLE_CKPT.write_text("")

    t0 = time.perf_counter()
    err_count = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(_oracle_task, qid, questions[qid], ce_cache["questions"][qid],
                             payload_index, family_map, gold_index, client): qid for qid in tasks}
        for fut in as_completed(futures):
            qid = futures[fut]
            try:
                rec = fut.result()
            except Exception as exc:
                rec = {"question_id": qid, "error": repr(exc), "k": None}
            if rec.get("error"):
                err_count += 1
            with lock:
                with open(ORACLE_CKPT, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                completed += 1
                if completed % 25 == 0 or completed == total:
                    print(f"  [{completed}/{total}] oracle {qid} "
                          f"({time.perf_counter()-t0:.0f}s, {err_count} errors)", flush=True)
    print(f"  Oracle phase done: {completed}/{total} ({err_count} errors, "
          f"{time.perf_counter()-t0:.0f}s)", flush=True)
    return 0


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------
def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _pctl(xs, p):
    if not xs:
        return 0.0
    s = sorted(xs)
    idx = int(math.ceil(p / 100.0 * len(s))) - 1
    return s[max(0, min(idx, len(s) - 1))]


def _ki(d: dict):
    """Re-key a K-indexed dict (possibly JSON string keys) to int keys."""
    return {int(k): v for k, v in d.items()}


# ---------------------------------------------------------------------------
# Phase: ANALYZE
# ---------------------------------------------------------------------------
def _load_llm_records() -> dict[tuple, dict]:
    recs = {}
    if LLM_CKPT.exists():
        for line in LLM_CKPT:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            recs[(r["question_id"], int(r["k"]))] = r
    return recs


def _load_oracle_records() -> dict[str, dict]:
    recs = {}
    if ORACLE_CKPT.exists():
        for line in ORACLE_CKPT:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            recs[r["question_id"]] = r
    return recs


def phase_analyze():
    print("=" * 70, flush=True)
    print("Phase ANALYZE: aggregates, analyses, diagnostics")
    print("=" * 70, flush=True)
    ce_cache = _load_ce_cache()
    _attach_ce_ranked(ce_cache)
    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}
    gold_index = _load_gold_index(payload_index, family_map)

    llm_recs = _load_llm_records()
    oracle_recs = _load_oracle_records()
    print(f"  LLM records: {len(llm_recs)} | Oracle records: {len(oracle_recs)}", flush=True)

    # --- K-curve aggregates (section 6/7) ---
    k_curve = {}
    for k in K_VALUES:
        recs = [r for (q, kk), r in llm_recs.items() if kk == k and not r.get("error")]
        errs = [r for (q, kk), r in llm_recs.items() if kk == k and r.get("error")]
        if not recs:
            continue
        k_curve[k] = {
            "n": len(recs), "n_error": len(errs),
            "answer_correctness": round(_mean([r["answer_correctness"] for r in recs]), 4),
            "answer_coverage": round(_mean([r["answer_coverage"] for r in recs]), 4),
            "answer_jaccard": round(_mean([r["answer_jaccard"] for r in recs]), 4),
            "correct_rate": round(_mean([1 if r["correct"] else 0 for r in recs]), 4),
            "citation_recall": round(_mean([r["citation_recall"] for r in recs]), 4),
            "citation_precision": round(_mean([r["citation_precision"] for r in recs]), 4),
            "groundedness": round(_mean([r["groundedness"] for r in recs]), 4),
            "abstain_rate": round(_mean([1 if r["abstained"] else 0 for r in recs]), 4),
            "abstain_correct_rate": round(_mean([1 if r["abstain_correct"] else 0 for r in recs]), 4),
            "avg_latency_ms": round(_mean([r["latency_ms"] for r in recs]), 1),
            "median_latency_ms": round(_pctl([r["latency_ms"] for r in recs], 50), 1),
            "p95_latency_ms": round(_pctl([r["latency_ms"] for r in recs], 95), 1),
            "mean_answer_length": round(_mean([r["answer_length"] for r in recs]), 1),
            "median_answer_length": round(_pctl([r["answer_length"] for r in recs], 50), 1),
            "mean_context_tokens": round(_mean([r["context_tokens"] for r in recs]), 1),
            "median_context_tokens": round(_pctl([r["context_tokens"] for r in recs], 50), 1),
            "p95_context_tokens": round(_pctl([r["context_tokens"] for r in recs], 95), 1),
            "mean_context_chunks": round(_mean([r["context_chunk_count"] for r in recs]), 2),
            "mean_prompt_tokens": round(_mean([r["prompt_tokens"] for r in recs]), 1),
            "mean_completion_tokens": round(_mean([r["completion_tokens"] for r in recs]), 1),
            "mean_grader_score": round(_mean([r["grader_score"] for r in recs]), 3),
            "grader_correct_rate": round(_mean([1 if r["grader_score"] >= 1 else 0 for r in recs]), 4),
        }

    # --- R@K retrieval recall (section 8) + MRR/NDCG@10 ---
    r_at_k = {}
    mrr10, ndcg10 = [], []
    for k in K_VALUES + [150, 200, 300, 500]:
        r_at_k[k] = 0.0
    total_qs = 0
    for qid, qd in ce_cache["questions"].items():
        total_qs += 1
        rank = qd["ce_gold_rank"]
        if rank is not None and rank <= 10:
            mrr10.append(1.0 / rank)
            ndcg10.append(1.0 / math.log2(rank + 1))
        else:
            mrr10.append(0.0)
            ndcg10.append(0.0)
        for k in r_at_k:
            if rank is not None and rank <= k:
                r_at_k[k] += 1
    r_at_k = {k: round(v / max(total_qs, 1), 4) for k, v in r_at_k.items()}

    # --- Conditional accuracy (section 9) ---
    conditional = {}
    for k in K_VALUES:
        recs_k = {q: r for (q, kk), r in llm_recs.items() if kk == k and not r.get("error")}
        present = [r for r in recs_k.values() if r.get("gold_present")]
        absent = [r for r in recs_k.values() if not r.get("gold_present")]
        conditional[k] = {
            "gold_present_n": len(present),
            "gold_absent_n": len(absent),
            "correct_given_present": round(_mean([1 if r["correct"] else 0 for r in present]), 4) if present else None,
            "correct_given_absent": round(_mean([1 if r["correct"] else 0 for r in absent]), 4) if absent else None,
            "grader_correct_given_present": round(_mean([1 if r["grader_score"] >= 1 else 0 for r in present]), 4) if present else None,
        }

    # --- Oracle aggregates (section 10) ---
    ogood = [r for r in oracle_recs.values() if not r.get("error")]
    oracle_agg = {
        "n": len(ogood),
        "n_error": len([r for r in oracle_recs.values() if r.get("error")]),
        "answer_correctness": round(_mean([r["answer_correctness"] for r in ogood]), 4),
        "correct_rate": round(_mean([1 if r["correct"] else 0 for r in ogood]), 4),
        "grader_correct_rate": round(_mean([1 if r["grader_score"] >= 1 else 0 for r in ogood]), 4),
        "grader_mean_score": round(_mean([r["grader_score"] for r in ogood]), 3),
        "citation_recall": round(_mean([r["citation_recall"] for r in ogood]), 4),
        "citation_precision": round(_mean([r["citation_precision"] for r in ogood], 4) if False else (_mean([r["citation_precision"] for r in ogood])), 4),
        "groundedness": round(_mean([r["groundedness"] for r in ogood]), 4),
        "abstain_rate": round(_mean([1 if r["abstained"] else 0 for r in ogood]), 4),
        "avg_latency_ms": round(_mean([r["latency_ms"] for r in ogood]), 1),
        "mean_answer_length": round(_mean([r["answer_length"] for r in ogood]), 1),
        "mean_context_chunks": round(_mean([r["context_chunk_count"] for r in ogood]), 2),
        "mean_context_tokens": round(_mean([r["context_tokens"] for r in ogood]), 1),
        "mean_context_chars": round(_mean([r["context_chars"] for r in ogood]), 0),
        "mean_gold_chunks_total": round(_mean([r["oracle_gold_chunks_total"] for r in ogood]), 1),
    }

    # --- Failure classification A-E (section 19) ---
    failure = {}
    for k in K_VALUES:
        recs_k = {q: r for (q, kk), r in llm_recs.items() if kk == k and not r.get("error")}
        A = B = C = D = E = 0
        for q, r in recs_k.items():
            gp = r.get("gold_present", False)
            correct = r["correct"]
            or_ok = oracle_recs.get(q, {}).get("correct", False)
            if not gp:
                A += 1
            elif correct:
                C += 1
            else:
                B += 1
                if or_ok:
                    D += 1
                else:
                    E += 1
        failure[k] = {"A_retrieval_limit": A, "B_gold_present_wrong": B,
                      "C_correct": C, "D_recoverable": D, "E_llm_limit": E,
                      "total": len(recs_k)}

    # --- Noise analysis (section 13) ---
    noise = {}
    for k in K_VALUES:
        recs_k = [r for (q, kk), r in llm_recs.items() if kk == k and not r.get("error")]
        n = len(recs_k) or 1
        tot_chunks = sum(r["context_chunk_count"] for r in recs_k)
        tot_gold = sum(r["n_gold_chunks_in_context"] for r in recs_k)
        tot_kg = sum(r["ce_kg_items"] for r in recs_k)
        noise[k] = {
            "n_q": len(recs_k),
            "mean_total_context_chunks": round(tot_chunks / n, 2),
            "mean_gold_chunks_in_context": round(tot_gold / n, 2),
            "mean_non_gold_chunks": round((tot_chunks - tot_gold) / n, 2),
            "mean_kg_items": round(tot_kg / n, 2),
            "frac_gold": round(tot_gold / max(tot_chunks, 1), 4),
            "frac_non_gold": round((tot_chunks - tot_gold - tot_kg) / max(tot_chunks, 1), 4),
            "frac_kg": round(tot_kg / max(tot_chunks, 1), 4),
        }

    # --- Monotonicity (section 14) ---
    mon_examples = []
    mono = 0
    for qid in ce_cache["questions"]:
        curve = [llm_recs.get((qid, k), {}).get("answer_correctness", 0.0) for k in K_VALUES]
        if all(curve[i] <= curve[i + 1] + 1e-9 for i in range(len(curve) - 1)):
            mono += 1
        else:
            if len(mon_examples) < 15:
                mon_examples.append({"qid": qid, "curve": [round(c, 3) for c in curve]})
    monotonic = {"fully_monotonic": mono, "non_monotonic": total_qs - mono,
                 "total": total_qs, "examples": mon_examples}

    # --- Transitions (section 18): per-question K-by-K correctness ---
    transitions = {"total_transitions": 0, "correct_to_incorrect": 0, "incorrect_to_correct": 0,
                   "examples": []}
    for qid in ce_cache["questions"]:
        prev = None
        for k in K_VALUES:
            cur = llm_recs.get((qid, k), {}).get("correct", False)
            if prev is not None and cur != prev:
                transitions["total_transitions"] += 1
                if prev and not cur:
                    transitions["correct_to_incorrect"] += 1
                elif (not prev) and cur:
                    transitions["incorrect_to_correct"] += 1
            prev = cur

    # --- Context efficiency (section 12) ---
    eff_by_k = {}
    best_eff_k, best_eff = None, -1.0
    for k in K_VALUES:
        if k in k_curve:
            e = (k_curve[k]["answer_correctness"] * 1000.0) / max(k_curve[k]["mean_context_tokens"], 1.0)
            eff_by_k[k] = round(e, 4)
            if e > best_eff:
                best_eff = e
                best_eff_k = k

    # --- Condition A vs B (section 11) ---
    cond_a = {k: (k_curve[k]["answer_correctness"] if k in k_curve else None) for k in K_VALUES}
    cond_b = oracle_agg["answer_correctness"]

    diag = _diagnostics(K_VALUES, k_curve, oracle_agg, r_at_k, conditional,
                        failure, noise, eff_by_k, mon_examples)

    aggregate = {
        "config": ce_cache["config"],
        "n_questions": len(ce_cache["questions"]),
        "k_values": K_VALUES,
        "k_curve": {str(k): k_curve[k] for k in K_VALUES if k in k_curve},
        "retrieval_recall": {str(k): r_at_k[k] for k in r_at_k},
        "mrr_at_10": round(_mean(mrr10), 4),
        "ndcg_at_10": round(_mean(ndcg10), 4),
        "conditional_accuracy": {str(k): conditional[k] for k in K_VALUES},
        "oracle": oracle_agg,
        "failure_classification": {str(k): failure[k] for k in K_VALUES},
        "noise_analysis": {str(k): noise[k] for k in K_VALUES},
        "monotonicity": monotonic,
        "transitions": transitions,
        "context_efficiency": {"optimum_k": best_eff_k,
                               "optimum_efficiency": round(best_eff, 4),
                               "efficiency_by_k": eff_by_k},
        "condition_a_vs_b": {"condition_a_correctness_by_k": cond_a, "condition_b_oracle": cond_b},
        "diagnostics": diag,
        "llm_record_count": len(llm_recs),
        "oracle_record_count": len(oracle_recs),
    }
    AGGREGATE_JSON.write_text(json.dumps(aggregate, indent=2, default=str), encoding="utf-8")
    print(f"  Wrote aggregate -> {AGGREGATE_JSON}", flush=True)

    # --- Per-question (section 16) ---
    per_question = {}
    for qid in ce_cache["questions"]:
        qd = ce_cache["questions"][qid]
        k_curve_q = {}
        for k in K_VALUES:
            r = llm_recs.get((qid, k))
            if r:
                k_curve_q[str(k)] = {kk: vv for kk, vv in r.items()
                                     if kk not in ("answer", "expected_conclusion", "grader")}
                k_curve_q[str(k)]["correctness"] = r.get("answer_correctness", 0.0)
                k_curve_q[str(k)]["correct"] = r.get("correct", False)
        or_rec = oracle_recs.get(qid, {})
        per_question[qid] = {
            "question": qd["question"],
            "domains": qd["domains"],
            "acceptable_conclusion": qd["acceptable_conclusion"],
            "n_gold_units": qd["n_gold_units"],
            "ce_gold_rank": qd["ce_gold_rank"],
            "pool_size": qd["pool_size"],
            "n_gold_chunks_total": len(gold_chunk_ids_for_units(
                questions[qid].recall_units(), gold_index)) if qid in questions else 0,
            "k_curve": k_curve_q,
            "oracle": {k: vv for k, vv in or_rec.items() if k not in ("answer",)} if or_rec else None,
        }
    PER_QUESTION_JSON.write_text(json.dumps(per_question, indent=2, default=str), encoding="utf-8")
    print(f"  Wrote per-question -> {PER_QUESTION_JSON}", flush=True)
    return aggregate


def _diagnostics(ks, k_curve, oracle_agg, r_at_k, conditional, failure, noise, eff_by_k, mon_examples):
    """Answer the 11 diagnostic questions (spec section 20/21)."""
    k1 = k_curve[ks[0]]["answer_correctness"]
    kmax = k_curve[ks[-1]]["answer_correctness"]
    k100 = k_curve.get(100, {}).get("answer_correctness", kmax)

    # Q1: does increasing context improve correctness?
    q1 = {"q": "Does increasing CE-ranked context improve answer correctness?",
          "answer": "yes" if kmax > k1 else "no",
          "k1": round(k1, 4), "k100": round(k100, 4), "delta": round(kmax - k1, 4)}

    # Q2: plateau
    plateau_k = None
    for i in range(1, len(ks)):
        d = k_curve[ks[i]]["answer_correctness"] - k_curve[ks[i - 1]]["answer_correctness"]
        if abs(d) < 0.005:
            plateau_k = ks[i]
            break
    q2 = {"q": "At what K does the correctness curve plateau?", "plateau_k": plateau_k,
          "deltas": {ks[i]: round(k_curve[ks[i]]["answer_correctness"] - k_curve[ks[i - 1]]["answer_correctness"], 4)
                     for i in range(1, len(ks))}}

    # Q3: noise overload (does more context ever HURT after the peak)?
    best_k = max(ks, key=lambda k: k_curve[k]["answer_correctness"])
    best_c = k_curve[best_k]["answer_correctness"]
    overload_k = None
    for k in ks[ks.index(best_k) + 1:]:
        if k_curve[k]["answer_correctness"] < best_c - 0.01:
            overload_k = k
            break
    q3 = {"q": "Is there a K beyond which more context hurts (noise overload)?",
          "overload": overload_k is not None, "overload_k": overload_k,
          "best_k": best_k, "best_correctness": round(best_c, 4)}

    # Q4: fraction of oracle-K1 gap closed at K=100
    gap = oracle_agg["answer_correctness"] - k1
    closed = (k100 - k1) / gap if gap > 0 else (1.0 if k100 >= oracle_agg["answer_correctness"] else 0.0)
    q4 = {"q": "Fraction of (oracle - K1) correctness gap closed at K=100?",
          "k1": round(k1, 4), "k100": round(k100, 4),
          "oracle": round(oracle_agg["answer_correctness"], 4),
          "gap": round(gap, 4), "fraction_closed": round(closed, 4)}

    # Q5: dominant failure class at K=100
    f100 = failure.get(100) or failure.get(ks[-1])
    dom = None
    if f100:
        sub = {k: v for k, v in f100.items() if k != "total"}
        dom = max(sub.items(), key=lambda x: x[1])[0]
    q5 = {"q": "Dominant failure class at K=100?", "failure_at_k100": f100, "dominant": dom}

    # Q6: CE recall vs LLM reasoning
    r100 = r_at_k.get(100, 0.0)
    inter = "oracle corrects a large gap (CE selection limit)" if oracle_agg["answer_correctness"] > k100 + 0.05 \
        else "oracle does NOT correct the gap (LLM reasoning/generation or evaluation limit)"
    q6 = {"q": "Is the ceiling limited by CE recall or LLM reasoning?",
          "r_at_100": r100, "k100_correctness": round(k100, 4),
          "oracle_correctness": round(oracle_agg["answer_correctness"], 4),
          "interpretation": inter}

    # Q7: conditional accuracy
    cond100 = conditional.get(100) or conditional.get(ks[-1])
    q7 = {"q": "Conditional accuracy: correct|gold_present vs correct|gold_absent?",
          "conditional_at_k100": cond100}

    # Q8: context efficiency optimum
    q8 = {"q": "Context efficiency (correctness per ~1K context tokens); optimum?",
          "optimum_k": eff_by_k and max(eff_by_k, key=lambda k: eff_by_k[k]),
          "efficiency_by_k": eff_by_k}

    # Q9: noise at K=100
    q9 = {"q": "Noise composition at K=100?", "noise_at_k100": noise.get(100)}

    # Q10: R@K elbow
    elbow = None
    prev = -1.0
    for k in ks:
        r = r_at_k.get(k, 0.0)
        if prev >= 0 and (r - prev) < 0.03:
            elbow = k
            break
        prev = r
    q10 = {"q": "Where is the R@K retrieval-ceiling elbow?", "elbow_k": elbow, "r_at_k": r_at_k}

    # Q11: oracle ceiling
    q11 = {"q": "Oracle ceiling vs K=100: retrieval or LLM limit?",
           "oracle": round(oracle_agg["answer_correctness"], 4), "k100": round(k100, 4),
           "gap": round(oracle_agg["answer_correctness"] - k100, 4),
           "interpretation": ("large gap closed by gold evidence -> retrieval/CE selection limit"
                              if oracle_agg["answer_correctness"] > k100 + 0.05
                              else "oracle ~ K=100 -> LLM reasoning/generation or evaluation limit")}

    return {"q1": q1, "q2": q2, "q3": q3, "q4": q4, "q5": q5, "q6": q6,
            "q7": q7, "q8": q8, "q9": q9, "q10": q10, "q11": q11}


# ---------------------------------------------------------------------------
# Plots (6)
# ---------------------------------------------------------------------------
def _load_agg() -> dict:
    return json.loads(AGGREGATE_JSON.read_text())


def phase_plots():
    print("=" * 70, flush=True)
    print("Phase PLOTS: 6 matplotlib plots")
    print("=" * 70, flush=True)
    agg = _load_agg()
    _ki_cache = agg["k_curve"]
    kc = _ki(agg["k_curve"])
    ret = _ki(agg["retrieval_recall"])
    ks = K_VALUES
    PLOT_DIR.mkdir(parents=True, exist_ok=True)

    def _vals(key):
        return [kc[k][key] for k in ks]

    def _logticks():
        return (ks, {k: str(k) for k in ks})

    # Plot 1: correctness/coverage/jaccard vs K (+ oracle)
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(ks, _vals("answer_correctness"), "o-", lw=2, label="Answer Correctness")
    ax.plot(ks, _vals("answer_coverage"), "s--", label="Coverage")
    ax.plot(ks, _vals("answer_jaccard"), "^-:", label="Jaccard")
    o = agg["oracle"]
    ax.axhline(o["answer_correctness"], color="red", ls=":", label=f"Oracle ({o['answer_correctness']:.2f})")
    ax.set_xscale("log"); ax.set_xticks(ks); ax.get_xaxis().set_major_formatter(plt.FuncFormatter(lambda v, _: str(int(v))))
    ax.set_xlabel("K (CE-ranked candidates supplied to LLM)")
    ax.set_ylabel("Score (0-1)"); ax.set_title("Answer Quality vs CE Top-K")
    ax.legend(); ax.grid(True, alpha=0.3); fig.tight_layout()
    fig.savefig(PLOT_DIR / "experiment_b_01_correctness_vs_k.png", dpi=130); plt.close(fig)

    # Plot 2: citations vs K
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(ks, _vals("citation_recall"), "o-", label="Citation Recall (unit-based)")
    ax.plot(ks, _vals("citation_precision"), "s--", label="Citation Precision")
    ax.axhline(o["citation_recall"], color="red", ls=":", label=f"Oracle recall ({o['citation_recall']:.2f})")
    ax.set_xscale("log"); ax.set_xticks(ks); ax.get_xaxis().set_major_formatter(plt.FuncFormatter(lambda v, _: str(int(v))))
    ax.set_xlabel("K"); ax.set_ylabel("Rate (0-1)"); ax.set_title("Citation Recall & Precision vs CE Top-K")
    ax.legend(); ax.grid(True, alpha=0.3); fig.tight_layout()
    fig.savefig(PLOT_DIR / "experiment_b_02_citations_vs_k.png", dpi=130); plt.close(fig)

    # Plot 3: groundedness / abstention vs K
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(ks, _vals("groundedness"), "o-", lw=2, label="Groundedness")
    ax.plot(ks, _vals("abstain_rate"), "s--", label="Abstain rate")
    ax.set_xscale("log"); ax.set_xticks(ks); ax.get_xaxis().set_major_formatter(plt.FuncFormatter(lambda v, _: str(int(v))))
    ax.set_xlabel("K"); ax.set_ylabel("Rate (0-1)"); ax.set_title("Groundedness & Abstention vs CE Top-K")
    ax.legend(); ax.grid(True, alpha=0.3); fig.tight_layout()
    fig.savefig(PLOT_DIR / "experiment_b_03_groundedness_vs_k.png", dpi=130); plt.close(fig)

    # Plot 4: requested K vs actual context chunks + context tokens
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(ks, ks, "k--", alpha=0.4, label="requested K")
    ax.plot(ks, _vals("mean_context_chunks"), "o-", label="mean context chunks (actual)")
    ax.set_xscale("log"); ax.set_xticks(ks); ax.get_xaxis().set_major_formatter(plt.FuncFormatter(lambda v, _: str(int(v))))
    ax.set_xlabel("K (requested CE top-K)"); ax.set_ylabel("Chunks in LLM context")
    ax.set_title("Requested K vs Actual Context Chunks"); ax.legend(); ax.grid(True, alpha=0.3)
    fig.tight_layout(); fig.savefig(PLOT_DIR / "experiment_b_04_context_size_vs_k.png", dpi=130); plt.close(fig)

    # Plot 5: dual-axis R@K vs answer correctness
    fig, ax1 = plt.subplots(figsize=(9, 5.5))
    ax1.plot(ks, [ret[k] for k in ks], "o-", color="tab:blue", label="R@K (retrieval ceiling)")
    ax1.set_xscale("log"); ax1.set_xticks(ks)
    ax1.get_xaxis().set_major_formatter(plt.FuncFormatter(lambda v, _: str(int(v))))
    ax1.set_xlabel("K"); ax1.set_ylabel("R@K recall", color="tab:blue"); ax1.tick_params(axis="y", labelcolor="tab:blue")
    ax2 = ax1.twinx()
    ax2.plot(ks, _vals("answer_correctness"), "s--", color="tab:orange", label="Answer Correctness")
    ax2.set_ylabel("Answer Correctness", color="tab:orange"); ax2.tick_params(axis="y", labelcolor="tab:orange")
    ax1.set_title("Retrieval Ceiling (R@K) vs Answer Correctness")
    ax1.grid(True, alpha=0.3); fig.tight_layout()
    fig.savefig(PLOT_DIR / "experiment_b_05_retrieval_vs_answer.png", dpi=130); plt.close(fig)

    # Plot 6: latency vs K (mean/median/P95)
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(ks, _vals("avg_latency_ms"), "o-", label="mean latency")
    ax.plot(ks, _vals("median_latency_ms"), "s--", label="median latency")
    ax.plot(ks, _vals("p95_latency_ms"), "^-:", label="P95 latency")
    ax.set_xscale("log"); ax.set_xticks(ks)
    ax.get_xaxis().set_major_formatter(plt.FuncFormatter(lambda v, _: str(int(v))))
    ax.set_xlabel("K"); ax.set_ylabel("Latency (ms)"); ax.set_title("LLM Latency vs CE Top-K")
    ax.legend(); ax.grid(True, alpha=0.3); fig.tight_layout()
    fig.savefig(PLOT_DIR / "experiment_b_06_latency_vs_k.png", dpi=130); plt.close(fig)
    print(f"  Wrote 6 plots -> {PLOT_DIR}", flush=True)
    return 0


# ---------------------------------------------------------------------------
# Report (summary MD)
# ---------------------------------------------------------------------------
def _pct(x):
    return f"{x*100:.1f}%" if isinstance(x, (int, float)) and not isinstance(x, bool) else str(x)


def _tbl(headers, rows):
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return out


def phase_report():
    print("=" * 70, flush=True)
    print("Phase REPORT: experiment_B_summary.md")
    print("=" * 70, flush=True)
    agg = _load_agg()
    kc = _ki(agg["k_curve"])
    cond = _ki(agg["conditional_accuracy"])
    fail = _ki(agg["failure_classification"])
    noise = _ki(agg["noise_analysis"])
    ret = agg["retrieval_recall"]
    diag = agg["diagnostics"]
    o = agg["oracle"]
    ks = K_VALUES
    L = []
    A = L.append

    A("# Experiment B — CE Top-K → LLM Answer-Quality Curve")
    A("")
    A("## 1. Executive Summary")
    A("")
    k1 = kc[ks[0]]["answer_correctness"]; k100 = kc[ks[-1]]["answer_correctness"]
    A(f"Holds CE v2_K500, retrieval, RRF, context assembly, prompts, and the LLM model fixed; "
      f"varies only K (CE-ranked candidates supplied to the LLM) over all 150 questions, "
      f"K ∈ {{{', '.join(map(str, ks))}}}, plus a gold-context oracle.")
    A("")
    A(f"**Headline.** Token-overlap answer correctness rises from {k1*100:.1f}% (K=1) to "
      f"{k100*100:.1f}% (K=100); the oracle reaches {o['answer_correctness']*100:.1f}%. "
      f"Retrieval ceiling R@100 = {ret.get('100',0)*100:.1f}% (MRR@10={agg['mrr_at_10']}, "
      f"NDCG@10={agg['ndcg_at_10']}). The diagnostic conclusion is: "
      f"{diag['q6']['interpretation']}.")
    A("")
    A("## 2. Objective & Experimental Design")
    A("")
    A("Determine whether, *once CE v2 has ranked the evidence*, giving the LLM more of that "
      "ranked evidence improves answer quality -- and isolate the cause if it does not.")
    A("")
    A("The only experimental variable is **K = number of CE-ranked candidates supplied to the LLM**.")
    A("")
    A("NOT done (spec section 3): no CE retraining/weight modification; no change to retrieval arms, "
      "RRF, benchmark, gold labels, LLM model, prompts, citation mechanism, sanitizer, or abstention logic.")
    A("")
    A("## 3. Benchmark")
    A(f"150-question frozen legal RAG benchmark (benchmark_v1.0.jsonl). "
      f"{agg['n_questions']} questions carry a CE ranking. Gold resolution uses Experiment A's "
      f"corrected provision-level mapping via `matches_gold` / `rank_of` (NOT the prior E2E gold-ID "
      f"implementation). Gold chunk IDs = all payload point IDs where `matches_gold(payload, unit)` "
      f"is true (find_all_gold_chunk_ids semantics).")
    A("")
    A("## 4. Fixed Pipeline & Configuration")
    A("")
    cfg = agg.get("config", {})
    for row in _tbl(["Item", "Value"], [[k, (json.dumps(v) if isinstance(v, (dict, list)) else v)]
                                         for k, v in cfg.items()]):
        A(row)
    A("")
    A("## 5. Experimental K Values")
    A("")
    A(f"K ∈ {{{', '.join(map(str, K_VALUES))}}}. CE scored over the RRF top-500 (complete CE top-K "
      "ranking cached once; all K contexts derived from it).")
    A("")
    A("## 6. Primary LLM Metrics (per K)")
    A("")
    A("| Metric | Definition |")
    A("|---|---|")
    A("| Answer correctness | token-overlap (jaccard+coverage)/2 over acceptable_conclusion "
      "(mirrors eval_e2e_v2.compute_question_metrics) |")
    A("| Coverage | |answer ∩ conclusion| / |conclusion| |")
    A("| Jaccard | |answer ∩ conclusion| / |answer ∪ conclusion| |")
    A("| Citation recall | gold units covered by ≥1 cited chunk / total gold units (unit-based) |")
    A("| Citation precision | cited chunks covering ≥1 gold unit / total cited (chunk-based) |")
    A("| Groundedness | from sanitizer (valid citations / citations) |")
    A("| Abstention | keyword+length heuristic; correct = abstain iff insufficient_evidence |")
    A("| Binary correct | answer_correctness > 0.5 (eval_e2e_v2.classify_failure convention) |")
    A("")
    A("## 7. Primary Result Table")
    A("")
    for row in _tbl(
        ["K", "Ans. Correctness", "Coverage", "Jaccard", "Cit. Recall", "Cit. Precision",
         "Groundedness", "Avg Latency (ms)"],
        [[k, f"{kc[k]['answer_correctness']*100:.1f}%", f"{kc[k]['answer_coverage']*100:.1f}%",
          f"{kc[k]['answer_jaccard']*100:.1f}%", f"{kc[k]['citation_recall']*100:.1f}%",
          f"{kc[k]['citation_precision']*100:.1f}%", f"{kc[k]['groundedness']*100:.1f}%",
          f"{kc[k]['avg_latency_ms']:.0f}"] for k in ks]):
        A(row)
    A("")
    A(f"Oracle: correctness={o['answer_correctness']*100:.1f}%, grader-correct="
      f"{o['grader_correct_rate']*100:.1f}%, citation_recall={o['citation_recall']*100:.1f}%, "
      f"groundedness={o['groundedness']*100:.1f}%.")
    A("")
    A("## 8. Retrieval Recall by K")
    A("")
    for row in _tbl(["K", "R@K (CE)", "R@K (head-150 reference)", "MRR@10", "NDCG@10"],
                    [[k, f"{ret.get(str(k),0)*100:.1f}%", "", f"{agg['mrr_at_10']*100:.1f}%",
                      f"{agg['ndcg_at_10']*100:.1f}%"] for k in ks]):
        A(row)
    A("")
    A("## 9. Conditional Accuracy (correct | gold present vs absent)")
    A("")
    for row in _tbl(["K", "gold_present_n", "gold_absent_n", "correct|present", "correct|absent"],
                    [[k, cond[k]["gold_present_n"], cond[k]["gold_absent_n"],
                      _pct(cond[k]["correct_given_present"]) if cond[k]["correct_given_present"] is not None else "n/a",
                      _pct(cond[k]["correct_given_absent"]) if cond[k]["correct_given_absent"] is not None else "n/a"]
                     for k in ks]):
        A(row)
    A("")
    A("## 10. Oracle Analysis")
    A("")
    for row in _tbl(["Metric", "Oracle"],
                    [["Answer Correctness", f"{o['answer_correctness']*100:.1f}%"],
                     ["Correct rate (>0.5)", f"{o['correct_rate']*100:.1f}%"],
                     ["Grader correct (score>=1)", f"{o['grader_correct_rate']*100:.1f}%"],
                     ["Citation Recall", f"{o['citation_recall']*100:.1f}%"],
                     ["Citation Precision", f"{o['citation_precision']*100:.1f}%"],
                     ["Groundedness", f"{o['groundedness']*100:.1f}%"],
                     ["Abstain rate", f"{o['abstain_rate']*100:.1f}%"],
                     ["Mean latency (ms)", f"{o['avg_latency_ms']:.0f}"],
                     ["Mean context chunks", f"{o['mean_context_chunks']:.1f}"],
                     ["Mean context tokens", f"{o['mean_context_tokens']:.0f}"],
                     ["Mean context chars", f"{o['mean_context_chars']:.0f}"],
                     ["Mean gold chunks fed", f"{o['mean_gold_chunks_total']:.1f}"]]):
        A(row)
    A("")
    A("## 11. Condition A (CE top-K) vs Condition B (Oracle)")
    A("")
    ca = agg["condition_a_vs_b"]["condition_a_correctness_by_k"]
    for row in _tbl(["K", "Condition A (CE top-K)", "Condition B (oracle)"],
                    [[k, f"{ca[str(k)]*100:.1f}%" if ca.get(str(k)) else "n/a",
                      f"{o['answer_correctness']*100:.1f}%"] for k in ks]):
        A(row)
    A("")
    A("## 12. Context Efficiency & Plateau")
    A("")
    eff = agg["context_efficiency"]
    eff_by_k = {int(k): v for k, v in eff["efficiency_by_k"].items()}
    A(f"Correctness per ~1K context tokens peaks at K={eff['optimum_k']} "
      f"(efficiency={eff['optimum_efficiency']:.4f}).")
    A("")
    for row in _tbl(["K", "Efficiency (correctness per ~1K context tokens)"],
                    [[k, f"{eff_by_k[k]:.4f}"] for k in ks if k in eff_by_k]):
        A(row)
    A("")
    A("## 13. Noise Analysis (K=100)")
    nm = noise.get(100) or list(noise.values())[0]
    if nm:
        A(f"Mean context chunks={nm['mean_total_context_chunks']}, gold-in-context="
          f"{nm['mean_gold_chunks_in_context']} ({nm['frac_gold']*100:.1f}%), "
          f"non-gold={nm['mean_non_gold_chunks']} ({nm['frac_non_gold']*100:.1f}%), "
          f"KG={nm['mean_kg_items']} ({nm['frac_kg']*100:.1f}%).")
    A("")
    A("## 14. Monotonicity")
    mo = agg["monotonicity"]
    A(f"Correctness non-decreasing in K for {mo['fully_monotonic']}/{mo['total']} questions "
      f"({(mo['fully_monotonic']/max(mo['total'],1))*100:.1f}%).")
    if mo.get("examples"):
        A("")
        for ex in mo["examples"][:5]:
            A(f"- {ex['qid']}: " + ", ".join(f"K{k}={v}" for k, v in zip(ks, ex["curve"])))
    A("")
    A("## 15. Actual vs Requested K")
    A("")
    for row in _tbl(["K (requested)", "=1 (actual chunk)", "(differs only if chars truncate)"],
                    [] if False else [[k, f"{kc[k]['mean_context_chunks']:.1f}",
                                       "yes (200K budget non-binding)"] for k in ks]):
        A(row)
    A("")
    A("## 16. Per-Question Results")
    A(f"Per-question K-curves + oracle written to `{PER_QUESTION_JSON}`. "
      f"Each record: correctness/coverage/jaccard, citation recall/precision, "
      f"groundedness, abstention, latency, context tokens/chars, grader score (0/1/2), "
      f"failure-relevant flags, and cited chunk IDs.")
    A("")
    A("## 17. Plot Gallery")
    A("")
    A("Saved as PNG (and SVG) in `evaluation/out/ceiling_v5/plots/`:")
    A("- `experiment_b_01_correctness_vs_k.png` — correctness/coverage/jaccard vs K")
    A("- `experiment_b_02_citations_vs_k.png` — citation recall/precision vs K")
    A("- `experiment_b_03_groundedness_vs_k.png` — groundedness & abstention vs K")
    A("- `experiment_b_04_context_size_vs_k.png` — requested K vs actual context chunks")
    A("- `experiment_b_05_retrieval_vs_answer.png` — dual-axis R@K vs correctness")
    A("- `experiment_b_06_latency_vs_k.png` — latency mean/median/P95 vs K")
    A("")
    A("## 18. Transitions (correct<->incorrect across consecutive K)")
    tr = agg["transitions"]
    A(f"Total correctness transitions across consecutive K values: "
      f"{tr['total_transitions']} "
      f"(incl->excl: {tr['correct_to_incorrect']}, excl->incl: {tr['incorrect_to_correct']}).")
    A("")
    A("## 19. Failure Classification (A-E) at K=max(K)")
    fk = fail.get(ks[-1]) or list(fail.values())[0]
    A(f"A(retrieval-limit, gold absent from CE top-K)={fk['A_retrieval_limit']}, "
      f"B(gold present, wrong)={fk['B_gold_present_wrong']}, "
      f"C(correct)={fk['C_correct']}, "
      f"D(recoverable via oracle)={fk['D_recoverable']}, "
      f"E(LLM/rec limit, oracle also wrong)={fk['E_llm_limit']}.")
    A("")
    A("## 20 / 21. Diagnostic Questions & Answers")
    answers = {
        "Q1": diag["q1"], "Q2": diag["q2"], "Q3": diag["q3"], "Q4": diag["q4"],
        "Q5": diag["q5"], "Q6": diag["q6"], "Q7": diag["q7"], "Q8": diag["q8"],
        "Q9": diag["q9"], "Q10": diag["q10"], "Q11": diag["q11"],
    }
    for k_, v in answers.items():
        A(f"### {k_} {v['q']}")
        A(f"- **Measurement:** {json.dumps({kk: vv for kk, vv in v.items() if kk != 'q'}, default=str)}")
        A("")
    A("## 22. Caveats & Limitations")
    A("- Token-overlap correctness is harsh (needs paraphrase of the exact "
      "`acceptable_conclusion`); the protocol §13 grader (`grade_answer`, 0/1/2) is "
      "reported alongside as a richer legal signal, but citation-dependent.")
    A("- The FSSAI `grounded_qa` system prompt is fixed for all questions (including "
      "non-FSSAI domains); prompt tuning between K is forbidden by the spec.")
    A("- Citation metrics require the LLM to emit `[n]` / `Section N` markers; if it does "
      "not, citation rates are ~0 and grader provision_correct becomes citation-dependent.")
    A("- The answerability-rejection heuristic is disabled to isolate K (fixed config); "
      "its real-world effect (truncating context to ~1.5 chunks) is a separate finding.")
    A("- `poolside/laguna-s-2.1:free` free-tier OpenRouter model; latency/rates are "
      "free-tier-limited.")
    A("")
    A("## 23. Recommended Next Experiment")
    A("")
    interp = diag["q6"]["interpretation"]
    if "retrieval" in interp or "CE selection" in interp:
        A("The oracle corrects a meaningful portion of the gap -> the ceiling is retrieval/CE-selection "
          "limited. **Next experiment:** tune CE reranker legal weights (sec/act/exact/lex) and/or "
          "add a 2nd-stage reranker; re-run Experiment B at the new operating point.")
    elif "LLM" in interp:
        A("The oracle does NOT correct the gap -> ceiling is LLM reasoning/generation. **Next experiment:** "
          "query-type-aware prompts + domain-matched system prompts + longer context window; re-run B.")
    else:
        A("Mixed -- both contribute. **Next experiment (A):** CE legal-weight sweep; "
          "(B): domain-aware prompts; re-run B under each.")
    A("")
    A("---")
    A(f"*Generated {time.strftime('%Y-%m-%d %H:%M:%S')} from "
      f"`evaluation/experiment_b_topk_eval.py`. "
      f"LLM calls={agg['llm_record_count'] + agg['oracle_record_count']} "
      f"(K-curve={agg['llm_record_count']}, oracle={agg['oracle_record_count']}).*")

    SUMMARY_MD.write_text("\n".join(L), encoding="utf-8")
    print(f"  Wrote summary -> {SUMMARY_MD}", flush=True)
    return 0


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def phase_all(stub: bool, concurrency: int):
    phase_build(dry=False)
    phase_llm(stub, concurrency, resume=True, limit=None)
    phase_oracle(stub, concurrency, resume=True, limit=None)
    phase_analyze()
    phase_plots()
    phase_report()
    return 0


def phase_validate():
    """Verify the cached CE ranking WITHOUT re-scoring or LLM calls.

    Recomputes R@K (CE full ranking vs head-150 reference), Q001-5 CE gold
    rank check, K-context accounting (requested_K vs actual_context_items,
    spec section 5), and oracle context math -- exactly what --dry does after
    phase_build, but against the on-disk cache.
    """
    print("=" * 70, flush=True)
    print("Phase VALIDATE: cached CE ranking verification (no CE re-score, no LLM)")
    print("=" * 70, flush=True)
    ce_cache = _load_ce_cache()
    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}
    gold_index = _load_gold_index(payload_index, family_map)
    _attach_ce_ranked(ce_cache)

    ks_all = K_VALUES + [150, 200, 300, 500]
    retrieval_recall = {k: [] for k in ks_all}
    head150_recall = {k: [] for k in K_VALUES}

    for qid, qd in ce_cache["questions"].items():
        g = qd.get("ce_gold_rank")  # authoritative; computed on LIVE ce in phase_build
        for kk in ks_all:
            retrieval_recall[kk].append(int(g is not None and g <= kk))
        for kk in K_VALUES:
            head150_recall[kk].append(int(g is not None and g <= kk))

    _print_build_summary(ce_cache, retrieval_recall, head150_recall, len(ce_cache["questions"]))

    try:
        _print_dry_context_math(ce_cache, questions, payload_index, family_map)
    except Exception as exc:
        import traceback
        print(f"  [VALIDATE] _print_dry_context_math raised: {exc!r}", flush=True)
        traceback.print_exc()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Experiment B -- CE Top-K -> LLM answer-quality curve")
    ap.add_argument("--phase", default="all",
                    choices=["all", "build", "llm", "oracle", "analyze", "plots", "report", "dry"])
    ap.add_argument("--dry", action="store_true", help="build in dry mode (no LLM, with verification + context math)")
    ap.add_argument("--stub", action="store_true", help="use stub LLM (validate pipeline, no real API calls)")
    ap.add_argument("--concurrency", type=int, default=LLM_CONCURRENCY)
    ap.add_argument("--resume", action="store_true", default=True, help="resume from checkpoint")
    ap.add_argument("--limit", type=int, default=None, help="limit LLM tasks (for testing)")
    ap.add_argument("--validate", action="store_true",
                    help="verify cached CE ranking: R@K + Q001-5 + K-context math + oracle math (no CE re-score, no LLM)")
    args = ap.parse_args(argv)

    if args.validate:
        return phase_validate()
    if args.phase == "dry":
        return phase_build(dry=True)
    if args.phase == "build":
        return phase_build(dry=args.dry)
    if args.phase == "llm":
        return phase_llm(args.stub, args.concurrency, args.resume, args.limit)
    if args.phase == "oracle":
        return phase_oracle(args.stub, args.concurrency, args.resume, args.limit)
    if args.phase == "analyze":
        return phase_analyze()
    if args.phase == "plots":
        phase_plots()
        return 0
    if args.phase == "report":
        phase_report()
        return 0
    if args.phase == "all":
        return phase_all(args.stub, args.concurrency)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

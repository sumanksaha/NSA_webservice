"""
Experiment B — 400-call Stratified Top-K Evaluation.

Objective:
    Determine whether increasing the amount of CE v2_K500-ranked context
    supplied to the LLM improves final RAG answer quality.

Pipeline (READ-ONLY — uses cached retrieval arms, no live Qdrant/Neo4j):
    For each of 100 stratified questions:
        K=10, K=20, K=50, K=100
            -> Build CE-ranked top-150 (same as Experiment A)
            -> Take top-K chunks
            -> Call LLM via GroundedGenerationService
            -> Compute metrics (answer correctness, context recall, citation
               recall, groundedness, abstention, latency, token usage)

Total LLM calls: 100 x 4 = 400 (exactly the budget).

Usage:
    python -u -m evaluation.experiment_b_topk_eval            # run
    python -u -m evaluation.experiment_b_topk_eval --dry    # verify without LLM
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
os.environ["RAG_USE_STUB_LLM"] = "false"

import torch
torch.set_num_threads(4)

from evaluation.benchmark import load_questions
from evaluation.config import CACHE_DIR
from evaluation.resolution import FamilyMap, matches_gold
from evaluation.rerank_legal import build_pool, rerank, rrf_scores, rank_of
from evaluation.metrics import RankedItem, _kg_item_keys, item_covers
from app.rag.generation.llm_client import GroundedLLMClient, GroundedLLMResponse
from app.rag.generation.grounded_service import GroundedGenerationService
from app.rag.generation.context_builder import ContextBuilder, BuiltContext
from app.rag.retrieval.result import RetrievedChunk, RAGResponse, Citation
from sentence_transformers import CrossEncoder

# ─── Config (mirrors Experiment A / eval_v2_checkpoint.py exactly) ─────────
MODELS_DIR = PROJECT_ROOT / "evaluation" / "out" / "models"
CE_V2 = MODELS_DIR / "legal_ce_v2_K500"
POOL_HEAD = 150
CE_BATCH = 64
SLICE_DEPTH = 500
KG_SLICE = 500
RRF_K = 60.0
LLM_TEMP = 0.1
LLM_MAX_TOKENS = 1024
MAX_CONCURRENCY = 4  # conservative to avoid rate limits

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
RAW_DIR = OUT_DIR / "raw"
EXP_A_JSON = OUT_DIR / "experiment_a_gold_rank.json"
EXP_B_QIDS_JSON = OUT_DIR / "experiment_B_question_ids.json"
EXP_B_RESULTS_JSON = OUT_DIR / "experiment_B_results.json"
EXP_B_REPORT_MD = OUT_DIR / "experiment_B_report.md"

K_VALUES = [10, 20, 50, 100]


# ─── SSL-bypass LLM client (copied from eval_e2e_v2.py pattern) ───────────
class _SSLBypassLLMClient(GroundedLLMClient):
    """GroundedLLMClient with SSL verification disabled (corporate proxy)."""

    def _real_call(self, system_prompt, user_prompt, *, temperature, max_tokens, **extra):
        import httpx

        start = time.perf_counter()
        url = self._base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nsa-webservice.local",
            "X-Title": "NSA Webservice Experiment B",
        }
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        body.update(extra)
        last_exc = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=60.0, verify=False) as client:
                    resp = client.post(url, headers=headers, json=body)
                    resp.raise_for_status()
                    data = resp.json()
                    choice = data["choices"][0]
                    message = choice.get("message", {})
                    text = message.get("content")
                    if text is None:
                        text = message.get("reasoning") or ""
                    usage = data.get("usage", {})
                    latency = time.perf_counter() - start
                    return GroundedLLMResponse(
                        text=text,
                        model=self.model,
                        usage={
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                            "total_tokens": usage.get("total_tokens", 0),
                        },
                        latency=latency,
                    )
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    break
        latency = time.perf_counter() - start
        return GroundedLLMResponse(
            error=f"LLM request failed after 3 attempts: {last_exc}",
            model=self.model,
            latency=latency,
        )


# ─── Context builder that allows K chunks (bypasses answerability check) ──
class _ExpBContextBuilder(ContextBuilder):
    """ContextBuilder that unconditionally includes all K chunks."""

    def __init__(self, k: int):
        super().__init__(max_context_chars=200_000, max_chunks=k, query_type="")

    def _check_answerability(self, query, chunks, query_type):
        return True, []


# ─── Helpers ────────────────────────────────────────────────────────────────

def load_payload_index() -> dict[str, dict]:
    cache_file = CACHE_DIR / "payload_index.jsonl"
    index: dict[str, dict] = {}
    with open(cache_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            index[rec["id"]] = rec["payload"]
    return index


def load_raw(arm: str) -> dict[str, dict]:
    recs: dict[str, dict] = {}
    p = RAW_DIR / f"{arm}.jsonl"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    recs[r["question_id"]] = r
    return recs


def load_jsonl_cached(path: Path) -> dict[str, dict]:
    recs = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    recs[r["question_id"]] = r
    return recs


def find_all_gold_chunk_ids(question, payload_index, family_map) -> set[str]:
    """Find ALL payload chunk IDs matching ANY gold unit (recall_units)."""
    gold_ids: set[str] = set()
    for unit in question.recall_units():
        for pid, payload in payload_index.items():
            if matches_gold(payload, unit, family_map):
                gold_ids.add(pid)
    return gold_ids


def score_pool_with_scores(items: list[dict], query: str, ce) -> list[dict]:
    """Score pool with CE and add ce_score to each item."""
    pairs = [
        (query, str(it["payload"].get("chunk_text") or it["payload"].get("text") or ""))
        for it in items
    ]
    if not pairs:
        return items
    scores = ce.predict(pairs, batch_size=CE_BATCH)
    scored = sorted(zip(scores, items, strict=False), key=lambda x: float(x[0]), reverse=True)
    result = []
    for s, it in scored:
        item = dict(it)
        item["ce_score"] = float(s)
        result.append(item)
    return result


def compute_answer_correctness(answer: str, expected: str) -> dict:
    """Jaccard + coverage token overlap (same as eval_e2e_v2.py)."""
    def tokens(text):
        return set(re.findall(r"[a-z0-9]+", text.lower()))
    ans_toks = tokens(answer)
    exp_toks = tokens(expected)
    if ans_toks and exp_toks:
        union = ans_toks | exp_toks
        jaccard = len(ans_toks & exp_toks) / len(union)
        coverage = len(exp_toks & ans_toks) / len(exp_toks)
    else:
        jaccard = 0.0
        coverage = 0.0
    accuracy = (jaccard + coverage) / 2
    return {
        "answer_correctness": round(accuracy, 4),
        "answer_jaccard": round(jaccard, 4),
        "answer_coverage": round(coverage, 4),
    }


def ce_ranked_to_chunks(ce_ranked: list[dict], k: int) -> list[RetrievedChunk]:
    """Convert top-K CE-ranked items to RetrievedChunk objects."""
    chunks = []
    for it in ce_ranked[:k]:
        if it["kind"] != "chunk":
            continue
        payload = it["payload"]
        chunks.append(RetrievedChunk(
            chunk_id=it["key"],
            score=float(it.get("ce_score", 0.0)),
            text=str(payload.get("chunk_text") or payload.get("text") or ""),
            section_number=str(payload.get("section_number")) if payload.get("section_number") else None,
            clause_number=None,
            document_title=payload.get("document_title", ""),
            act_name=payload.get("act_name", ""),
            document_type=payload.get("document_type", ""),
            authority=payload.get("authority", ""),
            chunk_index=payload.get("chunk_index", 0),
            hierarchy_level=payload.get("hierarchy_level", 0),
            parent_chunk_id=payload.get("parent_chunk_id"),
        ))
    return chunks


def generate_answer(query: str, chunks: list[RetrievedChunk], query_type: str, k: int,
                     llm_client: GroundedLLMClient) -> RAGResponse:
    """Generate answer using GroundedGenerationService with K-chunk context."""
    cb = _ExpBContextBuilder(k=k)
    service = GroundedGenerationService(llm_client=llm_client, context_builder=cb)
    return service.generate(query, chunks, query_type=query_type or "general_qa")


# ─── Main ───────────────────────────────────────────────────────────────────

def main(dry_run: bool = False) -> int:
    print("=" * 80, flush=True)
    print("EXPERIMENT B - 400-call Stratified Top-K Evaluation", flush=True)
    print("CE v2_K500 | 100 stratified questions | K=[10,20,50,100] | 400 LLM calls", flush=True)
    print("=" * 80, flush=True)

    # ─── 1. Load cached data ───────────────────────────────────────────────
    print("\n[1/7] Loading cached data...", flush=True)
    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}

    dense = load_raw("A_dense")
    sparse = load_raw("B_sparse")
    kg = load_raw("D_kg")
    ident = load_jsonl_cached(CACHE_DIR / "v55_ident" / "sparse_identifier.jsonl")
    if not ident:
        ident = load_jsonl_cached(CACHE_DIR / "sparse_identifier.jsonl")

    print(f"  payload_index={len(payload_index)}, questions={len(questions)}", flush=True)
    print(f"  dense={len(dense)}, sparse={len(sparse)}, kg={len(kg)}, ident={len(ident)}", flush=True)

    # ─── 2. Load stratified question IDs ────────────────────────────────────
    print("\n[2/7] Loading stratified question IDs...", flush=True)
    with open(EXP_B_QIDS_JSON) as f:
        qid_data = json.load(f)
    sampled_qids = qid_data["question_ids"]
    final_dist = qid_data["final_distribution"]
    print(f"  Sampled: {len(sampled_qids)} questions", flush=True)
    print(f"  Distribution: {final_dist}", flush=True)
    print(f"  Call budget: {qid_data['call_budget']['total_calls']} (max 400)", flush=True)
    assert qid_data["call_budget"]["exactly_400"], "Budget must be exactly 400!"

    # ─── 3. Load CE model ──────────────────────────────────────────────────
    print("\n[3/7] Loading CE v2_K500 model...", flush=True)
    ce = CrossEncoder(CE_V2.as_posix(), max_length=256)
    print("  Model loaded.", flush=True)

    # ─── 4. Build CE-ranked pools for all 100 questions ────────────────────
    print("\n[4/7] Building CE-ranked pools for 100 questions...", flush=True)
    ce_ranked_by_qid: dict[str, list[dict]] = {}
    gold_chunk_ids_by_qid: dict[str, set[str]] = {}
    ce_gold_ranks_by_qid: dict[str, int | None] = {}

    n_q = len(sampled_qids)
    for i, qid in enumerate(sorted(sampled_qids, key=lambda x: int(x[1:])), 1):
        q = questions[qid]
        d, s, k = dense.get(qid), sparse.get(qid), kg.get(qid)
        if not (d and s and k):
            continue

        # Build union pool (same config as Experiment A)
        pool = build_pool(d, s, k, payload_index, family_map,
                          slice_depth=SLICE_DEPTH, kg_slice=KG_SLICE)
        if not pool:
            continue

        # RRF (3-arm or 4-arm with identifier)
        rrf = rrf_scores([
            [{"key": c} for c in d.get("chunk_ids", [])[:SLICE_DEPTH]],
            [{"key": c} for c in s.get("chunk_ids", [])[:SLICE_DEPTH]],
            [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:200]],
        ])
        rec = ident.get(qid, {})
        ids = [str(c) for c in rec.get("chunk_ids", [])[:SLICE_DEPTH]]
        if ids:
            rrf = rrf_scores([
                [{"key": c} for c in d.get("chunk_ids", [])[:SLICE_DEPTH]],
                [{"key": c} for c in s.get("chunk_ids", [])[:SLICE_DEPTH]],
                [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:200]],
                [{"key": c} for c in ids],
            ])

        # RRF-ranked (pure RRF base)
        rrf_ranked = rerank(pool, q.question, family_map, rrf,
                            {"sec": 0.0, "act": 0.0, "exact": 0.0, "lex": 0.0})

        # CE rerank top-150
        ce_ranked = score_pool_with_scores(rrf_ranked[:POOL_HEAD], q.question, ce)
        ce_ranked_by_qid[qid] = ce_ranked

        # Gold chunk IDs (ALL matching payloads, correct approach)
        gold_chunk_ids_by_qid[qid] = find_all_gold_chunk_ids(q, payload_index, family_map)

        # CE gold rank (from Experiment A or recompute)
        best_ce = None
        for unit in q.recall_units():
            r = rank_of(ce_ranked, unit, payload_index, family_map)
            if r is not None:
                best_ce = min(best_ce, r) if best_ce else r
        ce_gold_ranks_by_qid[qid] = best_ce

        if i % 20 == 0 or i == n_q:
            print(f"  {i}/{n_q} pools built + CE scored", flush=True)

    # ─── Verify we have all 100 questions ─────────────────────────────────
    missing_qids = [qid for qid in sampled_qids if qid not in ce_ranked_by_qid]
    if missing_qids:
        print(f"  WARNING: {len(missing_qids)} questions missing from CE ranking: {missing_qids}", flush=True)
    ready_qids = [qid for qid in sampled_qids if qid in ce_ranked_by_qid]
    print(f"  {len(ready_qids)}/{len(sampled_qids)} questions ready for LLM", flush=True)

    # ─── Dry run check ────────────────────────────────────────────────────
    total_calls = len(ready_qids) * len(K_VALUES)
    print(f"\n  Total LLM calls to make: {total_calls}", flush=True)
    print(f"  K values: {K_VALUES}", flush=True)
    print(f"  Concurrency: {MAX_CONCURRENCY}", flush=True)

    if dry_run:
        print("\n  [DRY RUN] Skipping LLM calls.", flush=True)
        print(f"  Ready to make {total_calls} LLM calls.", flush=True)
        return 0

    # ─── 5. Run LLM evaluation ─────────────────────────────────────────────
    print(f"\n[5/7] Running {total_calls} LLM calls (100 questions x 4 K values)...", flush=True)
    print(f"  K={K_VALUES}, concurrency={MAX_CONCURRENCY}", flush=True)

    llm_client = _SSLBypassLLMClient()
    print(f"  LLM client: stub={llm_client.use_stub}, model={llm_client.model}", flush=True)
    if llm_client.use_stub:
        print("  WARNING: Running in STUB mode — results will not be realistic!", flush=True)

    # Build task list
    tasks = []
    for qid in sorted(ready_qids, key=lambda x: int(x[1:])):
        q = questions[qid]
        ce_ranked = ce_ranked_by_qid[qid]
        gold_ids = gold_chunk_ids_by_qid[qid]
        expected = q.acceptable_conclusion or ""
        qt = q.question_types[0] if q.question_types else "general_qa"

        for k in K_VALUES:
            chunks = ce_ranked_to_chunks(ce_ranked, k)
            context_chunk_ids = [c.chunk_id for c in chunks]
            gold_in_context = bool(set(context_chunk_ids) & gold_ids) if gold_ids else False
            context_recall = len(set(context_chunk_ids) & gold_ids) / max(len(gold_ids), 1) if gold_ids else 0.0

            tasks.append({
                "qid": qid,
                "question": q.question,
                "expected": expected,
                "query_type": qt,
                "k": k,
                "chunks": chunks,
                "context_chunk_ids": context_chunk_ids,
                "gold_in_context": gold_in_context,
                "context_recall": context_recall,
                "ce_gold_rank": ce_gold_ranks_by_qid[qid],
                "n_chunks": len(chunks),
            })

    print(f"  Task list: {len(tasks)} calls prepared", flush=True)
    assert len(tasks) == 400, f"Expected 400 tasks, got {len(tasks)}"

    # Thread pool for concurrent LLM calls
    from threading import Semaphore
    sem = Semaphore(MAX_CONCURRENCY)
    call_counter = [0]

    def run_single_task(task):
        with sem:
            call_counter[0] += 1
            qid = task["qid"]
            k = task["k"]
            try:
                response = generate_answer(
                    task["question"], task["chunks"], task["query_type"], k, llm_client
                )
                result = {
                    "question_id": qid,
                    "k": k,
                    "n_context_chunks": task["n_chunks"],
                    "context_chunk_ids": task["context_chunk_ids"],
                    "gold_chunk_ids_count": len(task.get("gold_ids_set", set())),
                    "gold_in_context": task["gold_in_context"],
                    "context_recall_at_k": round(task["context_recall"], 4),
                    "ce_gold_rank": task["ce_gold_rank"],
                    "answer": response.answer,
                    "citations": [asdict(c) for c in response.citations],
                    "groundedness": response.groundedness_score,
                    "hallucination_detected": response.hallucination_detected,
                    "confidence": response.confidence,
                    "llm_model": response.llm_model,
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "llm_latency_s": round(response.total_latency_ms / 1000.0, 2),
                    "context_length_tokens": response.debug.get("context_length", 0),
                    "error": response.error if not response.success else None,
                }
                # Compute answer correctness
                acc = compute_answer_correctness(response.answer, task["expected"])
                result.update(acc)
                # Citation metrics
                cited_ids = {c["chunk_id"] for c in result["citations"]}
                gold_ids = task.get("gold_ids_set", set())
                if gold_ids:
                    result["citation_recall"] = round(len(cited_ids & gold_ids) / len(gold_ids), 4)
                    result["citation_precision"] = round(len(cited_ids & gold_ids) / max(len(cited_ids), 1), 4)
                else:
                    result["citation_recall"] = 0.0
                    result["citation_precision"] = 0.0
                result["n_citations"] = len(result["citations"])
                # Abstention check
                ans_lower = (response.answer or "").lower().strip()
                abstain_kw = ["not in the", "cannot", "unable", "no information", "insufficient"]
                abstained = len(ans_lower) < 20 or any(k in ans_lower for k in abstain_kw)
                result["abstained"] = abstained
                result["answer_length"] = len(response.answer or "")
                return result
            except Exception as e:
                return {
                    "question_id": qid,
                    "k": k,
                    "error": f"{type(e).__name__}: {e}",
                }

    # Add gold_ids_set to tasks for citation metrics
    for task in tasks:
        task["gold_ids_set"] = gold_chunk_ids_by_qid[task["qid"]]

    from dataclasses import asdict

    results = []
    completed = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
        futures = {executor.submit(run_single_task, t): t for t in tasks}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            completed += 1
            if "error" in result and "LLM request failed" in str(result.get("error", "")):
                errors += 1
            if completed % 20 == 0 or completed == len(tasks):
                print(f"  {completed}/{len(tasks)} LLM calls completed (errors: {errors})", flush=True)

    print(f"\n  All {len(tasks)} calls completed. Errors: {errors}", flush=True)

    # ─── 6. Compute aggregate metrics per K ───────────────────────────────
    print("\n[6/7] Computing aggregate metrics per K...", flush=True)

    aggregate_by_k: dict[str, dict] = {}
    for k in K_VALUES:
        k_results = [r for r in results if r.get("k") == k and "error" not in r]
        n = len(k_results)
        if n == 0:
            aggregate_by_k[f"K={k}"] = {"n": 0}
            continue

        def _mean(key):
            vals = [r[key] for r in k_results if key in r]
            return round(sum(vals) / len(vals), 4) if vals else 0.0

        def _std(key):
            vals = [r[key] for r in k_results if key in r]
            if len(vals) < 2:
                return 0.0
            m = sum(vals) / len(vals)
            return round(math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1)), 4)

        def _median(key):
            vals = sorted(r[key] for r in k_results if key in r)
            if not vals:
                return 0.0
            return round(vals[len(vals) // 2], 4)

        agg = {
            "n": n,
            "mean_answer_correctness": _mean("answer_correctness"),
            "std_answer_correctness": _std("answer_correctness"),
            "median_answer_correctness": _median("answer_correctness"),
            "mean_answer_jaccard": _mean("answer_jaccard"),
            "mean_answer_coverage": _mean("answer_coverage"),
            "mean_context_recall_at_k": _mean("context_recall_at_k"),
            "mean_citation_recall": _mean("citation_recall"),
            "mean_citation_precision": _mean("citation_precision"),
            "mean_groundedness": _mean("groundedness"),
            "mean_answer_length": round(_mean("answer_length"), 1),
            "mean_n_citations": _mean("n_citations"),
            "mean_prompt_tokens": round(_mean("prompt_tokens"), 1),
            "mean_completion_tokens": round(_mean("completion_tokens"), 1),
            "mean_llm_latency_s": _mean("llm_latency_s"),
            "total_llm_latency_s": round(sum(r.get("llm_latency_s", 0) for r in k_results), 2),
            "abstained_count": sum(1 for r in k_results if r.get("abstained")),
            "abstained_pct": round(sum(1 for r in k_results if r.get("abstained")) / n * 100, 1),
        }
        aggregate_by_k[f"K={k}"] = agg

    # ─── 7. Save results + write report ───────────────────────────────────
    print("\n[7/7] Writing results and report...", flush=True)

    output = {
        "experiment": "Experiment B - 400-call Stratified Top-K Evaluation",
        "ce_model": "legal_ce_v2_K500",
        "llm_model": os.environ.get("RAG_LLM_MODEL", "poolside/laguna-s-2.1:free"),
        "benchmark": "benchmark_v1.0.jsonl (150 questions, 100 sampled)",
        "pipeline": {
            "pool": "dense@500 | sparse@500 | KG@500 | question-ident@500, head-150 by base RRF",
            "ce_batch_size": CE_BATCH,
            "max_length": 256,
            "rrf_k": 60.0,
            "pool_head": POOL_HEAD,
            "slice_depth": SLICE_DEPTH,
            "kg_slice": KG_SLICE,
            "llm_temperature": LLM_TEMP,
            "llm_max_tokens": LLM_MAX_TOKENS,
        },
        "stratification": {
            "final_distribution": final_dist,
            "selection_method": "deterministic (sorted QIDs, first-N per group; stride for redistribution)",
            "n_questions": len(ready_qids),
            "k_values": K_VALUES,
            "total_llm_calls": len(tasks),
        },
        "aggregate_by_K": aggregate_by_k,
        "per_question": sorted(results, key=lambda r: (
            int(r["question_id"][1:]), r.get("k", 0)
        )),
    }

    EXP_B_RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    EXP_B_RESULTS_JSON.write_text(
        json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  Results: {EXP_B_RESULTS_JSON} ({EXP_B_RESULTS_JSON.stat().st_size} bytes)", flush=True)

    # ─── Console summary ──────────────────────────────────────────────────
    print("\n" + "=" * 80, flush=True)
    print("EXPERIMENT B — AGGREGATE RESULTS", flush=True)
    print("=" * 80, flush=True)
    print(f"\n{'K':>6} {'N':>4} {'AnsCorrect':>11} {'Std':>6} {'Med':>6} {'CtxRec':>7} {'CiteRec':>8} {'Ground':>7} {'Lat(s)':>6}", flush=True)
    print("-" * 75, flush=True)
    for k in K_VALUES:
        key = f"K={k}"
        if key not in aggregate_by_k:
            continue
        a = aggregate_by_k[key]
        print(f"{k:>6} {a['n']:>4} {a['mean_answer_correctness']:>11.4f} {a['std_answer_correctness']:>6.4f} "
              f"{a['median_answer_correctness']:>6.4f} {a['mean_context_recall_at_k']:>7.4f} "
              f"{a['mean_citation_recall']:>8.4f} {a['mean_groundedness']:>7.4f} {a['mean_llm_latency_s']:>6.2f}", flush=True)

    print("\n" + "=" * 80, flush=True)
    print("DONE", flush=True)
    print("=" * 80, flush=True)
    return 0


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    raise SystemExit(main(dry_run=dry))

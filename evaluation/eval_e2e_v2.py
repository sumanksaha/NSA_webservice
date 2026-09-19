"""Comprehensive end-to-end evaluation of the legal RAG pipeline.

Evaluates:
  1. Query decomposition (SubQueryDecomposer + QueryClassifier + identifier_query)
  2. Retrieval diagnostics (R@1, R@20, R@50, R@100, MRR, NDCG@10 — both CE models)
  3. LLM answer generation (GroundedGenerationService with real OpenRouter LLM)
  4. Gold-context oracle evaluation (to separate retrieval vs. LLM limitations)
  5. Failure stage classification (stages 1-6 per question)
  6. Ranked improvement areas

READ-ONLY: does not modify any pipeline code, configs, or model files.
Only writes a new JSON result file to evaluation/out/ceiling_v5/.

Usage:
    python -u -m evaluation.eval_e2e_v2 2>&1
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
from dataclasses import asdict
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=True)
os.environ["RAG_USE_STUB_LLM"] = "false"

import torch

torch.set_num_threads(4)

from evaluation.benchmark import load_questions, load_gold_registry
from evaluation.config import CACHE_DIR
from evaluation.resolution import FamilyMap, matches_gold
from evaluation.rerank_legal import build_pool, rerank, rrf_scores, rank_of
from app.rag.generation.llm_client import GroundedLLMClient
from app.rag.generation.grounded_service import GroundedGenerationService
from app.rag.retrieval.result import RetrievedChunk, RAGResponse, Citation
from app.rag.retrieval.subquery_decomposer import SubQueryDecomposer
from app.rag.retrieval.query_classifier import QueryClassifier
from app.rag.retrieval.identifier import identifier_query

from sentence_transformers import CrossEncoder

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODELS_DIR = PROJECT_ROOT / "evaluation" / "out" / "models"
CE_V1 = MODELS_DIR / "legal_ce_v1"
CE_V2 = MODELS_DIR / "legal_ce_v2_K500"
POOL_HEAD = 150
CE_BATCH = 64
SLICE_DEPTH = 500
KG_SLICE = 500
RRF_K = 60.0
CONTEXT_TOP_K = 10
LLM_TEMP = 0.1
LLM_MAX_TOKENS = 1024
MAX_LLM_CONCURRENCY = 5
RAW_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "raw"
IDENT_CACHE = PROJECT_ROOT / "evaluation" / "out" / "cache" / "v55_ident" / "sparse_identifier.jsonl"
OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "e2e_eval_v2.json"


# ---------------------------------------------------------------------------
# Cached data loaders (no Flask app needed)
# ---------------------------------------------------------------------------


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
    """Load cached raw arm results from ceiling_v5/raw."""
    p = RAW_DIR / f"{arm}.jsonl"
    recs: dict[str, dict] = {}
    if p.exists():
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    recs[r["question_id"]] = r
    return recs


def load_jsonl(path: Path) -> dict[str, dict]:
    recs = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    recs[r["question_id"]] = r
    return recs


# ---------------------------------------------------------------------------
# SSL workaround: subclass GroundedLLMClient to bypass corporate proxy cert
# ---------------------------------------------------------------------------


class _SSLBypassLLMClient(GroundedLLMClient):
    """GroundedLLMClient that disables SSL verification for httpx calls."""

    def _real_call(self, system_prompt, user_prompt, *, temperature, max_tokens, **extra):
        start = time.perf_counter()
        import httpx

        url = self._base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nsa-webservice.local",
            "X-Title": "NSA Webservice E2E Eval",
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
                    from app.rag.generation.llm_client import GroundedLLMResponse

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
                    time.sleep(2**attempt)
                else:
                    break
        latency = time.perf_counter() - start
        from app.rag.generation.llm_client import GroundedLLMResponse

        return GroundedLLMResponse(
            error=f"LLM request failed after 3 attempts: {last_exc}",
            model=self.model,
            latency=latency,
        )


def generate_answer(
    query: str,
    chunks: list[RetrievedChunk],
    query_type: str,
) -> RAGResponse:
    """Run the GroundedGenerationService pipeline for one question.

    This uses the SAME pipeline code (ContextBuilder, PromptTemplate,
    CitationTracker, ResponseSanitizer) — only the LLM client is swapped
    to handle SSL.
    """
    llm_client = _SSLBypassLLMClient()
    service = GroundedGenerationService(llm_client=llm_client)
    return service.generate(query, chunks, query_type=query_type or "general_qa")


# ---------------------------------------------------------------------------
# Pool building + CE scoring
# ---------------------------------------------------------------------------


def score_pool(items: list[dict], query: str, ce) -> list[dict]:
    """Rerank a pool of candidate items with a CrossEncoder. Returns scored items."""
    pairs = [(query, str(it["payload"].get("chunk_text") or it["payload"].get("text") or "")) for it in items]
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


def get_rank_of(pool: list[dict], unit: Any, payload_index, family_map) -> int | None:
    """First-hit rank of a gold unit in a ranked pool (1-indexed)."""
    return rank_of(pool, unit, payload_index, family_map)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def compute_question_metrics(
    rag_response: RAGResponse,
    question,
    chunks: list[RetrievedChunk],
    gold_chunk_ids: set[str],
) -> dict:
    """Compute RAGAS-style metrics + answer correctness."""
    answer = rag_response.answer or ""
    expected = question.acceptable_conclusion or ""

    # --- Answer correctness (text overlap: Jaccard + coverage) ---
    def tokens(text: str) -> set[str]:
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
    answer_correctness = (jaccard + coverage) / 2

    # --- Context recall@10 (fraction of gold chunks in top-10) ---
    context_chunk_ids = {c.chunk_id for c in chunks}
    relevant_hit = bool(context_chunk_ids & gold_chunk_ids) if gold_chunk_ids else False
    context_recall = len(context_chunk_ids & gold_chunk_ids) / max(len(gold_chunk_ids), 1) if gold_chunk_ids else 0.0

    # --- Citation recall ---
    cited_ids = {c.chunk_id for c in rag_response.citations}
    citation_recall = len(cited_ids & gold_chunk_ids) / max(len(gold_chunk_ids), 1) if gold_chunk_ids else 0.0
    citation_precision = len(cited_ids & gold_chunk_ids) / max(len(cited_ids), 1) if cited_ids else 0.0

    # --- Groundedness (from service) ---
    groundedness = rag_response.groundedness_score

    # --- Hallucination detection ---
    hallucination = rag_response.hallucination_detected

    # --- Abstention check ---
    abstain_keywords = ["not", "cannot", "unable", "no information", "insufficient", "not in the", "unable to"]
    answer_lower = answer.lower().strip()
    abstained = len(answer_lower) < 20 or any(k in answer_lower for k in abstain_keywords)
    abstain_correct = abstained if question.insufficient_evidence else (not abstained)

    # --- Latency ---
    latency = rag_response.total_latency_ms / 1000.0

    return {
        "answer_correctness": round(answer_correctness, 4),
        "answer_jaccard": round(jaccard, 4),
        "answer_coverage": round(coverage, 4),
        "context_recall_at_10": round(context_recall, 4),
        "context_relevant_hit": int(relevant_hit),
        "citation_recall": round(citation_recall, 4),
        "citation_precision": round(citation_precision, 4),
        "groundedness": round(groundedness, 4),
        "hallucination_detected": hallucination,
        "abstained": abstained,
        "abstain_correct": abstain_correct,
        "llm_latency_s": round(latency, 2),
        "answer_length": len(answer),
        "n_citations": len(rag_response.citations),
        "n_context_chunks": len(chunks),
    }


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


def classify_failure(
    question,
    pool: list[dict],
    rrf_top150: list[dict],
    ce_ranked: list[dict],
    gold_chunk_ids: set[str],
    family_map,
    payload_index,
    llm_metrics: dict,
) -> dict[str, Any]:
    """Classify the primary failure stage.

    Stage 1: Query decomposition (compound query missed)
    Stage 2: Retrieval (gold not in union pool)
    Stage 3: Fusion (gold in pool but not in RRF top-150)
    Stage 4: CE reranking (gold in top-150 but not in CE top-10)
    Stage 5: Context assembly (gold in top-10 but context truncated/incomplete)
    Stage 6: LLM generation (gold in context but answer incorrect)
    """
    rel = question.relevant_units()
    if not rel:
        return {"stage": 0, "stage_name": "No relevant units", "reason": "No gold units for this question"}

    # Gold chunk IDs from payload index
    gold_ids = gold_chunk_ids

    # Check if gold is in pool (Stage 2)
    pool_keys = {it["key"] for it in pool}
    gold_in_pool = bool(gold_ids & pool_keys) or _gold_in_pool_by_unit(rel, pool, payload_index, family_map)

    # Check if gold is in RRF top-150 (Stage 3)
    rrf_keys = {it["key"] for it in rrf_top150}
    gold_in_rrf = bool(gold_ids & rrf_keys) or _gold_in_pool_by_unit(rel, rrf_top150, payload_index, family_map)

    # Check if gold is in CE top-10 (Stage 4+5)
    ce_top10_keys = {it["key"] for it in ce_ranked[:CONTEXT_TOP_K] if it["kind"] == "chunk"}
    gold_in_ce_top10 = bool(gold_ids & ce_top10_keys)

    # If gold is in CE top-10, check LLM answer correctness (Stage 6)
    answer_ok = llm_metrics.get("answer_correctness", 0.0) > 0.5 if llm_metrics else False
    context_hit = llm_metrics.get("context_relevant_hit", 0) if llm_metrics else 0

    if not gold_in_pool:
        return {
            "stage": 2,
            "stage_name": "Retrieval / candidate generation",
            "reason": "Gold provision not found in the union candidate pool",
            **{
                "gold_in_pool": False,
                "gold_in_rrf": False,
                "gold_in_ce_top10": False,
                "context_relevant_hit": context_hit,
                "answer_correctness": llm_metrics.get("answer_correctness", 0) if llm_metrics else 0,
            },
        }
    elif not gold_in_rrf:
        return {
            "stage": 3,
            "stage_name": "Fusion",
            "reason": "Gold provision in candidate pool but lost in RRF fusion (did not reach top-150)",
            **{
                "gold_in_pool": True,
                "gold_in_rrf": False,
                "gold_in_ce_top10": False,
                "context_relevant_hit": context_hit,
                "answer_correctness": llm_metrics.get("answer_correctness", 0) if llm_metrics else 0,
            },
        }
    elif not gold_in_ce_top10:
        return {
            "stage": 4,
            "stage_name": "CE reranking",
            "reason": "Gold provision in RRF top-150 but not in CE-reranked top-10",
            **{
                "gold_in_pool": True,
                "gold_in_rrf": True,
                "gold_in_ce_top10": False,
                "context_relevant_hit": context_hit,
                "answer_correctness": llm_metrics.get("answer_correctness", 0) if llm_metrics else 0,
            },
        }
    elif not answer_ok:
        return {
            "stage": 6,
            "stage_name": "LLM generation / reasoning",
            "reason": "Gold provision in context but answer does not match expected conclusion",
            **{
                "gold_in_pool": True,
                "gold_in_rrf": True,
                "gold_in_ce_top10": True,
                "context_relevant_hit": 1,
                "answer_correctness": llm_metrics.get("answer_correctness", 0) if llm_metrics else 0,
            },
        }
    else:
        return {
            "stage": 0,
            "stage_name": "No failure",
            "reason": "Gold provision in top-10 and answer matches expected conclusion",
            **{
                "gold_in_pool": True,
                "gold_in_rrf": True,
                "gold_in_ce_top10": True,
                "context_relevant_hit": 1,
                "answer_correctness": llm_metrics.get("answer_correctness", 0) if llm_metrics else 0,
            },
        }


def _gold_in_pool_by_unit(rel_units, pool, payload_index, family_map) -> bool:
    """Check if any relevant gold unit is covered by any pool item."""
    for unit in rel_units:
        for it in pool:
            if it["kind"] == "chunk":
                if matches_gold(payload_index.get(it["key"]) or {}, unit, family_map):
                    return True
            else:
                from evaluation.metrics import RankedItem, _kg_item_keys, item_covers

                for family, section in _kg_item_keys(it.get("payload") or {}, family_map):
                    if item_covers(RankedItem(kind="kg", key=it["key"], family=family, section=section), unit):
                        return True
    return False


# ---------------------------------------------------------------------------
# Retrieval metrics (per question)
# ---------------------------------------------------------------------------


def compute_retrieval_metrics(reranked: list[dict], question, payload_index, family_map) -> dict:
    rel = question.relevant_units()
    all_units = question.recall_units()
    n_rel = len(rel)

    if n_rel == 0:
        return {
            "R@1": 0.0,
            "R@20": 0.0,
            "R@50": 0.0,
            "R@100": 0.0,
            "MRR": 0.0,
            "NDCG@10": 0.0,
            "recall_unit@1": 0.0,
            "recall_unit@20": 0.0,
            "recall_unit@50": 0.0,
            "recall_unit@100": 0.0,
            "n_gold": 0,
            "n_pool": len(reranked),
        }

    # Rank of each relevant unit
    rel_ranks = []
    for unit in rel:
        r = rank_of(reranked, unit, payload_index, family_map)
        if r is not None:
            rel_ranks.append(r)

    # Unit-level recall (fraction of relevant units found in top-K)
    def recall_unit(k):
        return len([r for r in rel_ranks if r <= k]) / n_rel

    # Any-hit recall
    def recall_any(k):
        return 1.0 if any(r <= k for r in rel_ranks) else 0.0

    mrr = 1.0 / min(rel_ranks) if rel_ranks else 0.0

    # NDCG@10
    gains_by_rank: dict[int, float] = {}
    for unit in rel:
        r = rank_of(reranked, unit, payload_index, family_map)
        if r is not None and r <= 10:
            gain = 2.0 if unit.role == "primary" else 1.0
            gains_by_rank[r] = gains_by_rank.get(r, 0.0) + gain
    ideal = sorted([2.0 if u.role == "primary" else 1.0 for u in rel], reverse=True)
    idcg = sum(ideal[i] / math.log2(i + 2) for i in range(min(10, len(ideal)))) or 1e-9
    dcg = sum(g / math.log2(r + 1) for r, g in gains_by_rank.items() if r <= 10)
    ndcg = dcg / idcg

    return {
        "R@1": recall_any(1),
        "R@20": recall_any(20),
        "R@50": recall_any(50),
        "R@100": recall_any(100),
        "recall_unit@1": recall_unit(1),
        "recall_unit@20": recall_unit(20),
        "recall_unit@50": recall_unit(50),
        "recall_unit@100": recall_unit(100),
        "MRR": mrr,
        "NDCG@10": ndcg,
        "n_gold": n_rel,
        "n_pool": len(reranked),
    }


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 80, flush=True)
    print("END-TO-END RAG PIPELINE EVALUATION", flush=True)
    print("150 questions | legal_ce_v1 vs legal_ce_v2_K500 | OpenRouter LLM", flush=True)
    print("=" * 80, flush=True)

    # ---- 1. Load cached data ----
    print("\n[1/7] Loading cached data...", flush=True)
    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}
    registry = load_gold_registry()

    dense = load_raw("A_dense")
    sparse = load_raw("B_sparse")
    kg = load_raw("D_kg")
    ident = load_jsonl(IDENT_CACHE)

    print(f"  payload_index={len(payload_index)} points", flush=True)
    print(f"  questions={len(questions)}", flush=True)
    print(f"  golden_provision_registry={len(registry)} records", flush=True)

    # ---- 2. Query decomposition analysis ----
    print("\n[2/7] Evaluating query decomposition...", flush=True)
    decomposer = SubQueryDecomposer()
    qclassifier = QueryClassifier()

    decomp_results = []
    compound_count = 0
    query_types: dict[str, int] = {}
    for q in questions.values():
        sub_queries = decomposer.decompose(q.question)
        is_compound = len(sub_queries) > 1
        if is_compound:
            compound_count += 1
        q_type = qclassifier.classify(q.question)
        qt_name = q_type.value if hasattr(q_type, "value") else str(q_type)
        query_types[qt_name] = query_types.get(qt_name, 0) + 1

        # Identifier detection
        _ident_query, ident_meta = identifier_query(q.question)

        decomp_results.append({
            "question_id": q.question_id,
            "is_compound": is_compound,
            "n_subqueries": len(sub_queries),
            "sub_queries": sub_queries[:5],  # truncated
            "query_type": qt_name,
            "identifier_form": ident_meta.get("form", "none"),
            "identifier_act": ident_meta.get("act"),
            "identifier_section": ident_meta.get("section"),
            "insufficient_evidence": q.insufficient_evidence,
            "acceptable_conclusion": q.acceptable_conclusion[:200] if q.acceptable_conclusion else "",
        })

    print(f"  Compound queries: {compound_count}/{len(questions)}", flush=True)
    print(f"  Query types: {query_types}", flush=True)

    # ---- 3. Build pools + CE scoring ----
    print("\n[3/7] Building pools and scoring with CE models...", flush=True)
    ce_models = {
        "ce_v1": CrossEncoder(CE_V1.as_posix(), max_length=256),
        "ce_v2_K500": CrossEncoder(CE_V2.as_posix(), max_length=256),
    }
    print("  CE models loaded.", flush=True)

    # Per-question data: pools, RRF, CE reranked lists, gold chunks
    all_q_data: dict[str, dict] = {}
    retrieval_per_q: dict[str, dict[str, dict]] = {}  # model -> qid -> metrics

    n_q = len(questions)
    for i, (qid, q) in enumerate(sorted(questions.items()), 1):
        d, s, k = dense.get(qid), sparse.get(qid), kg.get(qid)
        if not (d and s and k):
            continue

        # Build union pool
        pool = build_pool(d, s, k, payload_index, family_map, slice_depth=SLICE_DEPTH, kg_slice=KG_SLICE)
        if not pool:
            continue

        # RRF scores
        rrf = rrf_scores([
            [{"key": c} for c in d.get("chunk_ids", [])[:SLICE_DEPTH]],
            [{"key": c} for c in s.get("chunk_ids", [])[:SLICE_DEPTH]],
            [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:KG_SLICE]],
        ])
        rec = ident.get(qid, {})
        ids = [str(c) for c in rec.get("chunk_ids", [])[:SLICE_DEPTH]]
        if ids:
            rrf = rrf_scores([
                [{"key": c} for c in d.get("chunk_ids", [])[:SLICE_DEPTH]],
                [{"key": c} for c in s.get("chunk_ids", [])[:SLICE_DEPTH]],
                [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:KG_SLICE]],
                [{"key": c} for c in ids],
            ])

        weights = {"sec": 0.0, "act": 0.0, "exact": 0.0, "lex": 0.0}
        base_ranked = rerank(pool, q.question, family_map, rrf, weights)
        rrf_top150 = base_ranked[:POOL_HEAD]

        # Gold chunks
        gold_chunks = []
        gold_chunk_ids = set()
        for unit in q.relevant_units():
            for pid, payload in payload_index.items():
                if matches_gold(payload, unit, family_map):
                    gold_chunks.append({"chunk_id": pid, "payload": payload})
                    gold_chunk_ids.add(pid)
                    break

        # CE rerank with both models
        ce_results: dict[str, list[dict]] = {}
        ce_metrics: dict[str, dict] = {}
        for mname, ce in ce_models.items():
            reranked = score_pool(rrf_top150, q.question, ce)
            ce_results[mname] = reranked
            ce_metrics[mname] = compute_retrieval_metrics(reranked, q, payload_index, family_map)

        all_q_data[qid] = {
            "question": q,
            "pool": pool,
            "rrf_top150": rrf_top150,
            "ce_ranked": ce_results,
            "gold_chunks": gold_chunks,
            "gold_chunk_ids": gold_chunk_ids,
        }
        for mname in ce_metrics:
            retrieval_per_q.setdefault(mname, {})[qid] = ce_metrics[mname]

        if i % 10 == 0 or i == n_q:
            print(f"  {i}/{n_q} pools + CE scored", flush=True)

    # ---- 4. Aggregate retrieval metrics ----
    print("\n[4/7] Aggregating retrieval metrics...", flush=True)
    retrieval_agg: dict[str, dict] = {}
    for mname in ("ce_v1", "ce_v2_K500"):
        rows = list(retrieval_per_q[mname].values())
        n = len(rows)
        if n == 0:
            continue
        m = {}
        for key in [
            "R@1",
            "R@20",
            "R@50",
            "R@100",
            "MRR",
            "NDCG@10",
            "recall_unit@1",
            "recall_unit@20",
            "recall_unit@50",
            "recall_unit@100",
        ]:
            m[key] = round(sum(r.get(key, 0) for r in rows) / n, 4)
        m["avg_n_gold"] = round(sum(r.get("n_gold", 0) for r in rows) / n, 2)
        m["n"] = n
        retrieval_agg[mname] = m

    # ---- 5. Determine gold-in-pool rate ----
    print("\n[5/7] Computing oracle analysis...", flush=True)
    gold_in_pool_count = 0
    oracle_n = 0
    for qid, q in questions.items():
        if qid not in all_q_data:
            continue
        oracle_n += 1
        data = all_q_data[qid]
        # Check if any relevant gold unit is in the pool
        rel = q.relevant_units()
        for unit in rel:
            for it in data["pool"]:
                if it["kind"] == "chunk":
                    if matches_gold(payload_index.get(it["key"]) or {}, unit, family_map):
                        gold_in_pool_count += 1
                        break
                else:
                    from evaluation.metrics import RankedItem, _kg_item_keys, item_covers

                    for fam, sec in _kg_item_keys(it.get("payload") or {}, family_map):
                        if item_covers(RankedItem(kind="kg", key=it["key"], family=fam, section=sec), unit):
                            gold_in_pool_count += 1
                            break
            else:
                continue
            break

    # Oracle gap per model (context recall @10)
    oracle_gap = {}
    for mname in ("ce_v1", "ce_v2_K500"):
        recalls = []
        for qid, q in questions.items():
            if qid not in all_q_data:
                continue
            data = all_q_data[qid]
            ce_ranked = data["ce_ranked"][mname]
            top10 = ce_ranked[:CONTEXT_TOP_K]
            top10_ids = {it["key"] for it in top10 if it["kind"] == "chunk"}
            gold_ids = data["gold_chunk_ids"]
            if gold_ids:
                recalls.append(len(top10_ids & gold_ids) / len(gold_ids))
            else:
                recalls.append(0.0)
        oracle_gap[mname] = {
            "context_recall_at_10": round(sum(recalls) / max(len(recalls), 1), 4),
            "gold_in_pool_rate": round(gold_in_pool_count / max(oracle_n, 1), 4),
            "max_context_recall_at_10": 1.0,  # Oracle always has all gold
        }

    # ---- 6. LLM answer generation ----
    print("\n[6/7] Running LLM answer generation (retrieved + gold context)...", flush=True)
    llm_results: dict[str, list[dict]] = {
        "ce_v1": [],
        "ce_v2_K500": [],
    }
    llm_oracle: dict[str, list[dict]] = {
        "ce_v1": [],
        "ce_v2_K500": [],
    }

    # Prepare tasks
    tasks = []
    for qid in sorted(all_q_data.keys()):
        q = questions[qid]
        data = all_q_data[qid]
        gold_chunks = data["gold_chunks"]

        for mname in ("ce_v1", "ce_v2_K500"):
            # Retrieved context: top-10 CE reranked chunks
            ce_ranked = data["ce_ranked"][mname]
            top_items = [it for it in ce_ranked[:CONTEXT_TOP_K] if it["kind"] == "chunk"]
            retrieved_chunks = [
                RetrievedChunk(
                    chunk_id=it["key"],
                    score=it.get("ce_score", 0.0),
                    text=str(it["payload"].get("chunk_text") or it["payload"].get("text") or ""),
                    section_number=str(it["payload"].get("section_number"))
                    if it["payload"].get("section_number")
                    else None,
                    clause_number=None,
                    document_title=it["payload"].get("document_title", ""),
                    act_name=it["payload"].get("act_name", ""),
                    document_type=it["payload"].get("document_type", ""),
                    authority=it["payload"].get("authority", ""),
                    chunk_index=it["payload"].get("chunk_index", 0),
                    hierarchy_level=it["payload"].get("hierarchy_level", 0),
                    parent_chunk_id=it["payload"].get("parent_chunk_id"),
                )
                for it in top_items
            ]
            tasks.append((qid, mname, "retrieved", retrieved_chunks, q))

            # Oracle context: gold chunks
            gold_chunk_objs = [
                RetrievedChunk(
                    chunk_id=gc["chunk_id"],
                    score=1.0,
                    text=str(gc["payload"].get("chunk_text") or gc["payload"].get("text") or ""),
                    section_number=str(gc["payload"].get("section_number"))
                    if gc["payload"].get("section_number")
                    else None,
                    clause_number=None,
                    document_title=gc["payload"].get("document_title", ""),
                    act_name=gc["payload"].get("act_name", ""),
                    document_type=gc["payload"].get("document_type", ""),
                    authority=gc["payload"].get("authority", ""),
                    chunk_index=gc["payload"].get("chunk_index", 0),
                    hierarchy_level=gc["payload"].get("hierarchy_level", 0),
                    parent_chunk_id=gc["payload"].get("parent_chunk_id"),
                )
                for gc in gold_chunks[:CONTEXT_TOP_K]
            ]
            tasks.append((qid, mname, "oracle", gold_chunk_objs, q))

    total_tasks = len(tasks)
    print(f"  {total_tasks} LLM calls ({total_tasks // 2} retrieved + {total_tasks // 2} oracle)", flush=True)

    # Semaphore for concurrency control
    from threading import Semaphore

    sem = Semaphore(MAX_LLM_CONCURRENCY)

    def run_task(task_args):
        qid, mname, mode, chunks, q = task_args
        with sem:
            try:
                qt = q.question_types[0] if q.question_types else "general_qa"
                response = generate_answer(q.question, chunks, qt)
                result = {
                    "question_id": qid,
                    "model": mname,
                    "mode": mode,
                    "query": q.question,
                    "answer": response.answer,
                    "citations": [asdict(c) for c in response.citations],
                    "groundedness_score": response.groundedness_score,
                    "hallucination_detected": response.hallucination_detected,
                    "confidence": response.confidence,
                    "llm_model": response.llm_model,
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_latency_ms": response.total_latency_ms,
                    "context_length": response.debug.get("context_length", 0),
                    "chunk_count": len(chunks),
                    "context_chunk_ids": [c.chunk_id for c in chunks],
                }
                return result
            except Exception as e:
                return {
                    "question_id": qid,
                    "model": mname,
                    "mode": mode,
                    "error": f"{type(e).__name__}: {e}",
                }

    completed = 0
    with ThreadPoolExecutor(max_workers=MAX_LLM_CONCURRENCY) as executor:
        futures = {executor.submit(run_task, t): t for t in tasks}
        for future in as_completed(futures):
            result = future.result()
            if "error" not in result:
                qid = result["question_id"]
                mname = result["model"]
                mode = result["mode"]
                data = all_q_data.get(qid, {})
                gold_ids = data.get("gold_chunk_ids", set())
                metrics = compute_question_metrics(
                    type(
                        "R",
                        (),
                        {
                            "answer": result["answer"],
                            "citations": [
                                Citation(**c) if isinstance(c, dict) else c for c in result.get("citations", [])
                            ],
                            "groundedness_score": result.get("groundedness_score", 0.0),
                            "hallucination_detected": result.get("hallucination_detected", False),
                            "confidence": result.get("confidence", 0.0),
                            "total_latency_ms": result.get("total_latency_ms", 0),
                        },
                    )(),
                    questions[qid],
                    [
                        RetrievedChunk.from_dict({"chunk_id": cid, "score": 0, "text": ""})
                        for cid in result.get("context_chunk_ids", [])
                    ],
                    gold_ids,
                )
                result["metrics"] = metrics

                if mode == "retrieved":
                    llm_results[mname].append(result)
                else:
                    llm_oracle[mname].append(result)
            else:
                llm_results["ce_v1"].append(result)  # errors go somewhere
            completed += 1
            if completed % 20 == 0 or completed == total_tasks:
                print(f"  {completed}/{total_tasks} LLM calls completed", flush=True)

    # ---- 7. Failure classification + aggregate ----
    print("\n[7/7] Classifying failures and building improvement ranking...", flush=True)

    # Build per-question LLM metrics lookup
    llm_metrics_by_q: dict[str, dict[str, dict]] = {}  # mname -> qid -> metrics
    for mname in ("ce_v1", "ce_v2_K500"):
        llm_metrics_by_q[mname] = {}
        for entry in llm_results[mname]:
            if "metrics" in entry:
                llm_metrics_by_q[mname][entry["question_id"]] = entry["metrics"]

    # Classify failures
    failure_classification: dict[str, dict] = {}
    stage_counts: dict[str, dict[str, int]] = {"ce_v1": {}, "ce_v2_K500": {}}
    stage_names = {
        0: "No failure",
        1: "Query decomposition",
        2: "Retrieval",
        3: "Fusion",
        4: "CE reranking",
        5: "Context assembly",
        6: "LLM generation",
    }

    for mname in ("ce_v1", "ce_v2_K500"):
        for qid, q in sorted(questions.items()):
            if qid not in all_q_data:
                continue
            data = all_q_data[qid]
            metrics = llm_metrics_by_q.get(mname, {}).get(qid, {})
            failure = classify_failure(
                q,
                data["pool"],
                data["rrf_top150"],
                data["ce_ranked"][mname],
                data["gold_chunk_ids"],
                family_map,
                payload_index,
                metrics,
            )
            failure_classification[f"{mname}_{qid}"] = {
                "question_id": qid,
                "failure_stage": failure["stage"],
                "failure_stage_name": failure["stage_name"],
                "details": failure,
            }
            s = failure["stage"]
            sname = stage_names.get(s, f"Stage {s}")
            stage_counts[mname][sname] = stage_counts[mname].get(sname, 0) + 1

    # Normalize stage counts
    stage_summary: dict[str, dict] = {}
    for mname in ("ce_v1", "ce_v2_K500"):
        total = sum(stage_counts[mname].values())
        stage_summary[mname] = {
            "counts": stage_counts[mname],
            "total": total,
            "percentages": {k: round(v / max(total, 1) * 100, 1) for k, v in stage_counts[mname].items()},
        }

    # LLM aggregate metrics
    llm_agg: dict[str, dict] = {}
    for mname in ("ce_v1", "ce_v2_K500"):
        for mode_name, entries in [("retrieved", llm_results[mname]), ("oracle", llm_oracle[mname])]:
            valid = [e for e in entries if "metrics" in e]
            n = len(valid)
            if n == 0:
                continue
            m: dict[str, float] = {}
            for key in [
                "answer_correctness",
                "answer_jaccard",
                "answer_coverage",
                "context_recall_at_10",
                "context_relevant_hit",
                "citation_recall",
                "citation_precision",
                "groundedness",
                "llm_latency_s",
                "answer_length",
                "n_citations",
                "n_context_chunks",
            ]:
                vals = [e["metrics"].get(key, 0) for e in valid]
                m[key] = round(sum(vals) / n, 4)
            m["abstain_correct"] = sum(1 for e in valid if e["metrics"].get("abstain_correct"))
            m["n_abstain"] = sum(1 for e in valid if e["metrics"].get("abstain_correct"))
            if sum(1 for e in valid if questions[e["question_id"]].insufficient_evidence) > 0:
                m["abstain_accuracy"] = round(
                    m["abstain_correct"] / sum(1 for e in valid if questions[e["question_id"]].insufficient_evidence), 4
                )
            m["n"] = n
            m["errors"] = sum(1 for e in entries if "error" in e)
            llm_agg[f"{mname}_{mode}"] = m

    # Max recoverable performance if each bottleneck were fixed
    max_recoverable = compute_max_recoverable(retrieval_agg, stage_summary, oracle_gap, llm_agg)

    # ---- Write results ----
    output = {
        "pipeline_config": {
            "benchmark": "benchmark_v1.0.jsonl (150 questions)",
            "ce_models": {
                "ce_v1": {"path": str(CE_V1), "description": "legal_ce_v1 fine-tuned on ~2,131 mined pairs"},
                "ce_v2_K500": {
                    "path": str(CE_V2),
                    "description": "legal_ce_v2_K500 (18,756 pairs, 3 epochs, 1,292 steps)",
                    "final_train_loss": 0.09919,
                    "best_val_loss": 0.71813,
                },
            },
            "llm": {
                "model": os.environ.get("RAG_LLM_MODEL", "poolside/laguna-s-2.1:free"),
                "provider": "OpenRouter (SSL verify=False for proxy workaround)",
                "temperature": LLM_TEMP,
                "max_tokens": LLM_MAX_TOKENS,
            },
            "context_top_k": CONTEXT_TOP_K,
            "pool_head": POOL_HEAD,
            "ce_batch": CE_BATCH,
            "slice_depth": SLICE_DEPTH,
            "kg_slice": KG_SLICE,
            "rrf_k": RRF_K,
            "notes": [
                "No live Qdrant — retrieval uses cached arm files (ceiling_v5/raw/)",
                "No live Neo4j — KG context expansion skipped (noted as limitation)",
                "SSL verify=False to work around corporate proxy self-signed certificate",
                "GroundedGenerationService used directly (same ContextBuilder/PromptTemplate/CitationTracker as pipeline)",
                "Retrieval metrics use cached dense/sparse/KG/identifier arms",
            ],
        },
        "query_decomposition": {
            "compound_queries": compound_count,
            "single_fact_queries": len(questions) - compound_count,
            "total": len(questions),
            "query_types": query_types,
            "decomposer": "SubQueryDecomposer (splits on 2+ section refs + conjunctions)",
            "per_question": decomp_results,
        },
        "retrieval_diagnostics": retrieval_agg,
        "oracle_analysis": oracle_gap,
        "llm_generation": {
            "aggregate": llm_agg,
        },
        "failure_classification": {
            "stage_summary": stage_summary,
            "stage_names": stage_names,
            "per_question": failure_classification,
        },
        "max_recoverable_performance": max_recoverable,
        "improvement_ranking": build_improvement_ranking(
            retrieval_agg, stage_summary, oracle_gap, llm_agg, max_recoverable
        ),
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults written to: {OUT_FILE}", flush=True)
    print(f"File size: {OUT_FILE.stat().st_size} bytes", flush=True)

    # Console summary
    print("\n" + "=" * 80, flush=True)
    print("SUMMARY", flush=True)
    print("=" * 80, flush=True)
    print("\n--- Retrieval Diagnostics ---", flush=True)
    print(f"{'Model':<16} {'R@1':>6} {'R@20':>6} {'R@50':>6} {'R@100':>6} {'MRR':>6} {'NDCG@10':>8}", flush=True)
    print("-" * 60, flush=True)
    for mname in ("ce_v1", "ce_v2_K500"):
        a = retrieval_agg.get(mname, {})
        print(
            f"{mname:<16} {a.get('R@1', 0):>6.4f} {a.get('R@20', 0):>6.4f} {a.get('R@50', 0):>6.4f} "
            f"{a.get('R@100', 0):>6.4f} {a.get('MRR', 0):>6.4f} {a.get('NDCG@10', 0):>8.4f}",
            flush=True,
        )

    print("\n--- LLM Generation (retrieved context) ---", flush=True)
    print(f"{'Model':<16} {'AnsCorrect':>11} {'CtxRec@10':>10} {'CiteRec':>8} {'Ground':>7} {'Latency':>7}", flush=True)
    print("-" * 60, flush=True)
    for mname in ("ce_v1", "ce_v2_K500"):
        m = llm_agg.get(f"{mname}_retrieved", {})
        print(
            f"{mname:<16} {m.get('answer_correctness', 0):>11.4f} {m.get('context_recall_at_10', 0):>10.4f} "
            f"{m.get('citation_recall', 0):>8.4f} {m.get('groundedness', 0):>7.4f} {m.get('llm_latency_s', 0):>7.2f}s",
            flush=True,
        )

    print("\n--- LLM Generation (oracle / gold context) ---", flush=True)
    for mname in ("ce_v1", "ce_v2_K500"):
        m = llm_agg.get(f"{mname}_oracle", {})
        print(
            f"{mname:<16} {m.get('answer_correctness', 0):>11.4f} {m.get('context_recall_at_10', 0):>10.4f} "
            f"{m.get('citation_recall', 0):>8.4f} {m.get('groundedness', 0):>7.4f}",
            flush=True,
        )

    print("\n--- Failure Stage Distribution ---", flush=True)
    for mname in ("ce_v1", "ce_v2_K500"):
        sc = stage_summary.get(mname, {}).get("percentages", {})
        print(f"  {mname}:", flush=True)
        for sname, pct in sorted(sc.items()):
            print(f"    {sname}: {pct}%", flush=True)

    print("\n--- vs legal_ce_v1 baseline ---", flush=True)
    v1 = retrieval_agg.get("ce_v1", {})
    v2 = retrieval_agg.get("ce_v2_K500", {})
    for metric in ["R@1", "R@20", "R@50", "R@100", "MRR", "NDCG@10", "recall_unit@1", "recall_unit@20"]:
        d = v2.get(metric, 0) - v1.get(metric, 0)
        arrow = "UP" if d > 0 else ("DOWN" if d < 0 else "=")
        print(f"  {metric:<14} v1={v1.get(metric, 0):.4f}  v2={v2.get(metric, 0):.4f}  {arrow} {d:+.4f}", flush=True)

    print("\n--- Oracle Gap ---", flush=True)
    for mname in ("ce_v1", "ce_v2_K500"):
        og = oracle_gap.get(mname, {})
        print(
            f"  {mname}: context_recall@10={og.get('context_recall_at_10', 0):.4f}, "
            f"gold_in_pool={og.get('gold_in_pool_rate', 0):.4f}, "
            f"max_context_recall@10={og.get('max_context_recall_at_10', 1):.4f}",
            flush=True,
        )

    return 0


def compute_max_recoverable(
    retrieval_agg: dict, stage_summary: dict, oracle_gap: dict, llm_agg: dict
) -> dict[str, Any]:
    """Estimate max recoverable performance if each bottleneck were fixed."""
    results: dict[str, Any] = {}
    for mname in ("ce_v1", "ce_v2_K500"):
        sc = stage_summary.get(mname, {}).get("percentages", {})
        v2_recall = retrieval_agg.get(mname, {}).get("R@20", 0)
        oracle_context_recall = oracle_gap.get(mname, {}).get("context_recall_at_10", 0)
        gold_in_pool = oracle_gap.get(mname, {}).get("gold_in_pool_rate", 0)

        # If retrieval were perfect (all gold in top-10)
        max_retrieval = 1.0
        # If CE reranking were perfect (gold always in top-10 given it's in pool)
        max_ce = gold_in_pool  # gold_in_pool is the ceiling for CE

        results[mname] = {
            "current_R@20": v2_recall,
            "if_retrieval_fixed": round(max_retrieval, 4),
            "if_ce_reranking_fixed": round(max_ce, 4),
            "if_LLM_fixed": round(1.0, 4)
            if llm_agg.get(f"{mname}_retrieved", {}).get("answer_correctness", 0) < 1.0
            else 1.0,
            "bottleneck_breakdown": sc,
        }
    return results


def build_improvement_ranking(
    retrieval_agg: dict, stage_summary: dict, oracle_gap: dict, llm_agg: dict, max_recoverable: dict
) -> list[dict]:
    """Produce a ranked list of improvement areas based on measured contribution."""
    ranking = []

    for mname in ("ce_v1", "ce_v2_K500"):
        sc = stage_summary.get(mname, {}).get("percentages", {})
        v2_recall = retrieval_agg.get(mname, {}).get("R@20", 0)
        llm_acc = llm_agg.get(f"{mname}_retrieved", {}).get("answer_correctness", 0)
        oracle_acc = llm_agg.get(f"{mname}_oracle", {}).get("answer_correctness", 0)
        oracle_gap_val = oracle_acc - llm_acc  # How much LLM improves with perfect retrieval

        # 1. Retrieval bottleneck (Stage 2)
        stage2_pct = sc.get("Retrieval", 0)
        ranking.append({
            "area": "Retrieval candidate generation (dense/sparse arm recall)",
            "model": mname,
            "measured_failure_pct": stage2_pct,
            "current_R@1": retrieval_agg.get(mname, {}).get("R@1", 0),
            "current_R@20": v2_recall,
            "ceiling_R@20": 1.0,
            "relative_improvement": round(1.0 - v2_recall, 4),
            "priority": "CRITICAL" if stage2_pct > 40 else ("HIGH" if stage2_pct > 20 else "MEDIUM"),
            "suggested_action": "Expand retrieval coverage via hybrid search tuning, query expansion, more candidates per arm",
        })

        # 2. CE reranking bottleneck (Stage 4)
        stage4_pct = sc.get("CE reranking", 0)
        ranking.append({
            "area": "CE reranking top-K selection (cross-encoder boundary at K=10)",
            "model": mname,
            "measured_failure_pct": stage4_pct,
            "current_NDCG@10": retrieval_agg.get(mname, {}).get("NDCG@10", 0),
            "oracle_context_recall_at_10": oracle_gap.get(mname, {}).get("context_recall_at_10", 0),
            "relative_improvement": round(1.0 - oracle_gap.get(mname, {}).get("context_recall_at_10", 0), 4),
            "priority": "HIGH" if stage4_pct > 30 else ("MEDIUM" if stage4_pct > 10 else "LOW"),
            "suggested_action": "Increase top-K from 10 to 20-30, or apply a second-stage re-ranker to push gold higher",
        })

        # 3. LLM generation (Stage 6)
        ranking.append({
            "area": "LLM generation / reasoning (answer correctness with gold context)",
            "model": mname,
            "measured_failure_pct": None,
            "retrieved_answer_correctness": round(llm_acc, 4),
            "oracle_answer_correctness": round(oracle_acc, 4),
            "oracle_gain": round(oracle_gap_val, 4),
            "priority": "HIGH" if oracle_gap_val > 0.1 else ("MEDIUM" if oracle_gap_val > 0.05 else "LOW"),
            "suggested_action": "Improve prompt engineering, LLM model choice, or post-processing (citation extraction, hallucination filtering)",
        })

        # 4. Fusion bottleneck (Stage 3)
        stage3_pct = sc.get("Fusion", 0)
        ranking.append({
            "area": "Fusion (RRF rank loss — gold in pool but outside top-150)",
            "model": mname,
            "measured_failure_pct": stage3_pct,
            "gold_in_pool_rate": oracle_gap.get(mname, {}).get("gold_in_pool_rate", 0),
            "priority": "HIGH" if stage3_pct > 30 else ("MEDIUM" if stage3_pct > 10 else "LOW"),
            "suggested_action": "Tune RRF weight distribution, increase pool head from 150, or use legal-feature re-ranking before CE",
        })

    # Add a v2-vs-v1 comparison summary
    v1_r20 = retrieval_agg.get("ce_v1", {}).get("R@20", 0)
    v2_r20 = retrieval_agg.get("ce_v2_K500", {}).get("R@20", 0)
    v2_improvement = v2_r20 - v1_r20
    ranking.append({
        "area": "MODEL UPGRADE SUMMARY: ce_v2_K500 vs ce_v1 baseline",
        "model": "comparison",
        "measured_failure_pct": None,
        "v1_R@20": v1_r20,
        "v2_R@20": v2_r20,
        "delta": round(v2_improvement, 4),
        "relative_gain_pct": round(v2_improvement / v1_r20 * 100, 1) if v1_r20 else None,
        "priority": "INFO",
        "suggested_action": "Further training data curation, progressive curriculum tuning, or model size increase",
    })

    # Sort by impact
    def sort_key(r):
        return (
            -(r.get("measured_failure_pct") or 0) * (1 if r["priority"] in ("CRITICAL", "HIGH") else 0.5)
            - (r.get("relative_improvement") or 0) * 0.3
        )

    ranking.sort(key=sort_key, reverse=True)

    return ranking


if __name__ == "__main__":
    raise SystemExit(main())

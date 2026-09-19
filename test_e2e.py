"""Quick test: CE scoring + LLM generation for one question."""
import sys, os, json, warnings
warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=True)
os.environ["RAG_USE_STUB_LLM"] = "false"

import torch
torch.set_num_threads(4)

from evaluation.benchmark import load_questions
from evaluation.config import CACHE_DIR
from evaluation.resolution import FamilyMap, matches_gold
from evaluation.rerank_legal import build_pool, rerank, rrf_scores, rank_of
from app.rag.generation.llm_client import GroundedLLMClient
from app.rag.generation.grounded_service import GroundedGenerationService
from app.rag.retrieval.result import RetrievedChunk
from sentence_transformers import CrossEncoder
import time

# Load data
payload_index = {}
with open(CACHE_DIR / "payload_index.jsonl", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            rec = json.loads(line)
            payload_index[rec["id"]] = rec["payload"]

questions = {q.question_id: q for q in load_questions()}
family_map = FamilyMap()

raw_dir = PROJECT_ROOT + "/evaluation/out/ceiling_v5/raw"
def load_raw(arm):
    recs = {}
    p = os.path.join(raw_dir, f"{arm}.jsonl")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    recs[r["question_id"]] = r
    return recs

ident = load_raw("v55_ident/sparse_identifier")
# Try alt path
ident = {}
p = PROJECT_ROOT + "/evaluation/out/cache/v55_ident/sparse_identifier.jsonl"
if os.path.exists(p):
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                ident[r["question_id"]] = r

dense = load_raw("A_dense")
sparse = load_raw("B_sparse")
kg = load_raw("D_kg")

print(f"Loaded: {len(payload_index)} payloads, {len(questions)} questions", flush=True)
print(f"Dense arm: {len(dense)}, Sparse arm: {len(sparse)}, KG arm: {len(kg)}, Ident: {len(ident)}", flush=True)

# Pick Q001
q = questions["Q001"]
print(f"\nQ001: {q.question[:100]}...", flush=True)
print(f"  Acceptable conclusion: {q.acceptable_conclusion[:100]}...", flush=True)

d, s, k = dense.get("Q001"), sparse.get("Q001"), kg.get("Q001")
if not (d and s and k):
    print("ERROR: Q001 missing arm data!", flush=True)
    sys.exit(1)

pool = build_pool(d, s, k, payload_index, family_map, slice_depth=500, kg_slice=500)
print(f"  Pool size: {len(pool)}", flush=True)

rrf = rrf_scores([
    [{"key": c} for c in d.get("chunk_ids", [])[:500]],
    [{"key": c} for c in s.get("chunk_ids", [])[:500]],
    [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:500]],
])
rec = ident.get("Q001", {})
ids = [str(c) for c in rec.get("chunk_ids", [])[:500]]
if ids:
    rrf = rrf_scores([
        [{"key": c} for c in d.get("chunk_ids", [])[:500]],
        [{"key": c} for c in s.get("chunk_ids", [])[:500]],
        [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:500]],
        [{"key": c} for c in ids],
    ])
print(f"  RRF scores computed: {len(rrf)} keys", flush=True)

base_ranked = rerank(pool, q.question, family_map, rrf, {"sec": 0.0, "act": 0.0, "exact": 0.0, "lex": 0.0})
rrf_top150 = base_ranked[:150]
print(f"  Top-150: {len(rrf_top150)} items", flush=True)

# Load CE v2
ce_v2 = CrossEncoder(os.path.join(PROJECT_ROOT, "evaluation/out/models/legal_ce_v2_K500"), max_length=256)
pairs = [(q.question, str(it["payload"].get("chunk_text") or it["payload"].get("text") or "")) for it in rrf_top150]
scores = ce_v2.predict(pairs, batch_size=64)
scored = sorted(zip(scores, rrf_top150), key=lambda x: float(x[0]), reverse=True)
ce_ranked = []
for s, it in scored:
    item = dict(it)
    item["ce_score"] = float(s)
    ce_ranked.append(item)
print(f"  CE reranked {len(ce_ranked)} items", flush=True)

# Top 10 chunks
top10 = [it for it in ce_ranked[:10] if it["kind"] == "chunk"]
print(f"\n  Top-10 chunks:", flush=True)
for it in top10:
    p = it["payload"]
    print(f"    key={it['key'][:40]}, ce_score={it['ce_score']:.4f}, "
          f"section={p.get('section_number')}, act={p.get('act_name', '')[:40]}", flush=True)

# Convert to RetrievedChunk
chunks = []
for it in top10:
    p = it["payload"]
    chunks.append(RetrievedChunk(
        chunk_id=it["key"],
        score=it["ce_score"],
        text=str(p.get("chunk_text") or p.get("text") or ""),
        section_number=str(p.get("section_number")) if p.get("section_number") else None,
        document_title=p.get("document_title", ""),
        act_name=p.get("act_name", ""),
        document_type=p.get("document_type", ""),
        authority=p.get("authority", ""),
        chunk_index=p.get("chunk_index", 0),
        hierarchy_level=p.get("hierarchy_level", 0),
        parent_chunk_id=p.get("parent_chunk_id"),
    ))

# Find gold chunks
gold_ids = set()
for unit in q.relevant_units():
    for pid, payload in payload_index.items():
        if matches_gold(payload, unit, family_map):
            gold_ids.add(pid)
            break

context_hit = any(c.chunk_id in gold_ids for c in chunks)
print(f"\n  Gold chunk IDs: {gold_ids}", flush=True)
print(f"  Gold in top-10 (context hit): {context_hit}", flush=True)

# Run LLM generation
print("\n  Running LLM generation...", flush=True)
qt = q.question_types[0] if q.question_types else "general_qa"
print(f"  Query type: {qt}", flush=True)

# Custom SSL-bypass LLM client
class _SSLBypassLLMClient(GroundedLLMClient):
    def _real_call(self, system_prompt, user_prompt, *, temperature, max_tokens, **extra):
        start = time.perf_counter()
        import httpx
        url = self._base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nsa-webservice.local",
            "X-Title": "NSA Webservice Test",
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
                        text=text, model=self.model,
                        usage={"prompt_tokens": usage.get("prompt_tokens", 0),
                               "completion_tokens": usage.get("completion_tokens", 0),
                               "total_tokens": usage.get("total_tokens", 0)},
                        latency=latency,
                    )
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    break
        latency = time.perf_counter() - start
        from app.rag.generation.llm_client import GroundedLLMResponse
        return GroundedLLMResponse(error=f"LLM failed after 3 attempts: {last_exc}", model=self.model, latency=latency)

llm_client = _SSLBypassLLMClient()
print(f"  LLM mode: {'stub' if llm_client.use_stub else 'LIVE'}", flush=True)

service = GroundedGenerationService(llm_client=llm_client)
response = service.generate(q.question, chunks, query_type=qt)

print(f"\n  LLM Response:", flush=True)
print(f"  Success: {response.answer != ''}", flush=True)
print(f"  Answer: {response.answer[:300]}", flush=True)
print(f"  Citations: {len(response.citations)}", flush=True)
print(f"  Groundedness: {response.groundedness_score}", flush=True)
print(f"  Hallucination: {response.hallucination_detected}", flush=True)
print(f"  Latency: {response.total_latency_ms}ms", flush=True)
print(f"  Model: {response.llm_model}", flush=True)
print(f"  Debug: {json.dumps(response.debug, indent=2)}", flush=True)

# Find gold chunks for oracle
gold_chunks = []
for unit in q.relevant_units():
    for pid, payload in payload_index.items():
        if matches_gold(payload, unit, family_map):
            gold_chunks.append(RetrievedChunk(
                chunk_id=pid, score=1.0,
                text=str(payload.get("chunk_text") or payload.get("text") or ""),
                section_number=str(payload.get("section_number")) if payload.get("section_number") else None,
                document_title=payload.get("document_title", ""),
                act_name=payload.get("act_name", ""),
                document_type=payload.get("document_type", ""),
                authority=payload.get("authority", ""),
            ))
            break

print(f"\n  Oracle context: {len(gold_chunks)} gold chunks", flush=True)
oracle_response = service.generate(q.question, gold_chunks, query_type=qt)
print(f"  Oracle answer: {oracle_response.answer[:300]}", flush=True)
print(f"  Oracle groundedness: {oracle_response.groundedness_score}", flush=True)

print("\n✓ Test completed successfully!", flush=True)

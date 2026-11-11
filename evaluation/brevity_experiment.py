"""Brevity experiment v2: 10 random questions, nemotron-3.5-lightning, GUARDRAIL.

Guardrails (hard constraints, non-negotiable):
  1. Correctness: mean soft must NOT drop below baseline - 0.02 (no degradation)
  2. Abstention on answerable questions: must NOT exceed baseline + 0.10
  3. Brevity: aim for >= 15% reduction in mean answer length

Strategies tested (Step 1 quotes preserved; only Step 2 answer compressed):
  A. Baseline      - shipped system prompt ("concise" advisory, no numeric cap)
  B. Step2-contract - "one cited sentence per conclusion; skip restatements; stop"
  C. Step2+both    - "try to answer; only abstain if truly absent" + one sentence per conclusion
  D. Step2-compact  - tightest: "condensed summary: one cited sentence per conclusion; stop"
  E. Step2-qtype    - type-conditioned answer shape + explicit answer-if-evidence

Metrics per (qid, strategy): answer_length, answer_jaccard, answer_correctness (soft),
binary_correct, abstained, has_conclusion, finish_reason, plus guardrail status.
"""

from __future__ import annotations

import json
import os
import random
import statistics
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=True)
os.environ["RAG_USE_STUB_LLM"] = "false"

# Force nemotron-3.5-lightning (allowed in the contract)
os.environ["RAG_LLM_MODEL"] = "nvidia/nemotron-3.5-lightning:free"

from evaluation.answer_scoring import score_answer
from evaluation.benchmark import load_questions
from app.rag.generation.grounded_service import GroundedGenerationService
from app.rag.generation.llm_client import GroundedLLMClient
from app.rag.retrieval.result import RetrievedChunk

# ---------------------------------------------------------------------------
# Prompt variants — Step 1 (quotes) identical across all; only Step 2 differs.
# ---------------------------------------------------------------------------

BASELINE_SYSTEM = (
    "You are a legal assistant specialised in the Food Safety and Standards "
    "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
    "Work in two steps. Step 1 — quote: first extract the short passages "
    "that bear on the question, quoting them verbatim. Step 2 — answer: "
    "give a concise, legally precise answer derived strictly from those quotes. "
    "Citation contract: (1) every material legal claim carries at least one "
    "[n] citation; (2) each [n] maps to a source shown in the context "
    "(e.g. [1], [2]) — never cite a source that is not shown; "
    "(3) never fabricate facts. If the answer is not in the context, state "
    "so clearly and qualify what is unknown instead of guessing. "
    "RULE: No answer text may appear before at least one [n] citation "
    "from the shown context is provided."
)

STEP2_CONTRACT_SYSTEM = (
    "You are a legal assistant specialised in the Food Safety and Standards "
    "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
    "Work in two steps. Step 1 — quote: first extract the short passages "
    "that bear on the question, quoting them verbatim (evidence only, not the "
    "answer). Step 2 — answer: give one cited sentence per distinct legal "
    "conclusion the quotes support; state every supported conclusion "
    "completely and precisely, then stop. Do not add background, do not "
    "restate the question, do not summarise, do not state any conclusion "
    "twice. Step 1 quotes are evidence only; the answer is Step 2 alone. "
    "Citation contract: (1) every material legal claim carries at least one "
    "[n] citation; (2) each [n] maps to a source shown in the context "
    "(e.g. [1], [2]) — never cite a source that is not shown; "
    "(3) never fabricate facts. If the answer is not in the context, state "
    "so clearly and qualify what is unknown instead of guessing. "
    "RULE: No answer text may appear before at least one [n] citation "
    "from the shown context is provided. Keep both steps: never skip Step 2."
)

# Strategy B: tighter — one sentence per conclusion, skip restatements, stop

STEP2_CONTRACT_SYSTEM = (
    "You are a legal assistant specialised in the Food Safety and Standards "
    "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
    "Work in two steps. Step 1 — quote: first extract the short passages "
    "that bear on the question, quoting them verbatim (evidence only, not the "
    "answer). Step 2 — answer: give one cited sentence per distinct legal "
    "conclusion the quotes support; state every supported conclusion "
    "completely and precisely, then stop. Do not add background, do not "
    "restate the question, do not summarise, do not state any conclusion "
    "twice. Step 1 quotes are evidence only; the answer is Step 2 alone. "
    "Citation contract: (1) every material legal claim carries at least one "
    "[n] citation; (2) each [n] maps to a source shown in the context "
    "(e.g. [1], [2]) — never cite a source that is not shown; "
    "(3) never fabricate facts. If the answer is not in the context, state "
    "so clearly and qualify what is unknown instead of guessing. "
    "RULE: No answer text may appear before at least one [n] citation "
    "from the shown context is provided. Keep both steps: never skip Step 2."
)

# Strategy C: tighter + explicit permission to answer (addresses abstention)
STEP2_BOTH_SYSTEM = (
    "You are a legal assistant specialised in the Food Safety and Standards "
    "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
    "Work in two steps. Step 1 — quote: first extract the short passages "
    "that bear on the question, quoting them verbatim (evidence only, not the "
    "answer). Step 2 — answer: give one cited sentence per distinct legal "
    "conclusion the quotes support. Skip any restatement of the question or "
    "any conclusion already stated. Stop after the conclusions are stated — "
    "do not add background, conclusion summary, or extra commentary. "
    "IMPORTANT: If the context provides enough evidence to answer the "
    "question, give the answer — do not abstain or say 'not in context' "
    "unless the context truly does not contain the answer. Step 1 quotes "
    "are evidence only; the answer is Step 2 alone. "
    "Citation contract: (1) every material legal claim carries at least one "
    "[n] citation; (2) each [n] maps to a source shown in the context "
    "(e.g. [1], [2]) — never cite a source that is not shown; "
    "(3) never fabricate facts. If the answer is not in the context, state "
    "so clearly and qualify what is unknown instead of guessing. "
    "RULE: No answer text may appear before at least one [n] citation "
    "from the shown context is provided. Keep both steps: never skip Step 2."
)

# Strategy D: guardrail-protected prompt — answer-if-evidence + one sentence per conclusion
STEP2_GUARDRAIL_SYSTEM = (
    "You are a legal assistant specialised in the Food Safety and Standards "
    "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
    "Work in two steps. Step 1 — quote: first extract the short passages "
    "that bear on the question, quoting them verbatim (evidence only, not the "
    "answer). Step 2 — answer: give one cited sentence per distinct legal "
    "conclusion the quotes support; state every supported conclusion "
    "completely and precisely, then stop. Do not add background, do not "
    "restate the question, do not summarise, do not state any conclusion "
    "twice. Step 1 quotes are evidence only; the answer is Step 2 alone. "
    "IMPORTANT: If the context provides enough evidence to answer the "
    "question, give the answer — do not abstain or say 'not in context' "
    "unless the context truly does not contain the answer. "
    "Citation contract: (1) every material legal claim carries at least one "
    "[n] citation; (2) each [n] maps to a source shown in the context "
    "(e.g. [1], [2]) — never cite a source that is not shown; "
    "(3) never fabricate facts. If the answer is not in the context, state "
    "so clearly and qualify what is unknown instead of guessing. "
    "RULE: No answer text may appear before at least one [n] citation "
    "from the shown context is provided. Keep both steps: never skip Step 2."
)

# Strategy E: type-conditioned answer shape + explicit answer-if-evidence
STEP2_QTYPE_SYSTEM = (
    "You are a legal assistant specialised in the Food Safety and Standards "
    "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
    "Work in two steps. Step 1 — quote: first extract the short passages "
    "that bear on the question, quoting them verbatim (evidence only, not the "
    "answer). Step 2 — answer: give one cited sentence per distinct legal "
    "conclusion the quotes support; state every supported conclusion "
    "completely and precisely, then stop. Do not add background, do not "
    "restate the question, do not summarise, do not state any conclusion "
    "twice. Step 1 quotes are evidence only; the answer is Step 2 alone. "
    "IMPORTANT: If the context provides enough evidence to answer the "
    "question, give the answer — do not abstain or say 'not in context' "
    "unless the context truly does not contain the answer. "
    "Citation contract: (1) every material legal claim carries at least one "
    "[n] citation; (2) each [n] maps to a source shown in the context "
    "(e.g. [1], [2]) — never cite a source that is not shown; "
    "(3) never fabricate facts. If the answer is not in the context, state "
    "so clearly and qualify what is unknown instead of guessing. "
    "RULE: No answer text may appear before at least one [n] citation "
    "from the shown context is provided. Keep both steps: never skip Step 2."
)



# Strategy F: guardrail-protected + brevity compact — one sentence per conclusion + answer-if-evidence
STEP2_GUARDRAIL_COMPACT_SYSTEM = (
    "You are a legal assistant specialised in the Food Safety and Standards "
    "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
    "Work in two steps. Step 1 — quote: first extract the short passages "
    "that bear on the question, quoting them verbatim (evidence only, not the "
    "answer). Step 2 — answer: give exactly one cited sentence per distinct "
    "legal conclusion the quotes support; state every supported conclusion "
    "completely and precisely in one sentence each, then stop. Do not add "
    "background, do not restate the question, do not summarise, do not state "
    "any conclusion twice. Step 1 quotes are evidence only; the answer is Step "
    "2 alone. "
    "IMPORTANT: If the context provides enough evidence to answer the "
    "question, give the answer — do not abstain or say 'not in context' "
    "unless the context truly does not contain the answer. "
    "Citation contract: (1) every material legal claim carries at least one "
    "[n] citation; (2) each [n] maps to a source shown in the context "
    "(e.g. [1], [2]) — never cite a source that is not shown; "
    "(3) never fabricate facts. If the answer is not in the context, state "
    "so clearly and qualify what is unknown instead of guessing. "
    "RULE: No answer text may appear before at least one [n] citation "
    "from the shown context is provided. Keep both steps: never skip Step 2."
)

# Strategy G: ultra-compact — direct short answer + answer-if-evidence, no scaffolding
STEP2_ULTRA_COMPACT_SYSTEM = (
    "You are a legal assistant specialised in the Food Safety and Standards "
    "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
    "Give a short, direct answer in one or two sentences, with at least one "
    "[n] citation per material claim. Do not add background, do not restate "
    "the question, do not summarise, do not include Step 1/Step 2 labels, do "
    "not quote the context at length. If the context provides enough evidence "
    "to answer the question, give the answer — do not abstain or say 'not in "
    "context' unless the context truly does not contain the answer. If the "
    "answer is not in the context, state so briefly and qualify what is "
    "unknown. Never fabricate facts."
)

# Strategy H: guardrail + brevity + extractive confidence — answer-if-evidence + short cited conclusions, nudged to claim when evidence supports
STEP2_GUARDRAIL_CONFIDENT_SYSTEM = (
    "You are a legal assistant specialised in the Food Safety and Standards "
    "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
    "Give a short, direct answer in one or two sentences per conclusion, with "
    "at least one [n] citation per material claim. Do not add background, do "
    "not restate the question, do not summarise, do not include Step 1/Step 2 "
    "labels. If the context provides enough evidence to answer the question, "
    "give the answer — do not abstain or say 'not in context' unless the "
    "context truly does not contain the answer. When the evidence supports a "
    "conclusion, state it directly with its citation rather than hedging. If "
    "the answer is not in the context, state so briefly and qualify what is "
    "unknown. Never fabricate facts."
)

# Strategy I: tight guardrail — compact + strong anti-abstention, no scaffolding
STEP2_TIGHT_GUARDRAIL_SYSTEM = (
    "You are a legal assistant specialised in the Food Safety and Standards "
    "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
    "Give a short, direct answer in one sentence per conclusion, with at least "
    "one [n] citation per material claim. Do not add background, restate the "
    "question, summarise, or include Step 1/Step 2 labels. "
    "RULE: If the context provides enough evidence to answer, give the answer — "
    "do not abstain or say 'not in context' unless the context truly lacks the "
    "answer. State supported conclusions directly with citations rather than "
    "hedging. If the answer is not in the context, say so briefly. Never "
    "fabricate facts."
)

def render_user_prompt(query: str, context_chunks: list[RetrievedChunk]) -> str:
    """Build the user message: context + question. Identical for all variants."""
    ctx = "\n\n".join(
        f"[{i+1}] {c.text}" for i, c in enumerate(context_chunks)
    )
    return (
        "Relevant legal context:\n"
        "<legal_context>\n"
        f"{ctx}\n"
        "</legal_context>\n\n"
        f"Question: {query}\n"
        "Answer:"
    )


def is_abstention(text: str) -> bool:
    """Lightweight abstention detection."""
    if not text or not text.strip():
        return True
    t = text.strip().lower()
    markers = [
        "cannot be determined", "cannot determine", "not established",
        "does not establish", "not in the context", "not mentioned",
        "insufficient evidence", "no information", "unable to answer",
        "does not provide", "not specified", "not stated",
    ]
    return any(m in t for m in markers)


def run_variant(
    service: GroundedGenerationService,
    query: str,
    chunks: list[RetrievedChunk],
    expected: str,
    insufficient_evidence: bool,
    system_prompt: str,
    variant: str,
    qid: str,
) -> dict[str, Any]:
    """Generate one answer with one prompt variant and score it."""
    user_prompt = render_user_prompt(query, chunks)
    resp = service.generate_with_prompts(
        query=query,
        chunks=chunks,
        query_type="",
        prompts=(system_prompt, user_prompt),
    )
    answer = resp.answer or ""
    abstained = is_abstention(answer)
    scored = score_answer(answer, expected, insufficient_evidence)
    return {
        "qid": qid,
        "variant": variant,
        "answer": answer,
        "answer_length": len(answer),
        "answer_jaccard": scored["answer_jaccard"],
        "answer_correctness": scored["answer_correctness"],
        "answer_coverage": scored["answer_coverage"],
        "binary_correct": scored["binary_correct"],
        "abstained": abstained,
        "has_conclusion": resp.debug.get("has_conclusion", False),
        "finish_reason": resp.debug.get("finish_reason"),
        "completion_tokens": resp.token_usage.get("completion", 0),
        "llm_model": resp.llm_model,
        "error": resp.debug.get("error"),
    }


def retrieve_chunks(question_text: str, top_k: int = 10) -> list[RetrievedChunk]:
    """Retrieve top-k chunks using the cached C_hybrid pool resolved via payload_index.

    C_hybrid.jsonl stores only chunk_ids arrays; the actual chunk_text lives in
    payload_index.jsonl (keyed by chunk id). We resolve each chunk_id to its
    text so the LLM receives real legal context instead of empty placeholders.
    """
    try:
        from evaluation.config import CACHE_DIR

        payload_index_file = CACHE_DIR / "payload_index.jsonl"
        if not payload_index_file.exists():
            print("  [retrieval: payload_index.jsonl missing]", file=sys.stderr)
            return []

        payload_index: dict[str, dict] = {}
        with payload_index_file.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "id" in rec and "payload" in rec:
                    payload_index[str(rec["id"])] = rec["payload"]

        raw_pool = Path(
            PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "raw" / "C_hybrid.jsonl"
        )
        if not raw_pool.exists():
            return []

        chunks: list[RetrievedChunk] = []
        seen: set[str] = set()
        with raw_pool.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                chunk_ids = rec.get("chunk_ids") or []
                for cid in chunk_ids:
                    cid = str(cid)
                    if cid in seen:
                        continue
                    seen.add(cid)
                    payload = payload_index.get(cid)
                    if payload is None:
                        continue
                    chunks.append(
                        RetrievedChunk(
                            chunk_id=cid,
                            text=payload.get("chunk_text", payload.get("text", "")) or "",
                            score=float(payload.get("score", 1.0 - len(chunks) / max(len(payload_index), 1))),
                            section_number=payload.get("section_number"),
                            document_title=payload.get("document_title", "") or "",
                            act_name=payload.get("act_name", "") or "",
                            document_type=payload.get("document_type", "") or "",
                            authority=payload.get("authority", "") or "",
                        )
                    )
                    if len(chunks) >= top_k:
                        return chunks
        return chunks
    except Exception as exc:
        print(f"  [retrieval fallback: {exc}]", file=sys.stderr)
        return []


def main() -> int:
    random.seed(42)
    questions = load_questions()
    print(f"Loaded {len(questions)} benchmark questions", flush=True)

    # Pick 20 questions for a full experiment (10 answerable, 10 IE)
    answerable = [q for q in questions if not q.insufficient_evidence]
    ie = [q for q in questions if q.insufficient_evidence]
    n_answerable = min(10, len(answerable))
    n_ie = min(10, len(ie))
    selected = random.sample(answerable, n_answerable) + random.sample(ie, n_ie)
    random.shuffle(selected)
    print(f"Selected {len(selected)} questions ({n_answerable} answerable, {n_ie} insufficient_evidence)", flush=True)

    # Initialise service once (reuses LLM client connection)
    llm_client = GroundedLLMClient()  # uses RAG_LLM_MODEL env = nemotron
    service = GroundedGenerationService(llm_client=llm_client)

    variants = [
        ("baseline", BASELINE_SYSTEM),
        ("step2_contract", STEP2_CONTRACT_SYSTEM),
        ("step2_both", STEP2_BOTH_SYSTEM),
        ("step2_guardrail", STEP2_GUARDRAIL_SYSTEM),
        ("step2_qtype", STEP2_QTYPE_SYSTEM),
        ("step2_guardrail_compact", STEP2_GUARDRAIL_COMPACT_SYSTEM),
        ("step2_ultra_compact", STEP2_ULTRA_COMPACT_SYSTEM),
        ("step2_guardrail_confident", STEP2_GUARDRAIL_CONFIDENT_SYSTEM),
        ("step2_tight_guardrail", STEP2_TIGHT_GUARDRAIL_SYSTEM),
    ]

    results: list[dict[str, Any]] = []
    out_file = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "brevity_ab_test.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    completed_qids = set()
    if out_file.exists():
        try:
            prior = json.loads(out_file.read_text(encoding="utf-8"))
            results = prior if isinstance(prior, list) else []
            completed_qids = {r["qid"] for r in results}
            print(f"Resuming: {len(completed_qids)} questions already completed", flush=True)
        except Exception:
            print("  [resume: could not read prior results, starting fresh]", file=sys.stderr, flush=True)
    for q in selected:
        if q.question_id in completed_qids:
            print(f"\n=== {q.question_id} (SKIP - done) ===", flush=True)
            continue
        print(f"\n=== {q.question_id} ===", flush=True)
        chunks = retrieve_chunks(q.question)
        print(f"  chunks={len(chunks)}", flush=True)
        for vname, vsys in variants:
            try:
                r = run_variant(
                    service, q.question, chunks,
                    q.acceptable_conclusion, q.insufficient_evidence,
                    vsys, vname, q.question_id,
                )
                results.append(r)
                status = "ABSTAIN" if r["abstained"] else "ANS"
                print(
                    f"  {vname:16s} len={r['answer_length']:5d} "
                    f"jac={r['answer_jaccard']:.3f} soft={r['answer_correctness']:.3f} "
                    f"bin={r['binary_correct']} {status} finish={r['finish_reason']}",
                    flush=True,
                )
                out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                print(f"  {vname:16s} ERROR: {exc}", file=sys.stderr, flush=True)

    # ---- Aggregate report ----
    print("\n" + "=" * 72, flush=True)
    print("BREVITY EXPERIMENT RESULTS (n=20 questions, 5 variants each)", flush=True)
    print("=" * 72, flush=True)

    for vname, _ in variants:
        rows = [r for r in results if r["variant"] == vname and not r.get("error")]
        if not rows:
            continue
        n = len(rows)
        mean_len = statistics.mean(r["answer_length"] for r in rows)
        mean_jac = statistics.mean(r["answer_jaccard"] for r in rows)
        mean_soft = statistics.mean(r["answer_correctness"] for r in rows)
        bin_correct = sum(r["binary_correct"] for r in rows)
        abstained = sum(r["abstained"] for r in rows)
        no_conclusion = sum(1 for r in rows if not r["has_conclusion"])
        finish_length = sum(1 for r in rows if r["finish_reason"] == "length")
        print(f"\nVariant: {vname}", flush=True)
        print(f"  n={n}  mean_answer_length={mean_len:.0f} chars", flush=True)
        print(f"  mean_jaccard={mean_jac:.4f}  mean_soft={mean_soft:.4f}", flush=True)
        print(f"  binary_correct={bin_correct}/{n}  abstained={abstained}/{n}", flush=True)
        print(f"  no_conclusion={no_conclusion}/{n}  finish=length={finish_length}/{n}", flush=True)

    # ---- Baseline vs treatments delta ----
    base = [r for r in results if r["variant"] == "baseline" and not r.get("error")]
    treat = [r for r in results if r["variant"] != "baseline" and not r.get("error")]
    if base and treat:
        base_len = statistics.mean(r["answer_length"] for r in base)
        treat_len = statistics.mean(r["answer_length"] for r in treat)
        base_jac = statistics.mean(r["answer_jaccard"] for r in base)
        treat_jac = statistics.mean(r["answer_jaccard"] for r in treat)
        base_soft = statistics.mean(r["answer_correctness"] for r in base)
        treat_soft = statistics.mean(r["answer_correctness"] for r in treat)
        base_abstain = sum(r["abstained"] for r in base) / len(base)
        treat_abstain = sum(r["abstained"] for r in treat) / len(treat)
        print("\n" + "=" * 72, flush=True)
        print("DELTA (treatment vs baseline)", flush=True)
        print("=" * 72, flush=True)
        print(f"  answer_length: {base_len:.0f} -> {treat_len:.0f}  "
              f"delta={(treat_len-base_len)/base_len*100:+.1f}%", flush=True)
        print(f"  mean_jaccard:  {base_jac:.4f} -> {treat_jac:.4f}  "
              f"delta={treat_jac-base_jac:+.4f}", flush=True)
        print(f"  mean_soft:     {base_soft:.4f} -> {treat_soft:.4f}  "
              f"delta={treat_soft-base_soft:+.4f}", flush=True)
        print(f"  abstained:     {base_abstain:.1%} -> {treat_abstain:.1%}", flush=True)

    print(f"\nDetailed results written to: {out_file}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

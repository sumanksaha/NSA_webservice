"""LIVE eval: Qdrant + real LLM for FBO-appeal query vs gold. Budget: 1 query."""

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path("C:/github/NSA_webservice")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

QUERY = "How can the FBO appeal to the designated officer?"
GOLD = "The FBO can appeal to the designated officer in **Form VIII**, Regulation 2.4.6."


def banner(s):
    print("\n" + "=" * 70 + f"\n{s}\n" + "=" * 70)


def main():
    t0 = time.time()
    print(f"QUERY: {QUERY}")
    print(f"GOLD : {GOLD}")
    print(
        f"env: stub={os.getenv('RAG_USE_STUB_LLM')} model={os.getenv('RAG_LLM_MODEL')} collection={os.getenv('RAG_QDRANT_COLLECTION')}",
    )
    banner("STAGE A - live Qdrant probe")
    live_ok, n_points = False, None
    try:
        from qdrant_client import QdrantClient

        c = QdrantClient(url=os.getenv("RAG_QDRANT_URL"), api_key=os.getenv("RAG_QDRANT_API_KEY"), timeout=25)
        info = c.get_collection(os.getenv("RAG_QDRANT_COLLECTION", "fssai_legal_768"))
        n_points = info.points_count
        live_ok = True
        print(f"collection points: {n_points}")
        try:
            pts, _ = c.scroll(
                os.getenv("RAG_QDRANT_COLLECTION", "fssai_legal_768"), limit=200, with_payload=True, with_vectors=False,
            )
            f8 = sum(1 for p in pts if "form viii" in json.dumps(p.payload or {}).lower())
            wf = sum(1 for p in pts if "workflow" in json.dumps(p.payload or {}).lower())
            print(f"200-pt sample: workflow_hits={wf} formVIII_hits={f8}")
        except Exception as e:
            print(f"scroll probe non-fatal fail: {type(e).__name__}: {str(e)[:200]}")
    except Exception as e:
        print(f"Qdrant UNREACHABLE (continuing): {type(e).__name__}: {str(e)[:300]}")
    print(f"live_qdrant={live_ok} points={n_points}")
    banner("STAGE B - live hybrid retrieval")
    chunks, retrieval_meta = [], {}
    if live_ok:
        try:
            from app import create_app

            app = create_app()
            ctx = app.app_context()
            ctx.push()
            try:
                from app.rag.tasks import run_retrieval_pipeline

                data = run_retrieval_pipeline(query=QUERY, top_k=5)
                chunks = data.get("chunks", [])
                retrieval_meta = {k: v for k, v in data.items() if k != "chunks"}
                print(f"retrieved {len(chunks)} chunks; meta={list(retrieval_meta.keys())[:10]}")
                for i, ch in enumerate(chunks[:5]):
                    txt = getattr(ch, "text", str(ch))[:220].replace("\n", " ")
                    print(f"  [{i}] score={getattr(ch, 'score', None)} id={getattr(ch, 'chunk_id', None)} :: {txt}...")
            finally:
                ctx.pop()
        except Exception as e:
            print(f"retrieval failed (non-fatal): {type(e).__name__}: {str(e)[:400]}")
    else:
        print("skipped (no Qdrant).")

    banner("STAGE C - real LLM generation, stub OFF in-process, 1 call")
    os.environ["RAG_USE_STUB_LLM"] = "false"
    answer, model_used = "", ""
    try:
        from app import create_app

        app = create_app()
        ctx = app.app_context()
        ctx.push()
        try:
            from app.rag.generation.grounded_service import GroundedGenerationService
            from app.rag.generation.llm_client import GroundedLLMClient

            svc = GroundedGenerationService(llm_client=GroundedLLMClient())
            t1 = time.time()
            resp = svc.generate(query=QUERY, chunks=chunks, query_type="procedure")
            dt = time.time() - t1
            answer, model_used = resp.answer, resp.llm_model
            print(f"model={model_used} latency={dt:.1f}s citations={resp.citations}")
            print(f"ANSWER:\n{answer}")
        finally:
            ctx.pop()
    except Exception as e:
        print(f"generation failed: {type(e).__name__}: {str(e)[:500]}")
    if not answer:
        print("NO ANSWER produced.")
        return
    banner("STAGE D - score vs gold + training signal")
    import app.rag.evaluation.workflow_evaluator as _we

    if not hasattr(_we, "chunk_text"):
        _we.chunk_text = lambda c: getattr(c, "text", str(c))
    _FRE = re.compile(r"\bform\s+(ii|iii|iv|v|vi|vii|viia|viii)\b", re.IGNORECASE)
    gold_forms = {m.group(1).lower() for m in _FRE.finditer(GOLD)}
    ans_forms = {m.group(1).lower() for m in _FRE.finditer(answer)}
    print(
        f"forms: gold={sorted(gold_forms)} answer={sorted(ans_forms)} missing={sorted(gold_forms - ans_forms)} extra={sorted(ans_forms - gold_forms)}",
    )
    ev = _we.evaluate_workflow_answer(QUERY, answer, GOLD, chunks=chunks)
    print(
        f"SCORES: faithfulness={ev.faithfulness.score} completeness={ev.completeness.score} citation={ev.citation_quality.score} structure={ev.structure.score} overall={ev.overall.score}",
    )
    print(f"COMPLETENESS detail: {ev.completeness.detail}")
    print(
        f"TRAINING: {json.dumps({'query': QUERY, 'type': 'live_appeal_eval', 'forms': sorted(ans_forms), 'overall': ev.overall.score, 'model': model_used})}",
    )
    print(f"\nTOTAL wall: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()

"""Evaluate query understanding + targeted retry combinations with live LLM calls."""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Semaphore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / '.env', override=True)
os.environ['RAG_USE_STUB_LLM'] = 'false'

from evaluation.answer_scoring import score_answer
from evaluation.bench_p0_1_packing import RAW_DIR, _chunks_for, _load_jsonl, _load_payload_index
from evaluation.benchmark import load_questions
from evaluation.llm_ssl_client import SSLBypassLLMClient
from evaluation.resolution import FamilyMap, matches_gold

ARM = 'C_hybrid'
BASELINE_K = 10
RETRY_K = 20
MAX_WORKERS = 2

COMBOS = [
    ('qu_off_tr_off', {'RAG_LEGAL_QUERY_TYPING': 'false', 'RAG_TARGETED_RETRY_V2': 'false'}),
    ('qu_on_tr_off',  {'RAG_LEGAL_QUERY_TYPING': 'true',  'RAG_TARGETED_RETRY_V2': 'false'}),
    ('qu_off_tr_on',  {'RAG_LEGAL_QUERY_TYPING': 'false', 'RAG_TARGETED_RETRY_V2': 'true'}),
    ('qu_on_tr_on',   {'RAG_LEGAL_QUERY_TYPING': 'true',  'RAG_TARGETED_RETRY_V2': 'true'}),
]


def _retry_chunks(qtext: str, chunks: list, payload_index: dict):
    from evaluation.ab_targeted_retry import _derive_failures, _plan_match_ids
    from app.rag.planning.targeted_retry import TargetedRetryPlanner

    baseline = chunks[:BASELINE_K]
    failures = _derive_failures(qtext, False)
    plan = TargetedRetryPlanner().target_plan(
        qtext, failures, 'general',
        {'top_k': BASELINE_K, 'chunks': [c.to_dict() for c in chunks], 'answer': ''},
    )
    additions = _plan_match_ids(plan, chunks[BASELINE_K:], payload_index)
    want = additions[: max(0, RETRY_K - len(baseline))]
    extra = [c for c in chunks[BASELINE_K:] if c.chunk_id in set(want)]
    return baseline + extra, {'arm': plan.arm, 'strategy': plan.strategy, 'plan_query': plan.query}


def _one(task, payload_index, family_map, questions, use_retry: bool, combo_env: dict):
    old = {k: os.environ.get(k) for k in combo_env}
    try:
        for k, v in combo_env.items():
            os.environ[k] = v
        from app.rag.generation.grounded_service import GroundedGenerationService

        qid, qtext, chunks = task
        q = questions[qid]
        pool = chunks[:BASELINE_K]
        if use_retry:
            pool, _ = _retry_chunks(qtext, chunks, payload_index)
        svc = GroundedGenerationService()
        svc.llm_client = SSLBypassLLMClient(model=os.environ.get('RAG_LLM_MODEL', ''))
        qt = (q.question_types or ['general'])[0] if getattr(q, 'question_types', None) else 'general'
        t0 = time.monotonic()
        built = svc.context_builder.build(qtext, pool, query_type=qt)
        sys_p, user_p = svc._render_prompt(qtext, built)
        llm = svc._call_llm(sys_p, user_p)
        answer = getattr(llm, 'text', '') or ''
        tracked = svc._extract_citations(llm, pool, built)
        san = svc.sanitizer.sanitize(answer, tracked, pool)
        gold = {c.chunk_id for c in pool if any(matches_gold(payload_index.get(c.chunk_id, {}), u, family_map) for u in q.recall_units())}
        m = score_answer(answer, q.acceptable_conclusion or '', q.insufficient_evidence)
        cited = {c.chunk_id for c in tracked}
        m['citation_recall'] = round(len(cited & gold) / max(len(gold), 1), 4) if gold else 0.0
        m['citation_precision'] = round(len(cited & gold) / max(len(cited), 1), 4) if cited else 0.0
        m['groundedness_score'] = san.groundedness_score
        m['hallucination_detected'] = int(san.hallucination_detected)
        m['latency_s'] = round(time.monotonic() - t0, 1)
        ans_low = (answer or '').strip().lower()
        is_abstain = any(k in ans_low for k in ['cannot answer', 'insufficient evidence', 'unable to answer', 'not enough information'])
        m['abstention'] = int(is_abstain)
        return {'qid': qid, 'm': m, 'llm_error': getattr(llm, 'error', None)}
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=8)
    ap.add_argument('--qids', default='')
    ap.add_argument('--workers', type=int, default=MAX_WORKERS)
    args = ap.parse_args()

    questions = {q.question_id: q for q in load_questions()}
    payload_index = _load_payload_index()
    family_map = FamilyMap()
    recs = {r['question_id']: r for r in _load_jsonl(RAW_DIR / f'{ARM}.jsonl')}

    want = []
    if args.qids:
        want = [q.strip() for q in args.qids.split(',') if q.strip()]
    else:
        try:
            s1 = json.loads((PROJECT_ROOT / 'evaluation' / 'out' / 'ceiling_v5' / 'targeted_retry_ab.json').read_text())
            want = s1.get('recovered_qids', [])[: args.limit]
        except Exception:
            want = sorted(recs)[: args.limit]

    tasks = []
    for qid in want:
        if qid not in recs or qid not in questions: 
            continue
        chunks = _chunks_for([str(c) for c in (recs[qid].get('chunk_ids') or [])], payload_index)
        if len(chunks) < 2:
            continue
        tasks.append((qid, recs[qid].get('query') or questions[qid].question, chunks))
    if not tasks:
        print('no tasks', file=sys.stderr)
        return 1

    results = {}
    gate = Semaphore(args.workers)
    model = os.environ.get('RAG_LLM_MODEL', '')

    for cname, cenv in COMBOS:
        print(f'\n=== {cname} {cenv} n={len(tasks)} ===', flush=True)

        def run(retry):
            def one(t):
                with gate:
                    return _one(t, payload_index, family_map, questions, retry, cenv)
            out = []
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs = [ex.submit(one, t) for t in tasks]
                for f in as_completed(futs):
                    out.append(f.result())
            return out

        base = run(False)
        if cenv['RAG_TARGETED_RETRY_V2'] == 'true':
            retr_res = run(True)
        else:
            retr_res = base
        results[cname] = {'base': base, 'retr': retr_res, 'env': cenv}

    out_path = PROJECT_ROOT / 'evaluation' / 'out' / 'qu_tr_combos.json'
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def aggr(lst):
        if not lst: return {}
        keys = ['binary_correct','answer_correctness','citation_recall','citation_precision','groundedness_score','hallucination_detected','abstention','latency_s']
        res = {}
        for k in keys:
            vals = [r['m'][k] for r in lst if r.get('m') and k in r['m']]
            if not vals: res[k] = 0.0
            else: res[k] = round(statistics.mean(vals), 4)
        return res

    agg = {cname: {'base': aggr(res['base']), 'retr': aggr(res['retr'])} for cname, res in results.items()}
    payload = {'model': model, 'n': len(tasks), 'qids': [t[0] for t in tasks], 'combos': results, 'aggregate': agg}
    out_path.write_text(json.dumps(payload, indent=2))
    print(f'\nwritten: {out_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

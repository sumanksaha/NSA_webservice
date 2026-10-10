"""Re-run generation on the gold-in-prompt failure set and grade correctness.

The failures that prompt conditioning can address are the ones whose gold
provision is *admitted to the context window* yet the answer is still wrong —
``evaluation/out/ceiling_v5/failure_attribution.json`` bucket
``"generation"`` ∩ audit verdict ``model_wrong`` (60 qids at time of writing).
This harness replays those exact questions through the production generation
path (same ``C_hybrid`` retrieval arm, same ``ContextBuilder`` packing, same
``GroundedGenerationService``) with whatever template is currently shipped,
then grades each answer with the frozen ``score_answer`` (soft + binary)
against the benchmark reference.

It is the correctness-side instrument for prompt conditioning:
``ab_baseline_citation`` gates citations/abstention; this gates whether the
answer is actually right. Results cache per qid and are resumable, and each
record carries the audit's ``model_action`` so results can be sliced by cause.

Usage:
    python -m evaluation.rerun_gold_in_prompt --tag baseline
    python -m evaluation.rerun_gold_in_prompt --tag v1 --limit 12
    python -m evaluation.rerun_gold_in_prompt --tag v1 --qids Q015,Q036
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

CEILING_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
OUT_DIR = CEILING_DIR / "rerun_gip"
ATTRIBUTION = CEILING_DIR / "failure_attribution.json"
TABULATION = CEILING_DIR / "full_review_tabulation.json"

#: The free-tier model throttles; keep concurrency low and let the client's
#: backoff absorb 429s (ADR-0010 §9.5 records the same constraint).
DEFAULT_CONCURRENCY = 3


def load_targets() -> list[dict]:
    """The gold-in-prompt failure set: bucket == generation ∩ model_wrong."""
    attribution = json.loads(ATTRIBUTION.read_text(encoding="utf-8"))
    tabulation = json.loads(TABULATION.read_text(encoding="utf-8"))
    notes = {rec.get("qid"): rec for rec in tabulation.get("records") or []}
    targets = []
    for rec in attribution.get("per_question") or []:
        if rec.get("verdict") != "model_wrong" or rec.get("bucket") != "generation":
            continue
        audit = notes.get(rec.get("qid")) or {}
        targets.append(
            {
                "qid": rec.get("qid"),
                "audit_action": audit.get("model_action"),
                "audit_category": audit.get("category"),
                "audit_note": audit.get("notes") or "",
            },
        )
    return sorted(targets, key=lambda t: str(t["qid"]))


def load_complement() -> list[dict]:
    """Every other benchmark qid — the regression cohort for the same changes."""
    target_ids = {t["qid"] for t in load_targets()}
    from evaluation.benchmark import load_questions

    return [
        {"qid": q.question_id, "audit_action": "control", "audit_category": "", "audit_note": ""}
        for q in load_questions()
        if q.question_id not in target_ids
    ]


def run_one(qid: str, arms: dict, payload_index: dict, question, service) -> dict:
    """Generate one answer through the production path and grade it."""
    from evaluation.answer_scoring import score_answer
    from evaluation.bench_p0_1_packing import _chunks_for
    from app.rag.generation.context_builder import ContextBuilder
    from evaluation.resolution import FamilyMap, matches_gold

    qt = question.question_types[0] if question.question_types else ""

    # Same context provenance as failure_attribution: C_hybrid arm chunks
    # rebuilt from the payload index and packed by the production builder.
    ids = arms.get(qid) or []
    chunks = _chunks_for(ids, payload_index)
    if not chunks:
        return {"qid": qid, "error": "no chunks for arm", "binary_correct": 0}

    # Gold still admitted to the packed window? Guards against packing fixes
    # that drop gold while "fixing" something else.
    gold_in_prompt = False
    try:
        fm = FamilyMap()
        built = ContextBuilder(query_type=qt).build(question.question, chunks, qt)
        admitted = {c.get("chunk_id") for c in built.citations}
        units = question.recall_units()
        gold_in_prompt = any(
            cid in payload_index and any(matches_gold(payload_index[cid], u, fm) for u in units) for cid in admitted
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("gold check failed for %s: %s", qid, exc)

    t0 = time.monotonic()
    try:
        resp = service.generate(question.question, chunks, query_type=qt)
        answer = resp.answer or ""
        err = (resp.debug or {}).get("error")
    except Exception as exc:
        answer, err = "", f"{type(exc).__name__}: {exc}"

    scored = score_answer(answer, question.acceptable_conclusion, question.insufficient_evidence, None)
    return {
        "qid": qid,
        "binary_correct": int(scored["binary_correct"]),
        "binary_correct_overlap_only": int(scored["binary_correct_overlap_only"]),
        "binary_correct_abstention_credit": int(scored["binary_correct_abstention_credit"]),
        "answer_correctness": scored["answer_correctness"],
        "answer_coverage": scored["answer_coverage"],
        "abstained": bool(answer.strip() and "not in" in answer.lower()[:400]) or len(answer.strip()) < 20,
        "answer_length": len(answer),
        "gold_in_prompt": int(bool(gold_in_prompt)),
        "latency_s": round(time.monotonic() - t0, 2),
        "error": err,
        "answer": answer,
    }


def adjudicate_tags(tags: list[str], cohort: str = "failures") -> None:
    """Re-score cached answer sets against the benchmark's own requirements.

    Soft token overlap under-credits legally correct answers phrased
    differently (the audit's 38% evaluator-miss finding is the same failure
    in the human grader). This second pass asks the benchmark itself what the
    answer must contain — ``gold_concepts`` and ``gold_authorities`` — and
    reports per-qid concept/authority coverage. A qid counts as eliminated
    only when its answer carries those requirements (or the question is an
    ``insufficient_evidence`` one and the abstention rule credits the
    refusal). Run after any LLM pass; makes no LLM calls.
    """
    from evaluation.benchmark import load_questions

    if cohort == "control":
        targets = load_complement()
    elif cohort == "all":
        targets = sorted([*load_targets(), *load_complement()], key=lambda t: str(t["qid"]))
    else:
        targets = load_targets()
    questions = {q.question_id: q for q in load_questions()}
    print(f"{'tag':<12} {'fixed':>7} {'remaining':>10}  by audit action", flush=True)
    for tag in tags:
        path = OUT_DIR / f"{tag}.jsonl"
        if not path.exists():
            print(f"{tag:<12} missing: {path}", flush=True)
            continue
        recs = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                recs[rec["qid"]] = rec
        rows = []
        for t in targets:
            q = questions[t["qid"]]
            rec = recs.get(t["qid"], {})
            cov = concept_coverage(rec.get("answer") or "", q)
            auth = authority_coverage(rec.get("answer") or "", q)
            abstained = bool(rec.get("abstained"))
            ok = (cov["all"] and auth["hit"] and not abstained) or (
                q.insufficient_evidence and rec.get("binary_correct_abstention_credit")
            )
            rows.append({
                **t,
                "concepts_covered": cov["covered"],
                "concepts_missing": cov["missing"],
                "authority_hit": auth["hit"],
                "eliminated": bool(ok),
            })
        fixed = sum(1 for r in rows if r["eliminated"])
        by_action: dict[str, list] = {}
        for r in rows:
            by_action.setdefault(r["audit_action"] or "unknown", []).append(r)
        action_str = "  ".join(
            f"{a}={sum(1 for x in rs if x['eliminated'])}/{len(rs)}" for a, rs in sorted(by_action.items())
        )
        print(f"{tag:<12} {fixed:>3}/{len(rows)} {len(rows) - fixed:>10}  {action_str}", flush=True)
        (OUT_DIR / f"{tag}_adjudication.json").write_text(
            json.dumps({"tag": tag, "n": len(rows), "n_eliminated": fixed, "rows": rows}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        for r in rows:
            if not r["eliminated"]:
                print(
                    f"    remaining {r['qid']} ({r['audit_action']}) missing={r['concepts_missing']} "
                    f"auth={r['authority_hit']}",
                    flush=True,
                )


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {"the", "of", "a", "an", "and", "or", "to", "in", "on", "for", "under", "per", "with", "by"}


def _toks(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOP}


def concept_coverage(answer: str, question) -> dict:
    """How many of the benchmark's gold_concepts the answer carries."""
    ans = _toks(answer)
    covered, missing = [], []
    for c in question.gold_concepts:
        need = _toks(c)
        if not need:
            continue
        hit = len(need & ans) / len(need)
        (covered if hit >= 0.8 else missing).append(c)
    return {"covered": covered, "missing": missing, "all": bool(covered) and not missing}


def authority_coverage(answer: str, question) -> dict:
    """Whether the answer names at least one gold authority (>=50% token match)."""
    ans = _toks(answer)
    for a in question.gold_authorities:
        need = _toks(a)
        if not need:
            continue
        if len(need & ans) / len(need) >= 0.5:
            return {"hit": True, "authority": a}
    return {"hit": False, "authority": None}


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    os.environ["RAG_USE_STUB_LLM"] = "false"

    from evaluation.bench_p0_1_packing import _load_jsonl, _load_payload_index, RAW_DIR
    from evaluation.benchmark import load_questions

    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="", help="result file name under out/ceiling_v5/rerun_gip/")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--qids", default="", help="comma-separated subset")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between completed calls")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--max-chars", type=int, default=0, help="override ContextBuilder char budget (0 = config default)"
    )
    parser.add_argument(
        "--cohort",
        choices=["failures", "control", "all"],
        default="failures",
        help="failures = the gold-in-prompt 60 (default); control = the other 90; all = the full benchmark",
    )
    parser.add_argument(
        "--legacy-template",
        action="store_true",
        help="render the pre-conditioning scan-only user template (control for the template change)",
    )
    parser.add_argument("--no-drop-index", action="store_true", help="control for the index-chunk packing change")
    parser.add_argument(
        "--score-only",
        default="",
        help="comma-separated tags to (re-)adjudicate from cached results; no LLM calls",
    )
    args = parser.parse_args()

    if args.score_only:
        adjudicate_tags(
            [t.strip() for t in args.score_only.split(",") if t.strip()],
            cohort=args.cohort,
        )
        return 0

    if args.cohort == "control":
        targets = load_complement()
    elif args.cohort == "all":
        targets = sorted([*load_targets(), *load_complement()], key=lambda t: str(t["qid"]))
    else:
        targets = load_targets()
    if args.qids:
        wanted = {q.strip() for q in args.qids.split(",") if q.strip()}
        targets = [t for t in targets if t["qid"] in wanted]
    if args.limit:
        targets = targets[: args.limit]
    print(f"targets: {len(targets)} gold-in-prompt qids", flush=True)

    questions = {q.question_id: q for q in load_questions()}
    arm_recs = _load_jsonl(RAW_DIR / "C_hybrid.jsonl")
    arms = {r.get("question_id"): [str(c) for c in (r.get("chunk_ids") or [])] for r in arm_recs}
    payload_index = _load_payload_index()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{args.tag}.jsonl"
    summary_path = OUT_DIR / f"{args.tag}_summary.json"
    done: dict[str, dict] = {}
    if out_path.exists() and not args.force:
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                done[rec["qid"]] = rec
    todo = [t for t in targets if t["qid"] not in done]
    print(f"cached: {len(done)} | to run: {len(todo)}", flush=True)

    from app.rag.generation import GroundedGenerationService
    from app.rag.generation.context_builder import ContextBuilder
    from evaluation.llm_ssl_client import SSLBypassLLMClient
    import app.rag.generation.prompt_template as pt
    from app.shared.config import cfg

    if args.legacy_template:
        from evaluation.ab_baseline_citation import _CANDIDATES

        legacy = _CANDIDATES["cand_scan"]
        pt.GROUND_QA_USER_TEMPLATE = legacy
        pt._TEMPLATES["grounded_qa"] = (pt.GROUND_QA_SYSTEM_PROMPT, legacy)
    if args.no_drop_index:
        cfg.context_drop_index_chunks = False

    service = GroundedGenerationService(
        llm_client=SSLBypassLLMClient(),
        context_builder=ContextBuilder(max_context_chars=args.max_chars or None),
    )
    results = dict(done)

    def work(target):
        qid = target["qid"]
        question = questions.get(qid)
        if question is None:
            return {"qid": qid, "error": "question not in benchmark", "binary_correct": 0}
        rec = run_one(qid, arms, payload_index, question, service)
        rec["audit_action"] = target["audit_action"]
        rec["audit_note"] = target["audit_note"]
        return rec

    if todo:
        with (
            ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex,
            open(out_path, "a", encoding="utf-8") as out,
        ):
            futures = {ex.submit(work, t): t for t in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                rec = fut.result()
                results[rec["qid"]] = rec
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                status = "FIXED" if rec.get("binary_correct") else "fail"
                print(
                    f"[{i}/{len(todo)}] {rec['qid']} {status} soft={rec.get('answer_correctness')} "
                    f"gold={rec.get('gold_in_prompt')} len={rec.get('answer_length')} ({rec.get('audit_action')})",
                    flush=True,
                )
                if args.delay:
                    time.sleep(args.delay)

    rows = [results.get(t["qid"], {}) for t in targets]
    n = len(rows)
    fixed = sum(1 for r in rows if r.get("binary_correct"))
    by_action: dict[str, dict] = {}
    for r in rows:
        a = r.get("audit_action") or "unknown"
        slot = by_action.setdefault(a, {"n": 0, "fixed": 0})
        slot["n"] += 1
        slot["fixed"] += int(bool(r.get("binary_correct")))
    errors = [r["qid"] for r in rows if r.get("error")]
    missing_gold = [r["qid"] for r in rows if r.get("gold_in_prompt") == 0]

    summary = {
        "tag": args.tag,
        "n_targets": n,
        "n_fixed": fixed,
        "n_remaining": n - fixed,
        "by_audit_action": by_action,
        "errors": errors,
        "gold_dropped_from_prompt": missing_gold,
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 70, flush=True)
    print(f"tag={args.tag}  fixed {fixed}/{n}  remaining {n - fixed}", flush=True)
    for action, slot in sorted(by_action.items()):
        print(f"  {action:<24} fixed {slot['fixed']:>3}/{slot['n']:<3}", flush=True)
    if errors:
        print(f"  ERRORS ({len(errors)}): {', '.join(errors)}", flush=True)
    if missing_gold:
        print(f"  gold dropped from prompt ({len(missing_gold)}): {', '.join(missing_gold)}", flush=True)
    print(f"results: {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Freeze the generation-failure cohort for the contrastive statutory reasoning experiment.

Milestone 1 of ``docs/plans/CONTRASTIVE_STATUTORY_REASONING_PLAN.md``.

Why this exists
---------------
The plan targets "the 60-question generation failure cohort" and says to
filter ``retrieval_answer_link.json`` for rows where ``bucket == "generation"``.
That filter yields **113** rows, not 60:

* ``evaluation/out/ceiling_v5/retrieval_answer_link.json`` is a **stale
  snapshot** -- it reports a ``generation`` bucket of n=60 and a binary rate of
  1.67%, from an older attribution.
* the live source, ``evaluation/out/ceiling_v5/failure_attribution.json``,
  has **113** rows with ``bucket == "generation"``: 59 ``model_wrong``,
  52 ``evaluator_miss`` and 2 ``reference_narrow``.

The cohort the plan means is ``bucket == "generation" AND verdict ==
"model_wrong"`` -- which is exactly what
:func:`evaluation.rerun_gold_in_prompt.load_targets` returns (59 qids).  This
script uses that as the single source of truth and asserts the count.

What is frozen
--------------
For each question: the query, the benchmark's own requirements (reference
conclusion, gold authorities/concepts, gold provision units), the **frozen
retrieved context** (the same ``C_hybrid`` window the rerun harness builds,
with the chunk text embedded so a later corpus re-ingest cannot silently
change the experiment), and a **cached baseline answer** so that a
verification gate can fall back to it without spending a second call.

Provenance is recorded as sha256 of every input, and ``--check`` re-derives
the cohort and reports drift -- the point of freezing is to be able to prove
nothing moved.

Usage:
    python -m evaluation.build_generation_cohort
    python -m evaluation.build_generation_cohort --baseline-tags cond_v5,current_status_eval
    python -m evaluation.build_generation_cohort --check
    python -m evaluation.build_generation_cohort --include-rendered-context
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from evaluation.benchmark import load_questions  # noqa: E402
from evaluation.bench_p0_1_packing import (  # noqa: E402
    RAW_DIR,
    _chunks_for,
    _load_jsonl,
    _load_payload_index,
)
from evaluation.rerun_gold_in_prompt import load_targets  # noqa: E402
from evaluation.rubric_top_lawyer import load_cached_answers  # noqa: E402

CEIL_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
OUT_FILE = CEIL_DIR / "generation_cohort.json"

#: The retrieval arm whose window every measurement in this repo replays.
ARM = "C_hybrid"

#: The live cohort size.  ``bucket == "generation" AND verdict == "model_wrong"``.
EXPECTED_COUNT = 59

#: The audit's own failure-mode taxonomy for this cohort.  The contrastive
#: plan proposes three categories (Provision Swap / Penalty-Proviso /
#: Remedy-Authority) but the human audit has three *different* ones -- and the
#: plan's third category has no audit counterpart while ``abstention_gate``
#: (a refusal failure, 20 of 59) is absent from the plan entirely.
#: Exported so the gap is visible rather than implied.
AUDIT_ACTIONS = ("provision_check", "needs_deeper_reasoning", "abstention_gate")

#: Default baseline tags, in priority order.  The first tag with a usable
#: record for a question wins; the tag actually used is recorded per
#: question, so any mixing is visible rather than silent.
DEFAULT_BASELINE_TAGS = ("cond_v5", "current_status_eval", "prod_r1")


def _sha256(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _gold_unit(unit: Any) -> dict[str, Any]:
    return {
        "provision_id": unit.provision_id,
        "family": unit.family,
        "section": unit.section,
        "act": unit.act,
        "role": unit.role,
        "gain": unit.gain,
        "title": unit.title,
    }


def _chunk(chunk: Any, include_text: bool) -> dict[str, Any]:
    out = {
        "chunk_id": chunk.chunk_id,
        "score": round(float(chunk.score), 6),
        "section_number": chunk.section_number,
        "act_name": chunk.act_name,
        "document_title": chunk.document_title,
        "document_type": chunk.document_type,
        "authority": chunk.authority,
    }
    if include_text:
        out["text"] = chunk.text
    return out


def _baseline_for(qid: str, caches: dict[str, dict[str, dict]]) -> tuple[str, dict[str, Any]] | None:
    """First usable baseline answer for *qid*, in tag priority order."""
    for tag, records in caches.items():
        rec = records.get(qid)
        if rec is None or rec.get("error"):
            continue
        answer = rec.get("answer") or ""
        if not answer:
            continue
        return tag, {
            "answer": answer,
            "binary_correct": int(rec.get("binary_correct") or 0),
            "answer_correctness": float(rec.get("answer_correctness") or 0.0),
            "answer_coverage": float(rec.get("answer_coverage") or 0.0),
            "abstained": bool(rec.get("abstained")),
            "answer_length": int(rec.get("answer_length") or 0),
            "citation": rec.get("citation"),
            "flags": rec.get("flags"),
        }
    return None


def build_cohort(
    *,
    baseline_tags: tuple[str, ...] = DEFAULT_BASELINE_TAGS,
    include_text: bool = True,
    include_rendered_context: bool = False,
    include_pool_ids: bool = False,
) -> dict[str, Any]:
    """Assemble the frozen cohort."""
    from evaluation.resolution import FamilyMap, matches_gold

    questions = {q.question_id: q for q in load_questions()}
    targets = load_targets()
    payload_index = _load_payload_index()
    arm_recs = _load_jsonl(RAW_DIR / f"{ARM}.jsonl")
    arms = {str(r.get("question_id")): [str(c) for c in (r.get("chunk_ids") or [])] for r in arm_recs}
    caches = {tag: load_cached_answers(tag) for tag in baseline_tags}
    family_map = FamilyMap()

    rows: list[dict[str, Any]] = []
    missing_context: list[str] = []
    missing_baseline: list[str] = []

    for target in targets:
        qid = str(target["qid"])
        question = questions.get(qid)
        if question is None:
            continue
        chunk_ids = arms.get(qid) or []
        chunks = _chunks_for(chunk_ids, payload_index)
        if not chunks:
            missing_context.append(qid)
        by_id = {str(c.chunk_id): c for c in chunks}

        # Which chunks carry gold?  Two sets, and the difference matters:
        # ``gold_admitted`` covers every recall unit (primary + acceptable +
        # supporting) and is therefore broad -- 14 chunks for one question.
        # ``gold_relevant`` covers only the primary and acceptable units, i.e.
        # the provisions that actually govern, and is what Stage 1's recall
        # has to hit.
        gold_admitted: list[str] = []
        gold_relevant: list[str] = []
        relevant_units = question.relevant_units()
        for chunk in chunks:
            payload = payload_index.get(str(chunk.chunk_id))
            if payload is None:
                continue
            try:
                if any(matches_gold(payload, u, family_map) for u in question.recall_units()):
                    gold_admitted.append(str(chunk.chunk_id))
                if relevant_units and any(matches_gold(payload, u, family_map) for u in relevant_units):
                    gold_relevant.append(str(chunk.chunk_id))
            except Exception:
                continue

        # The frozen context is the PACKED WINDOW -- what the model actually
        # sees -- not the 500-chunk retrieval pool behind it.  Stage 1
        # extracts spans from the prompt, so the prompt is what has to be
        # frozen; the pool is kept as a fingerprint, because the pool is what
        # determines the packing.
        packed: dict[str, Any] = {
            "arm": ARM,
            "pool_size": len(chunk_ids),
            "pool_chunk_ids_sha256": _sha256_text("|".join(chunk_ids)),
        }
        window_chunks: list[Any] = []
        try:
            from app.rag.generation.context_builder import ContextBuilder

            qt = question.question_types[0] if question.question_types else ""
            built = ContextBuilder(query_type=qt).build(question.question, chunks, qt)
            packed["citations"] = [{"index": c.get("index"), "chunk_id": c.get("chunk_id")} for c in built.citations]
            packed["context_chars"] = len(built.context or "")
            packed["context_sha256"] = _sha256_text(built.context or "")
            if include_rendered_context:
                packed["context"] = built.context
            window_ids = [str(c.get("chunk_id")) for c in built.citations]
            window_chunks = [by_id[cid] for cid in window_ids if cid in by_id]
        except Exception as exc:  # noqa: BLE001 — the packed view is a convenience, not a gate
            packed["error"] = f"{type(exc).__name__}: {exc}"
            window_chunks = chunks

        packed["window_size"] = len(window_chunks)
        if include_pool_ids:
            packed["pool_chunk_ids"] = chunk_ids

        baseline = _baseline_for(qid, caches)
        if baseline is None:
            missing_baseline.append(qid)
            baseline_tag, baseline_rec = "", {}
        else:
            baseline_tag, baseline_rec = baseline

        rows.append({
            "qid": qid,
            "question": question.question,
            "question_types": question.question_types,
            "difficulty": question.difficulty,
            "domains": question.domains,
            "audit_action": target.get("audit_action"),
            "audit_category": target.get("audit_category"),
            "audit_note": target.get("audit_note"),
            "acceptable_conclusion": question.acceptable_conclusion,
            "insufficient_evidence": question.insufficient_evidence,
            "gold_authorities": question.gold_authorities,
            "gold_concepts": question.gold_concepts,
            "gold_units_recall": [_gold_unit(u) for u in question.recall_units()],
            "gold_units_relevant": [_gold_unit(u) for u in question.relevant_units()],
            "context": {
                **packed,
                "gold_in_prompt": bool(gold_admitted),
                "gold_in_window": bool(set(gold_admitted) & {str(c.chunk_id) for c in window_chunks}),
                "gold_admitted_chunk_ids": gold_admitted,
                "gold_relevant_chunk_ids": gold_relevant,
                "gold_relevant_in_window": bool(set(gold_relevant) & {str(c.chunk_id) for c in window_chunks}),
                "chunks": [_chunk(c, include_text) for c in window_chunks],
            },
            "baseline": {"tag": baseline_tag, **baseline_rec},
        })

    action_mix = Counter(r["audit_action"] for r in rows)
    baseline_mix = Counter(r["baseline"]["tag"] for r in rows)
    soft = [r["baseline"]["answer_correctness"] for r in rows if r["baseline"]]
    binary = sum(r["baseline"]["binary_correct"] for r in rows if r["baseline"])
    window_sizes = sorted(r["context"]["window_size"] for r in rows)
    gold_in_window = sum(1 for r in rows if r["context"]["gold_in_window"])
    gold_absent = [
        {"qid": r["qid"], "audit_action": r["audit_action"], "window_size": r["context"]["window_size"]}
        for r in rows
        if not r["context"]["gold_in_window"]
    ]

    return {
        "schema": "generation_cohort/1",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "cohort_definition": {
            "predicate": "failure_attribution.bucket == 'generation' AND verdict == 'model_wrong'",
            "source": "evaluation.rerun_gold_in_prompt.load_targets",
            "n": len(rows),
            "expected_n": EXPECTED_COUNT,
        },
        "baseline_tags": list(baseline_tags),
        "summary": {
            "n": len(rows),
            "by_audit_action": dict(action_mix),
            "baseline_tags_used": dict(baseline_mix),
            "baseline_binary_correct": binary,
            "baseline_binary_rate": round(binary / len(rows), 4) if rows else None,
            "baseline_soft_mean": round(sum(soft) / len(soft), 4) if soft else None,
            "insufficient_evidence": sum(1 for r in rows if r["insufficient_evidence"]),
            "gold_in_prompt": sum(1 for r in rows if r["context"]["gold_in_prompt"]),
            "gold_in_window": gold_in_window,
            "gold_relevant_in_window": sum(1 for r in rows if r["context"]["gold_relevant_in_window"]),
            "gold_absent_from_window": gold_absent,
            "window_size": {
                "min": window_sizes[0] if window_sizes else None,
                "median": window_sizes[len(window_sizes) // 2] if window_sizes else None,
                "max": window_sizes[-1] if window_sizes else None,
            },
            "missing_context": missing_context,
            "missing_baseline": missing_baseline,
        },
        "provenance": {
            "arm": ARM,
            "C_hybrid.jsonl": {"path": str(RAW_DIR / f"{ARM}.jsonl"), "sha256": _sha256(RAW_DIR / f"{ARM}.jsonl")},
            "payload_index.jsonl": {
                "path": str(PROJECT_ROOT / "evaluation" / "out" / "cache" / "payload_index.jsonl"),
                "sha256": _sha256(PROJECT_ROOT / "evaluation" / "out" / "cache" / "payload_index.jsonl"),
            },
            "failure_attribution.json": {
                "path": str(CEIL_DIR / "failure_attribution.json"),
                "sha256": _sha256(CEIL_DIR / "failure_attribution.json"),
            },
            "full_review_tabulation.json": {
                "path": str(CEIL_DIR / "full_review_tabulation.json"),
                "sha256": _sha256(CEIL_DIR / "full_review_tabulation.json"),
            },
        },
        "questions": rows,
    }


def _check(existing: dict[str, Any], fresh: dict[str, Any]) -> int:
    """Report drift between a frozen cohort and a fresh derivation."""
    old = {q["qid"]: q for q in existing.get("questions") or []}
    new = {q["qid"]: q for q in fresh.get("questions") or []}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed_ctx = []
    changed_baseline = []
    for qid in sorted(set(old) & set(new)):
        if old[qid].get("context", {}).get("context_sha256") != new[qid].get("context", {}).get("context_sha256"):
            changed_ctx.append(qid)
        if old[qid].get("baseline", {}).get("answer") != new[qid].get("baseline", {}).get("answer"):
            changed_baseline.append(qid)

    print("=" * 92)
    print("COHORT DRIFT CHECK")
    print("=" * 92)
    print(f"  frozen n={len(old)}   fresh n={len(new)}")
    print(f"  added   : {added or 'none'}")
    print(f"  removed : {removed or 'none'}")
    print(f"  context changed  : {len(changed_ctx)} {changed_ctx[:10]}")
    print(f"  baseline changed : {len(changed_baseline)} {changed_baseline[:10]}")
    for key in ("C_hybrid.jsonl", "payload_index.jsonl", "failure_attribution.json"):
        old_hash = (existing.get("provenance", {}).get(key) or {}).get("sha256")
        new_hash = (fresh.get("provenance", {}).get(key) or {}).get("sha256")
        if old_hash != new_hash:
            print(f"  INPUT CHANGED: {key}  {old_hash[:12]} -> {new_hash[:12]}")
    drift = bool(added or removed or changed_ctx or changed_baseline)
    print(f"\n  verdict: {'DRIFT -- the frozen cohort no longer matches' if drift else 'no drift'}")
    return 1 if drift else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--baseline-tags",
        default=",".join(DEFAULT_BASELINE_TAGS),
        help="comma-separated cached tags, in priority order; the first with a usable record wins",
    )
    parser.add_argument("--out", default=str(OUT_FILE))
    parser.add_argument("--no-text", action="store_true", help="omit the embedded chunk text")
    parser.add_argument("--include-rendered-context", action="store_true", help="embed the packed prompt text")
    parser.add_argument("--include-pool-ids", action="store_true", help="embed the full retrieval pool's chunk ids")
    parser.add_argument("--check", action="store_true", help="compare against an existing cohort and report drift")
    parser.add_argument("--expect-count", type=int, default=EXPECTED_COUNT)
    parser.add_argument("--no-guard", action="store_true", help="do not fail when the count differs")
    args = parser.parse_args()

    tags = tuple(t.strip() for t in args.baseline_tags.split(",") if t.strip())
    cohort = build_cohort(
        baseline_tags=tags,
        include_text=not args.no_text,
        include_rendered_context=args.include_rendered_context,
        include_pool_ids=args.include_pool_ids,
    )
    out_path = Path(args.out)

    if args.check:
        if not out_path.exists():
            print(f"no frozen cohort at {out_path}; nothing to compare against")
            return 1
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        return _check(existing, cohort)

    s = cohort["summary"]
    print("=" * 92)
    print("GENERATION FAILURE COHORT (frozen)")
    print("=" * 92)
    print(f"  n = {s['n']}   (expected {args.expect_count})")
    print(f"  predicate: {cohort['cohort_definition']['predicate']}")
    print(f"  by audit action      : {s['by_audit_action']}")
    print(f"  baseline tags used   : {s['baseline_tags_used']}")
    print(
        f"  baseline on cohort   : binary {s['baseline_binary_correct']}/{s['n']} "
        f"({s['baseline_binary_rate']}), soft mean {s['baseline_soft_mean']}"
    )
    print(f"  insufficient_evidence: {s['insufficient_evidence']}")
    print(f"  gold in retrieval pool: {s['gold_in_prompt']}/{s['n']}")
    print(
        f"  gold in packed window : {s['gold_in_window']}/{s['n']}"
        f"   window size min/median/max = "
        f"{s['window_size']['min']}/{s['window_size']['median']}/{s['window_size']['max']}"
    )
    if s["gold_absent_from_window"]:
        print("  !! gold NOT in the packed window (nothing to adjudicate):")
        for g in s["gold_absent_from_window"]:
            print(f"       {g['qid']}  action={g['audit_action']}  window_size={g['window_size']}")
    if s["missing_context"]:
        print(f"  !! no context for    : {s['missing_context']}")
    if s["missing_baseline"]:
        print(f"  !! no baseline for   : {s['missing_baseline']}")

    uncovered = [a for a in AUDIT_ACTIONS if a not in s["by_audit_action"]]
    print()
    print("  FAILURE-MODE COVERAGE (the plan's three categories vs the audit's three)")
    for action in AUDIT_ACTIONS:
        print(f"    {action:<24} n={s['by_audit_action'].get(action, 0)}")
    print(
        "    NOTE: the plan's category C (Remedy/Authority conflation) has no audit\n"
        "    counterpart, and abstention_gate is a REFUSAL failure that a\n"
        "    contrastive 'commit to the governing provision' pipeline does not\n"
        "    address -- and pushes the wrong way on."
    )
    if uncovered:
        print(f"    !! audit actions with no cohort members: {uncovered}")

    if s["n"] != args.expect_count and not args.no_guard:
        print(f"\nGUARD FAILED: expected {args.expect_count} questions, got {s['n']}.")
        print("The cohort definition changed. Re-run with --no-guard if that is intended.")
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cohort, indent=2, ensure_ascii=False), encoding="utf-8")
    size_mb = out_path.stat().st_size / 1e6
    print(f"\nwritten: {out_path}  ({size_mb:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

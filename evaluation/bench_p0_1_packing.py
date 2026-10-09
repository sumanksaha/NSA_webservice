"""P0-1 benchmark: does evidence-set packing change the prompt for the better?

Deterministic, offline, no LLM calls.  Uses the cached retrieval arms from
``evaluation/out/ceiling_v5/raw/`` so the same 150 benchmark questions can be
replayed without Qdrant, Neo4j, or a paid LLM.

What this measures
------------------
P0-1 reorders the prompt by legal role and annotates ``<document>`` tags.  The
honest question is not "does the prompt look different" (it obviously does) but
whether the change moves *gold evidence* into a position the model can use.

Two effects are reported per arm:

``gold_in_prompt``
    Whether the gold provision's chunk survives into the context at all.
    Packing must not lose ground here — a role filter that drops gold is a
    regression even if ordering improves.

``primary_first``
    Whether the highest-value provision (primary / subsection / exception)
    leads the prompt under role ordering.  This is the anti-definition-anchoring
    claim, and it is the only genuinely new behaviour.

Because the cached arms carry ``chunk_ids`` but not retrieval scores, the
baseline ordering here is arm order (the cached rank), not re-sorted score.  That
is a limitation, stated in the output, not a hidden assumption.

Usage:
    python -m evaluation.bench_p0_1_packing
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

RAW_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "raw"
CACHE_DIR = PROJECT_ROOT / "evaluation" / "out" / "cache"
OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "p0_1_packing_bench.json"

ARMS = ("C_hybrid", "B_sparse", "A_dense", "D_kg")

#: Roles that lead the prompt under _PACK_ORDER — the "governing" provisions.
LEADING_ROLES = {"primary_provision", "subsection", "exception"}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _load_payload_index() -> dict[str, dict]:
    recs = _load_jsonl(CACHE_DIR / "payload_index.jsonl")
    return {r["id"]: r["payload"] for r in recs if "id" in r and "payload" in r}


def _chunks_for(chunk_ids: list[str], payload_index: dict[str, dict]):
    """Cached chunk_ids → RetrievedChunk objects (score is arm rank, descending)."""
    from app.rag.retrieval.result import RetrievedChunk

    out = []
    n = len(chunk_ids)
    for i, cid in enumerate(chunk_ids):
        payload = payload_index.get(str(cid))
        if payload is None:
            continue
        out.append(
            RetrievedChunk(
                chunk_id=str(cid),
                score=1.0 - (i / max(n, 1)),
                text=payload.get("chunk_text", payload.get("text", "")) or "",
                section_number=payload.get("section_number"),
                document_title=payload.get("document_title", "") or "",
                act_name=payload.get("act_name", "") or "",
                document_type=payload.get("document_type", "") or "",
                authority=payload.get("authority", "") or "",
            ),
        )
    return out


def _gold_hit(chunk_ids: list[str], payload_index: dict[str, dict], question, family_map) -> bool:
    """True when any cached chunk resolves to one of the question's gold units."""
    from evaluation.resolution import matches_gold

    units = question.recall_units()
    if not units:
        return False
    for cid in chunk_ids:
        payload = payload_index.get(str(cid))
        if payload is None:
            continue
        for unit in units:
            try:
                if matches_gold(payload, unit, family_map):
                    return True
            except Exception:
                continue
    return False


def _load_gold_index(payload_index: dict[str, dict]) -> dict[str, list]:
    """Qid -> its GoldUnit list, for provision-level matching.

    Gold provisions are keyed by act+section (``fssai:s16(1)``) and their
    ``chunk_id`` fields are largely null, so gold CANNOT be resolved by id
    lookup.  Matching is provision-level via ``matches_gold``, exactly as
    ``eval_e2e_v2``/``grading.py`` do it — a chunk counts as gold when its
    payload resolves to one of the question's gold units.
    """
    from evaluation.benchmark import load_questions

    questions = {q.question_id: q for q in load_questions()}
    return questions


def run_arm(
    arm: str,
    payload_index: dict[str, dict],
    questions: dict[str, Any],
    family_map: Any,
    max_chunks: int = 10,
    max_context_chars: int = 10_000,
) -> dict[str, Any]:
    from app.rag.generation.context_builder import ContextBuilder
    from app.rag.retrieval.evidence_selector import select_evidence_set

    recs = {r["question_id"]: r for r in _load_jsonl(RAW_DIR / f"{arm}.jsonl")}
    if not recs:
        return {"arm": arm, "n_questions": 0, "note": "no cached arm file"}

    builder = ContextBuilder(max_chunks=max_chunks, max_context_chars=max_context_chars)
    stats = {
        "arm": arm,
        "max_chunks": max_chunks,
        "max_context_chars": max_context_chars,
        "n_questions": 0,
        "n_gold_scored": 0,
        "baseline_gold_in_prompt": 0,
        "packed_gold_in_prompt": 0,
        "packed_primary_first": 0,
        "baseline_primary_first": 0,
        "prompt_changed": 0,
        "n_with_evidence_set": 0,
        "evidence_set_all_fits": 0,
    }

    for qid, rec in recs.items():
        chunk_ids = [str(c) for c in (rec.get("chunk_ids") or [])][:10]
        chunks = _chunks_for(chunk_ids, payload_index)
        if len(chunks) < 2:
            continue
        stats["n_questions"] += 1

        question = questions.get(qid)
        es = select_evidence_set(rec.get("query", ""), chunks, max_size=5, min_size=2)
        es_dict = es.to_dict()

        baseline = builder.build(rec.get("query", ""), chunks, query_type="general")
        packed = builder.build(rec.get("query", ""), chunks, query_type="general", evidence_set=es_dict)

        b_ids = [c["chunk_id"] for c in baseline.citations]
        p_ids = [c["chunk_id"] for c in packed.citations]

        if question is not None:
            stats["n_gold_scored"] += 1
            stats["baseline_gold_in_prompt"] += int(_gold_hit(b_ids, payload_index, question, family_map))
            stats["packed_gold_in_prompt"] += int(_gold_hit(p_ids, payload_index, question, family_map))
        p_id_set = set(p_ids)

        if packed.context != baseline.context:
            stats["prompt_changed"] += 1

        # primary_first: first citation is a governing role under each ordering
        if baseline.citations:
            b_first = baseline.citations[0]
            if es_dict.get("items"):
                first_item = es_dict["items"][0]
                if b_first["chunk_id"] == first_item.get("chunk_id"):
                    stats["baseline_primary_first"] += 1
        if packed.citations:
            p_first = packed.citations[0]
            if p_first.get("evidence_type") in LEADING_ROLES:
                stats["packed_primary_first"] += 1

        if es_dict.get("items"):
            stats["n_with_evidence_set"] += 1
            sel = {str(i.get("chunk_id")) for i in es_dict["items"]}
            if sel and sel.issubset(p_id_set):
                stats["evidence_set_all_fits"] += 1

    n = max(stats["n_questions"], 1)
    ng = max(stats["n_gold_scored"], 1)
    stats["baseline_gold_rate"] = round(stats["baseline_gold_in_prompt"] / ng, 4)
    stats["packed_gold_rate"] = round(stats["packed_gold_in_prompt"] / ng, 4)
    stats["packed_primary_first_rate"] = round(stats["packed_primary_first"] / n, 4)
    stats["prompt_changed_rate"] = round(stats["prompt_changed"] / n, 4)
    stats["evidence_set_fit_rate"] = round(stats["evidence_set_all_fits"] / max(stats["n_with_evidence_set"], 1), 4)
    return stats


def main() -> int:
    from evaluation.resolution import FamilyMap

    payload_index = _load_payload_index()
    questions = _load_gold_index(payload_index)
    family_map = FamilyMap()
    if not payload_index:
        print("missing payload_index.jsonl — cannot rebuild chunk text", file=sys.stderr)
        return 1

    # Sweep the context window.  The selector picks up to 5 items, but the
    # builder also admits context_relevant chunks; whether every selection
    # survives is a function of max_chunks, so measure it rather than assume.
    sweep = [(mc, 10_000) for mc in (5, 10, 15, 20)]
    arms: list[dict[str, Any]] = []
    for mc, chars in sweep:
        for arm in ARMS:
            res = run_arm(arm, payload_index, questions, family_map, max_chunks=mc, max_context_chars=chars)
            if res.get("n_questions"):
                arms.append(res)

    output = {
        "benchmark": "p0_1_evidence_packing",
        "date": "2026-10-04",
        "method": (
            "Deterministic, offline replay of cached retrieval arms through "
            "ContextBuilder with evidence_set=None vs evidence_set supplied. "
            "No LLM calls: this measures the PROMPT EFFECT of packing, not "
            "answer quality. Answer quality needs a paid A/B run."
        ),
        "limitations": [
            "Cached arms carry chunk_ids but not retrieval scores; baseline "
            "ordering is arm rank, so baseline_gold_rate is not the production "
            "score-sorted ordering.",
            "Gold is matched at PROVISION level via matches_gold, not by chunk "
            "id — gold_provisions_v1.0.json has null chunk_ids for most records.",
            "Measures whether gold evidence survives into the prompt and whether "
            "a governing provision leads. It does NOT measure answer correctness; "
            "that needs a paid LLM A/B run.",
            "evidence_set_fit_rate < 1.0 means selected evidence was dropped by "
            "the max_chunks window — the sweep across max_chunks quantifies how "
            "much of that gap is window-bound.",
        ],
        "gold_questions_found": len(questions),
        "payload_index_size": len(payload_index),
        "max_chunks_sweep": [mc for mc, _ in sweep],
        "arms": arms,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 78)
    print("P0-1 evidence-set packing — deterministic prompt-effect benchmark")
    print("=" * 78)
    print(f"payload index: {len(payload_index)} chunks | gold questions loaded: {len(questions)}")
    print()
    hdr = (
        f"{'maxChunks':>9} {'arm':<11} {'n':>4} {'goldBase':>9} {'goldPack':>9} "
        f"{'prim1st':>8} {'changed':>8} {'esFit':>7}"
    )
    print(hdr)
    print("-" * len(hdr))
    for a in arms:
        if not a.get("n_questions"):
            print(f"{a['arm']:<12}   (no cached data)")
            continue
        print(
            f"{a['max_chunks']:>9} {a['arm']:<11} {a['n_questions']:>4} {a['baseline_gold_rate']:>9.4f} "
            f"{a['packed_gold_rate']:>9.4f} {a['packed_primary_first_rate']:>8.4f} "
            f"{a['prompt_changed_rate']:>8.4f} {a['evidence_set_fit_rate']:>7.4f}",
        )
    print()
    print("goldBase/goldPack = gold provision present in prompt (must not drop)")
    print("prim1st           = governing role leads the prompt under packing")
    print("changed           = prompt actually differs from baseline")
    print("esFit             = every selected evidence item survived into the prompt")
    print()
    print(f"written: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

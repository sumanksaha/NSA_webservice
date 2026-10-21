"""Text-fallback tracking metrics.

Computes and writes evaluation/out/ceiling_v5/text_fallback_stats.json with:

- gold_chunks_found_via_text_fallback
    Total chunk-level gold matches resolved *only* through text fallback
    (stamp-based matches_gold failed, resolve_by_text succeeded), summed
    across all model_wrong questions.

- questions_where_text_fallback_is_the_only_resolution
    Count of questions for which *every* matched gold unit has at least one
    text-fallback-only chunk AND zero stamp-based matches — i.e. the text
    fallback is the sole gold-resolution path for that question.

- family_guard_rejections
    Total chunk×gold-unit pairs where stamp matching failed *and* the text
    fallback was rejected at the family-guard stage (chunk's act_name /
    document_title does not resolve to the gold unit's family).

Usage:
    python -m evaluation.text_fallback_tracking
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.benchmark import load_questions
from evaluation.bench_p0_1_packing import _load_payload_index
from evaluation.config import CACHE_DIR
from evaluation.resolution import FamilyMap, matches_gold
from evaluation.gold_text_match import resolve_by_text, _payload_families

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
OUT_FILE = OUT_DIR / "text_fallback_stats.json"
RAW_DIR = OUT_DIR / "raw"
TABULATION = OUT_DIR / "full_review_tabulation.json"
ARM = "C_hybrid"


def _categorise_text_fallback(payload: dict, unit, family_map: FamilyMap) -> tuple[bool, str]:
    """Replicate resolve_by_text but return *why* it failed.

    Returns (matched, reason):
      - 'stamp_hit'       when stamp-based matches_gold succeeds
      - 'text_match'      when resolve_by_text succeeds
      - 'family_guard'    stamp failed, family guard rejected
      - 'text_nomatch'    stamp failed, family matched but phrase not found
      - 'no_text'         stamp failed, chunk has no text at all
    """
    from evaluation.gold_text_match import text_matches_title, title_phrase

    if matches_gold(payload, unit, family_map, text_fallback=False):
        return True, "stamp_hit"

    text = payload.get("chunk_text") or payload.get("text") or ""
    if not text:
        return False, "no_text"

    # family guard
    if not unit.family:
        return False, "family_guard"
    families = _payload_families(payload, family_map)
    if not families or unit.family not in families:
        return False, "family_guard"

    # text phrase match
    phrase = title_phrase(unit.title)
    if phrase is None or not text_matches_title(text, unit.title):
        return False, "text_nomatch"

    return True, "text_match"


def _load_arm_chunk_ids(arm: str) -> dict[str, list[str]]:
    """Load {question_id: [chunk_id, ...]} from the raw arm file."""
    result: dict[str, list[str]] = {}
    fpath = RAW_DIR / f"{arm}.jsonl"
    if not fpath.exists():
        return result
    with open(fpath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            qid = rec.get("question_id")
            ids = rec.get("chunk_ids", [])
            if qid and ids:
                result[qid] = [str(c) for c in ids]
    return result


def main() -> int:
    payload_index = _load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}

    # model_wrong QIDs from the tabulation
    tab = json.loads(TABULATION.read_text(encoding="utf-8"))
    model_wrong = [
        r["qid"] for r in (tab.get("records") or [])
        if r.get("verdict") == "model_wrong"
    ]

    arm_ids = _load_arm_chunk_ids(ARM)

    per_question: dict[str, dict] = {}
    total_stamp_hits = 0
    total_text_only = 0
    total_distinct_text_only = 0
    total_family_guard_rejections = 0
    total_text_nomatch = 0
    total_no_text = 0
    total_pairs_scanned = 0
    tf_only_questions = 0
    tf_only_unit_count = 0

    for qid in model_wrong:
        q = questions.get(qid)
        if not q:
            continue
        chunk_ids = arm_ids.get(qid, [])
        if not chunk_ids:
            continue

        units = q.recall_units()
        stamp_hits: list[str] = []
        text_only_hits: list[str] = []
        text_only_set: set[str] = set()
        unit_breakdown: dict[str, dict] = {}
        q_family_guard = 0
        q_text_nomatch = 0
        q_no_text = 0
        q_pairs = 0

        for unit in units:
            unit_stamp: list[str] = []
            unit_text_only: list[str] = []
            for cid in chunk_ids:
                payload = payload_index.get(cid)
                if payload is None:
                    continue
                q_pairs += 1
                matched, reason = _categorise_text_fallback(payload, unit, family_map)
                if reason == "stamp_hit":
                    unit_stamp.append(cid)
                elif reason == "text_match":
                    unit_text_only.append(cid)
                    text_only_set.add(cid)
                elif reason == "family_guard":
                    q_family_guard += 1
                elif reason == "text_nomatch":
                    q_text_nomatch += 1
                elif reason == "no_text":
                    q_no_text += 1

            stamp_hits.extend(unit_stamp)
            text_only_hits.extend(unit_text_only)
            unit_breakdown[unit.provision_id] = {
                "stamp_hit_chunks": unit_stamp,
                "text_only_chunks": unit_text_only,
                "stamp_only_resolved": len(unit_stamp) > 0,
                "text_fallback_only_resolved": len(unit_stamp) == 0 and len(unit_text_only) > 0,
            }

        # text fallback is the ONLY resolution for ALL units
        tf_only_units = sum(
            1 for v in unit_breakdown.values() if v["text_fallback_only_resolved"]
        )
        tf_only = tf_only_units == len(units) and len(units) > 0 and len(stamp_hits) == 0

        if tf_only:
            tf_only_questions += 1
        tf_only_unit_count += tf_only_units

        total_stamp_hits += len(stamp_hits)
        total_text_only += len(text_only_hits)
        total_distinct_text_only += len(text_only_set)
        total_family_guard_rejections += q_family_guard
        total_text_nomatch += q_text_nomatch
        total_no_text += q_no_text
        total_pairs_scanned += q_pairs

        per_question[qid] = {
            "n_gold_units": len(units),
            "n_retrieved_chunks": len(chunk_ids),
            "stamp_hit_chunks": len(stamp_hits),
            "text_only_chunks": len(text_only_hits),
            "distinct_text_only_chunks": len(text_only_set),
            "family_guard_rejections": q_family_guard,
            "text_nomatch_rejections": q_text_nomatch,
            "no_text_chunks": q_no_text,
            "text_fallback_only_resolved_units": tf_only_units,
            "text_fallback_only_question": tf_only,
            "units": unit_breakdown,
        }

    stats = {
        "version": 1,
        "arm": ARM,
        "population": "model_wrong",
        "n_questions": len(model_wrong),
        "n_questions_with_retrieval": sum(1 for qid in model_wrong if qid in arm_ids),
        "summary": {
            "gold_chunks_found_via_text_fallback": total_text_only,
            "distinct_chunks_found_via_text_fallback": total_distinct_text_only,
            "questions_where_text_fallback_is_the_only_resolution": tf_only_questions,
            "family_guard_rejections": total_family_guard_rejections,
            "stamp_hit_chunks_total": total_stamp_hits,
            "text_nomatch_rejections": total_text_nomatch,
            "no_text_chunks": total_no_text,
            "total_chunk_unit_pairs_scanned": total_pairs_scanned,
            "text_fallback_only_resolved_unit_count": tf_only_unit_count,
        },
        "per_question": per_question,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT_FILE}")
    print(f"  Questions scanned: {stats['n_questions_with_retrieval']}")
    print(f"  Gold chunks via text fallback: {total_text_only}")
    print(f"  Distinct chunks via text fallback: {total_distinct_text_only}")
    print(f"  Questions where text fallback is sole resolution: {tf_only_questions}")
    print(f"  Family-guard rejections: {total_family_guard_rejections}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

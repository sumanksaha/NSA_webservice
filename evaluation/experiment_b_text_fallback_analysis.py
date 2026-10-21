"""Text-fallback analysis for Experiment B per-question output (Medium Priority #5).

Loads experiment_B_per_question.json and re-computes gold chunk IDs for every
question BOTH stamp-only and with text fallback, reporting how gold
identification changes when the text-fallback resolver is enabled.

Writes:
  evaluation/out/ceiling_v5/experiment_b_text_fallback_analysis.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

from evaluation.benchmark import load_questions
from evaluation.config import CACHE_DIR
from evaluation.resolution import FamilyMap, matches_gold, payload_to_keys

OUT = ROOT / "evaluation" / "out" / "ceiling_v5"
PER_QUESTION_JSON = OUT / "experiment_B_per_question.json"
PAYLOAD_INDEX = CACHE_DIR / "payload_index.jsonl"
OUTPUT_JSON = OUT / "experiment_b_text_fallback_analysis.json"


def load_payload_index() -> dict[str, dict]:
    index: dict[str, dict] = {}
    with open(PAYLOAD_INDEX, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            index[rec["id"]] = rec["payload"]
    return index


def main():
    print("Loading experiment_B_per_question.json ...")
    per_question = json.loads(PER_QUESTION_JSON.read_text(encoding="utf-8"))

    print("Loading payload index ...")
    payload_index = load_payload_index()
    print(f"  {len(payload_index)} payload points")

    print("Loading questions + family map ...")
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}

    # Build stamp-based index (same as experiment_b's gold_chunk_ids_for_units)
    by_section: dict[str, dict[str, set[str]]] = {}
    by_family: dict[str, set[str]] = {}
    fam_chunks_list: dict[str, list[str]] = {}  # family -> chunk IDs for text scan
    for cid, pl in payload_index.items():
        for fam, sec in payload_to_keys(pl, family_map):
            by_family.setdefault(fam, set()).add(cid)
            fam_chunks_list.setdefault(fam, []).append(cid)
            if sec is not None:
                by_section.setdefault(fam, {}).setdefault(str(sec), set()).add(cid)


    def stamp_based(units) -> set[str]:
        ids: set[str] = set()
        for u in units:
            if u.section is None:
                ids |= by_family.get(u.family, set())
            else:
                ids |= by_section.get(u.family, {}).get(str(u.section), set())
        return ids


    def text_fallback_based(units) -> set[str]:
        """Full text-fallback resolution: stamp hits + resolve_by_text."""
        ids = stamp_based(units)
        for u in units:
            # Only scan in-family chunks for text fallback
            candidates = fam_chunks_list.get(u.family, [])
            for pid in candidates:
                if pid in ids:
                    continue
                pl = payload_index.get(pid, {})
                if matches_gold(pl, u, family_map, text_fallback=True):
                    ids.add(pid)
        return ids

    results: dict[str, Any] = {
        "n_questions": 0,
        "questions_with_text_fallback_delta": 0,
        "total_stamp_gold_chunks": 0,
        "total_text_fallback_gold_chunks": 0,
        "total_additional_gold_chunks": 0,
        "per_question": {},
    }

    qids = sorted(per_question.keys())
    results["n_questions"] = len(qids)

    for qi, qid in enumerate(qids):
        qd = per_question[qid]
        q = questions.get(qid)
        if q is None:
            continue
        units = q.recall_units()

        if qi % 25 == 0:
            print(f"  processing {qi}/{len(qids)} ({qid}) ...", flush=True)

        stamp_ids = stamp_based(units)
        tex_ids = text_fallback_based(units)
        additional = tex_ids - stamp_ids

        old_n_gold = qd.get("n_gold_chunks_total", 0)
        delta = len(tex_ids) - old_n_gold

        k_curve = qd.get("k_curve", {})
        oracle = qd.get("oracle", {})
        cited_ids: set[str] = set()
        if oracle:
            cited_ids |= set(oracle.get("cited_chunk_ids", []))
            cited_ids |= set(oracle.get("context_ids", []))
        for k_rec in k_curve.values():
            cited_ids |= set(k_rec.get("cited_chunk_ids", []))
            cited_ids |= set(k_rec.get("context_ids", []))

        tf_in_cited = len(additional & cited_ids)

        entry = {
            "question": qd.get("question", "")[:120],
            "n_gold_units": qd.get("n_gold_units", 0),
            "stamp_gold_chunks": len(stamp_ids),
            "text_fallback_gold_chunks": len(tex_ids),
            "additional_chunks_via_text_fallback": len(additional),
            "text_fallback_chunks_in_cited": tf_in_cited,
            "n_gold_chunks_total_in_output": old_n_gold,
            "delta": delta,
            "ce_gold_rank": qd.get("ce_gold_rank"),
            "additional_sample": sorted(additional)[:5],
        }
        results["per_question"][qid] = entry

        results["total_stamp_gold_chunks"] += len(stamp_ids)
        results["total_text_fallback_gold_chunks"] += len(tex_ids)
        results["total_additional_gold_chunks"] += len(additional)
        if additional:
            results["questions_with_text_fallback_delta"] += 1

    delta_counts = {"0_delta": 0, "1-5": 0, "6-20": 0, "21+": 0}
    for e in results["per_question"].values():
        d = e["additional_chunks_via_text_fallback"]
        if d == 0:
            delta_counts["0_delta"] += 1
        elif d <= 5:
            delta_counts["1-5"] += 1
        elif d <= 20:
            delta_counts["6-20"] += 1
        else:
            delta_counts["21+"] += 1
    results["delta_distribution"] = delta_counts

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {OUTPUT_JSON}")
    print(f"  Questions analysed:    {results['n_questions']}")
    print(f"  Questions with text-fallback delta: {results['questions_with_text_fallback_delta']}")
    print(f"  Total stamp gold chunks:  {results['total_stamp_gold_chunks']}")
    print(f"  Total text-fallback gold chunks: {results['total_text_fallback_gold_chunks']}")
    print(f"  Additional via text fallback: {results['total_additional_gold_chunks']}")
    print(f"  Delta distribution: {delta_counts}")

    print("\nTop 10 questions by additional text-fallback gold chunks:")
    sorted_qs = sorted(
        results["per_question"].items(),
        key=lambda kv: kv[1]["additional_chunks_via_text_fallback"],
        reverse=True,
    )[:10]
    for qid, e in sorted_qs:
        print(f"  {qid}: +{e['additional_chunks_via_text_fallback']} "
              f"(stamp={e['stamp_gold_chunks']}, tf={e['text_fallback_gold_chunks']}, "
              f"ce_rank={e['ce_gold_rank']})")


if __name__ == "__main__":
    main()

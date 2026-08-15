"""Ranking-failure dataset — structured records for every ranking failure.

Reads the K=500 live checkpoint and the hard-negative mining output to
produce a machine-readable dataset of ranking failures, following the
master plan Section 7 schema.

Output: evaluation/out/cache/ranking_failures.jsonl
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.config import OUT_DIR  # noqa: E402
from evaluation.failure_taxonomy import classify_failure  # noqa: E402

CEILING_V5 = OUT_DIR / "ceiling_v5"
MINING_FILE = OUT_DIR / "ceiling_v5" / "hard_negative_mining.jsonl"
OUT_FILE = OUT_DIR / "cache" / "ranking_failures.jsonl"
STATS_FILE = OUT_DIR / "cache" / "ranking_failure_stats.json"


def _extract_ids(payload: dict) -> dict:
    """Extract legal identity fields from a payload."""
    section = str(payload.get("section_number") or "")
    m = re.match(r"\s*(\d{1,4})", section)
    sec_num = m.group(1) if m else None

    subsection = str(payload.get("subsection") or "")
    m2 = re.match(r"\((\d+)\)|\(([ivxlc]+)\)|\(([a-z]+)\)", subsection)
    sub_num = m2.group(1) if m2 else None

    return {
        "act_name": payload.get("act_name", ""),
        "document_title": payload.get("document_title", ""),
        "section": sec_num,
        "subsection": sub_num,
        "authority": payload.get("authority", ""),
        "jurisdiction": payload.get("jurisdiction", ""),
        "temporal_status": payload.get("temporal_status", ""),
    }


def build_ranking_failures() -> int:
    """Build the ranking-failure dataset from mining output."""
    if not MINING_FILE.exists():
        print(f"[ranking_failure_dataset] Mining file not found: {MINING_FILE}", file=sys.stderr)
        print("  Run: python -m evaluation.hard_negative_miner --offline", file=sys.stderr)
        return 1

    from evaluation.benchmark import load_questions
    from evaluation.resolution import FamilyMap

    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}
    payload_index = {}

    # Load payload index
    pi_path = OUT_DIR.parent / "cache" / "payload_index.jsonl"
    if pi_path.exists():
        with open(pi_path, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                payload_index[str(rec["id"])] = rec["payload"]

    failures = []
    cat_counts: dict[str, int] = {}

    with open(MINING_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            qid = rec["question_id"]

            q = questions.get(qid)
            if not q:
                continue

            rel = q.relevant_units()
            if not rel:
                continue

            positives = rec.get("positives", [])
            negatives = rec.get("negatives", [])

            if not positives or not negatives:
                continue

            # For each negative, build a failure record
            for neg in negatives:
                gold_unit = None
                for u in rel:
                    if u.provision_id == neg.get("gold_unit"):
                        gold_unit = u
                        break
                if not gold_unit:
                    gold_unit = rel[0]

                gold_payload = {}
                for pos in positives:
                    if pos.get("gold_unit") == gold_unit.provision_id:
                        gold_payload = payload_index.get(pos["chunk_id"], {
                            "chunk_text": pos.get("text", ""),
                            "section_number": pos.get("section"),
                            "act_name": pos.get("act_name", ""),
                        })
                        break

                neg_payload = payload_index.get(neg["chunk_id"], {
                    "chunk_text": neg.get("text", ""),
                    "section_number": neg.get("section"),
                    "act_name": neg.get("act_name", ""),
                    "document_title": neg.get("document_title", ""),
                })

                # Classify the failure
                category = classify_failure(gold_unit, gold_payload, neg_payload, family_map)
                cat_counts[category] = cat_counts.get(category, 0) + 1

                gold_ids = _extract_ids(gold_payload)
                neg_ids = _extract_ids(neg_payload)

                failure_record = {
                    "question_id": qid,
                    "query": q.question,
                    "gold_provision_id": gold_unit.provision_id,
                    "gold_document_id": gold_payload.get("document_id", ""),
                    "gold_rank": -1,  # unknown without live retrieval
                    "predicted_document_id": neg["chunk_id"],
                    "predicted_rank": neg.get("rank", -1),
                    # Legal identity comparison
                    "gold_act": gold_ids["act_name"],
                    "predicted_act": neg_ids["act_name"],
                    "gold_section": gold_ids["section"],
                    "predicted_section": neg_ids["section"],
                    "gold_subsection": gold_ids["subsection"],
                    "predicted_subsection": neg_ids["subsection"],
                    "gold_authority": gold_ids["authority"],
                    "predicted_authority": neg_ids["authority"],
                    "gold_jurisdiction": gold_ids["jurisdiction"],
                    "predicted_jurisdiction": neg_ids["jurisdiction"],
                    "gold_temporal_validity": gold_ids["temporal_status"],
                    "predicted_temporal_validity": neg_ids["temporal_status"],
                    # Scores
                    "retrieval_scores": {},
                    "semantic_score": neg.get("features", {}).get("word_overlap", 0.0),
                    "identifier_score": neg.get("features", {}).get("same_family", 0.0),
                    "CE_score": 0.0,
                    "ensemble_score": neg.get("score", 0.0),
                    # Classification
                    "failure_category": category,
                    "failure_category_label": {
                        "A_same_act_wrong_section": "Same Act, wrong section",
                        "B_same_section_wrong_subsection": "Same section, wrong subsection",
                        "C_same_legal_concept": "Same legal concept",
                        "D_same_terminology": "Same terminology",
                        "E_procedural_vs_substantive": "Procedural vs substantive",
                        "F_definition_vs_operative": "Definition vs operative",
                        "G_exception_vs_general_rule": "Exception vs general rule",
                        "H_cross_reference_failure": "Cross-reference failure",
                        "I_authority_jurisdiction_mismatch": "Authority/jurisdiction mismatch",
                        "J_temporal_version_error": "Temporal-version error",
                        "K_adjacent_section_confusion": "Adjacent-section confusion",
                        "L_multi_provision_requirement": "Multi-provision requirement",
                        "unclassified": "Unclassified",
                    }.get(category, "Unknown"),
                    "negative_tier": neg.get("tier", 0),
                    "negative_features": neg.get("features", {}),
                }
                failures.append(failure_record)

    # Write output
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        for rec in failures:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    summary = {
        "total_failures": len(failures),
        "questions_with_failures": len({r["question_id"] for r in failures}),
        "category_counts": cat_counts,
        "tier_counts": {
            str(t): sum(1 for r in failures if r.get("negative_tier") == t)
            for t in range(1, 4)
        },
    }
    STATS_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


def main() -> int:
    return build_ranking_failures()


if __name__ == "__main__":
    raise SystemExit(main())

"""Evaluate the deterministic section-based classifier (identifier_query)
against the 150-question benchmark gold annotations.

This script is READ-ONLY — it does not modify any pipeline code, configs,
checkpoints, or data files.  It only reads benchmark questions and the
identifier classifier, then writes results to a NEW file.

Usage:
    python -m evaluation.eval_identifier_classifier
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.benchmark import load_questions
from app.rag.retrieval.identifier import identifier_query, detect_act, detect_section

OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "identifier_classifier_eval.json"


def norm(text: str) -> str:
    return text.strip().lower()


def main() -> int:
    questions = load_questions()

    per_q = []
    total = len(questions)

    # Counters
    gold_has_act = 0
    gold_has_section = 0
    gold_has_both = 0
    detected_any = 0
    detected_act_correct = 0
    detected_act_total = 0
    detected_section_correct = 0
    detected_section_total = 0
    detected_both_correct = 0
    detected_both_total = 0
    false_pos_act = 0
    false_pos_section = 0

    # Also track questions where gold is detectable but classifier missed
    missed_both = []
    missed_act_only = []
    missed_section_only = []

    print(f"Evaluating identifier classifier on {total} benchmark questions...")
    print("=" * 80)

    for q in questions:
        # Gold signals from gold_units
        gold_acts = set()
        gold_sections = set()
        gold_act_sections = set()  # (act, section) pairs
        for u in q.gold_units:
            if u.act:
                gold_acts.add(norm(u.act))
            if u.section:
                gold_sections.add(u.section)
            if u.act and u.section:
                gold_act_sections.add((norm(u.act), u.section))

        has_gold_act = len(gold_acts) > 0
        has_gold_section = len(gold_sections) > 0
        has_gold_both = len(gold_act_sections) > 0

        if has_gold_act:
            gold_has_act += 1
        if has_gold_section:
            gold_has_section += 1
        if has_gold_both:
            gold_has_both += 1

        # Run the classifier
        query_text, meta = identifier_query(q.question)
        detected_form = meta.get("form", "none")
        detected_act = meta.get("act")
        detected_section = meta.get("section")

        if detected_act or detected_section:
            detected_any += 1

        # Act evaluation
        act_correct = False
        if detected_act:
            detected_act_total += 1
            det_act_norm = norm(detected_act)
            if det_act_norm in gold_acts:
                detected_act_correct += 1
                act_correct = True
            else:
                false_pos_act += 1
        elif has_gold_act:
            missed_act_only.append(q.question_id)

        # Section evaluation
        section_correct = False
        if detected_section:
            detected_section_total += 1
            if detected_section in gold_sections:
                detected_section_correct += 1
                section_correct = True
            else:
                false_pos_section += 1
        elif has_gold_section:
            missed_section_only.append(q.question_id)

        # Combined (act + section both correct)
        if detected_act and detected_section:
            detected_both_total += 1
            det_as = (norm(detected_act), detected_section)
            if det_as in gold_act_sections:
                detected_both_correct += 1

        # Track missed questions (gold has both, but classifier missed at least one)
        if has_gold_both:
            if not (act_correct and section_correct):
                missed_both.append(q.question_id)

        per_q.append({
            "question_id": q.question_id,
            "question": q.question[:300],
            "difficulty": q.difficulty,
            "insufficient_evidence": q.insufficient_evidence,
            "gold_acts": sorted(gold_acts),
            "gold_sections": sorted(gold_sections),
            "gold_act_section_pairs": [list(p) for p in sorted(gold_act_sections)],
            "detected_form": detected_form,
            "detected_act": detected_act,
            "detected_section": detected_section,
            "detected_query": query_text,
            "act_correct": act_correct,
            "section_correct": section_correct,
            "both_correct": (act_correct and section_correct) if has_gold_both else None,
        })

    # ---- Print summary ----
    print("\n--- ACT DETECTION ---")
    print(f"  Questions with gold act mention: {gold_has_act}/{total} ({gold_has_act/total*100:.1f}%)")
    print(f"  Questions where classifier detected an act: {detected_act_total}/{total} ({detected_act_total/total*100:.1f}%)")
    print(f"  Correct act detections: {detected_act_correct}/{detected_act_total} ({detected_act_correct/max(detected_act_total,1)*100:.1f}%)")
    print(f"  False positive act detections: {false_pos_act}/{detected_act_total} ({false_pos_act/max(detected_act_total,1)*100:.1f}%)")
    print(f"  Recall (detected correct / questions with gold act): {detected_act_correct}/{gold_has_act} ({detected_act_correct/max(gold_has_act,1)*100:.1f}%)")

    print("\n--- SECTION DETECTION ---")
    print(f"  Questions with gold section mention: {gold_has_section}/{total} ({gold_has_section/total*100:.1f}%)")
    print(f"  Questions where classifier detected a section: {detected_section_total}/{total} ({detected_section_total/total*100:.1f}%)")
    print(f"  Correct section detections: {detected_section_correct}/{detected_section_total} ({detected_section_correct/max(detected_section_total,1)*100:.1f}%)")
    print(f"  False positive section detections: {false_pos_section}/{detected_section_total} ({false_pos_section/max(detected_section_total,1)*100:.1f}%)")
    print(f"  Recall (detected correct / questions with gold section): {detected_section_correct}/{gold_has_section} ({detected_section_correct/max(gold_has_section,1)*100:.1f}%)")

    print("\n--- COMBINED ACT+SECTION DETECTION ---")
    print(f"  Questions with gold act+section pairs: {gold_has_both}/{total} ({gold_has_both/total*100:.1f}%)")
    print(f"  Questions where classifier detected both act+section correctly: {detected_both_correct}/{gold_has_both} ({detected_both_correct/max(gold_has_both,1)*100:.1f}%)")
    print(f"  Questions missed (gold has both, classifier missed at least one): {len(missed_both)}/{gold_has_both} ({len(missed_both)/max(gold_has_both,1)*100:.1f}%)")

    print("\n--- DETECTION COVERAGE ---")
    print(f"  Questions where classifier detected ANY identifier: {detected_any}/{total} ({detected_any/total*100:.1f}%)")
    no_gold = total - gold_has_act - gold_has_section + gold_has_both
    print(f"  Questions with NO gold act or section: {no_gold}/{total} ({no_gold/total*100:.1f}%)")

    # Breakdown by detection form
    from collections import Counter
    form_counts = Counter(q["detected_form"] for q in per_q)
    print(f"\n  Detection form distribution:")
    for form in ["act+section", "act", "section", "none"]:
        count = form_counts.get(form, 0)
        print(f"    {form}: {count}/{total} ({count/total*100:.1f}%)")

    print("\n--- DIFFICULTY BREAKDOWN ---")
    for diff in ["EASY", "MEDIUM", "HARD"]:
        diff_qs = [q for q in questions if q.difficulty == diff]
        if not diff_qs:
            print(f"  {diff}: no questions of this difficulty")
            continue
        diff_ids = set(q.question_id for q in diff_qs)
        diff_per_q = [q for q in per_q if q["question_id"] in diff_ids]
        both_correct = sum(1 for q in diff_per_q if q["both_correct"] is True)
        gold_both = sum(1 for q in diff_per_q if q["gold_act_section_pairs"])
        if gold_both > 0:
            print(f"  {diff}: {len(diff_qs)} questions, {gold_both} have gold act+section, {both_correct} correctly detected ({both_correct/gold_both*100:.1f}%)")

    print(f"\n--- MISSED QUESTIONS (gold has act+section, classifier missed at least one) ---")
    print(f"  Count: {len(missed_both)}/{gold_has_both} ({len(missed_both)/max(gold_has_both,1)*100:.1f}% miss rate)")
    if missed_both:
        print(f"  First 10 QIDs: {missed_both[:10]}")

    # ---- Write results ----
    output = {
        "classifier": "app.rag.retrieval.identifier.identifier_query (deterministic act+section detection)",
        "n_questions": total,
        "summary": {
            "gold_has_act": gold_has_act,
            "gold_has_section": gold_has_section,
            "gold_has_both": gold_has_both,
            "detected_any": detected_any,
            "detected_act_total": detected_act_total,
            "detected_act_correct": detected_act_correct,
            "detected_act_false_positive": false_pos_act,
            "act_precision": round(detected_act_correct / max(detected_act_total, 1), 4),
            "act_recall": round(detected_act_correct / max(gold_has_act, 1), 4),
            "detected_section_total": detected_section_total,
            "detected_section_correct": detected_section_correct,
            "detected_section_false_positive": false_pos_section,
            "section_precision": round(detected_section_correct / max(detected_section_total, 1), 4),
            "section_recall": round(detected_section_correct / max(gold_has_section, 1), 4),
            "detected_both_correct": detected_both_correct,
            "combined_precision": round(detected_both_correct / max(detected_both_total, 1), 4),
            "combined_recall": round(detected_both_correct / max(gold_has_both, 1), 4),
            "missed_count": len(missed_both),
            "miss_rate": round(len(missed_both) / max(gold_has_both, 1), 4),
        },
        "form_distribution": {form: count for form, count in form_counts.items()},
        "missed_question_ids": missed_both,
        "missed_act_only_ids": missed_act_only,
        "missed_section_only_ids": missed_section_only,
        "per_question": per_q,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults written to: {OUT_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

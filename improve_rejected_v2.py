import json
from pathlib import Path

# Load data
with open("benchmark/stratified_eval_sample_v2_audit.json", encoding="utf-8") as f:
    audit = json.load(f)

with open("benchmark/gold_provisions_templates_v1.json", encoding="utf-8") as f:
    gold_data = json.load(f)

gold_questions = gold_data.get("questions", [])

with open("benchmark/stratified_eval_sample_v2_cleaned.json", encoding="utf-8") as f:
    cleaned = json.load(f)

gold_by_qid = {gq["qid"]: gq for gq in gold_questions}

print("IMPROVEMENT PIPELINE FOR REJECTED ITEMS")
print("=" * 80)

rejected = [q for q in audit["questions"] if q.get("review_status") == "reject"]
print(f"Total rejected items: {len(rejected)}")
print()

improved_items = []
not_improvable = []
already_cleaned = []
no_matching_gold = []

for item in rejected:
    qid = item["qid"]
    original = item["question"]
    reason = item.get("review_reason", "Unknown reason")

    if qid not in gold_by_qid:
        not_improvable.append((item, f"No gold standard question for qid {qid}"))
        continue

    gold_q = gold_by_qid[qid]
    improved_q = gold_q.get("question", "")

    if improved_q and improved_q.lower() == original.lower():
        not_improvable.append((item, "Gold question identical to rejected question"))
        continue

    if any(q["qid"] == qid for q in cleaned["questions"]):
        already_cleaned.append((item, "Already in cleaned benchmark"))
        continue

    act = item.get("act", "")
    section = item.get("section", "")
    domain = item.get("domain", "")
    title = item.get("title", "")
    template = item.get("template", "")

    gold_lower = improved_q.lower()
    if "maximum penalty" in gold_lower or "penalty" in gold_lower:
        answer_type = "penalty"
    elif "prison term" in gold_lower or "imprisonment" in gold_lower:
        answer_type = "imprisonment"
    elif "fine" in gold_lower:
        answer_type = "fine"
    elif "does" in gold_lower and "state" in gold_lower:
        answer_type = "question"
    elif "what" in gold_lower:
        answer_type = "duty_operator"
    else:
        answer_type = "general"

    improved_item = {
        "qid": qid,
        "question": improved_q,
        "section": section,
        "act": act,
        "title": title,
        "domain": domain,
        "source_template": template,
        "benchmark_status": "improved",
        "original_rejected_question": original,
        "improvement_reason": reason,
        "answer_type": answer_type,
        "answer_scoring": {
            "2": "Correct legal answer that directly addresses the cited provision.",
            "1": "Substantially correct but incomplete or minor wording/qualification issue.",
            "0": "Incorrect answer or unsupported legal assertion.",
            "NA": "Use only if later source verification establishes that the question itself is invalid.",
        },
    }

    improved_items.append(improved_item)
    print(f"IMPROVED: {qid}")
    print(f"   Original: {original[:90]}")
    print(f"   Improved: {improved_q[:90]}")
    print(f"   Answer type: {answer_type}")
    print()
print("=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"Total rejected: {len(rejected)}")
print(f"Improved:       {len(improved_items)}")
total_handled = len(improved_items) + len(not_improvable) + len(already_cleaned) + len(no_matching_gold)
print(f"Total handled: {total_handled}")
assert total_handled == len(rejected), "Mismatch in item accounting"

# Save the improved items
if improved_items:
    output_data = {
        "version": "stratified-sample-v2-improved",
        "summary": {
            "source": "stratified_eval_sample_v2_audit.json",
            "n_original_rejected": len(rejected),
            "n_improved": len(improved_items),
            "n_not_improvable": len(not_improvable),
            "n_already_cleaned": len(already_cleaned),
            "n_no_matching_gold": len(no_matching_gold),
            "n_existing_cleaned": len(cleaned["questions"]),
            "n_total_after": len(cleaned["questions"]) + len(improved_items),
            "recommendation": "Improved items are added to the cleaned benchmark with 'improved' status. Not-improvable items remain in the rejection pool for future human review.",
            "scoring": {
                "2": "Correct legal answer that directly addresses the cited provision.",
                "1": "Substantially correct but incomplete or minor wording/qualification issue.",
                "0": "Incorrect answer or unsupported legal assertion.",
                "NA": "Invalid benchmark question.",
            },
        },
        "not_improvable_reasons": {
            "no_gold_standard": "No corresponding gold standard question in gold_provisions_templates_v1.json",
            "identical_question": "Gold question identical to rejected question - no actual improvement possible",
        },
        "questions": improved_items,
    }

    improved_path = Path("benchmark/stratified_eval_sample_v2_improved.json")
    with open(improved_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)

    print()
    print(f"SAVED: Improved items written to {improved_path}")
    print(f"Count: {len(improved_items)} improved questions")
    print()

    # Also save a report of not-improvable items
    if not_improvable or no_matching_gold or already_cleaned:
        report = {
            "version": "stratified-sample-v2-rejected-report",
            "rejected_items": [],
            "already_cleaned": already_cleaned,
            "no_matching_gold": no_matching_gold,
        }
        for item, reason in not_improvable:
            report["rejected_items"].append({
                "qid": item["qid"],
                "question": item["question"],
                "act": item["act"],
                "section": item["section"],
                "reason": item.get("review_reason", "Unknown"),
                "improvement_blocker": reason,
                "recommendation": "Requires manual review and human-written improved question",
            })
        for item, reason in no_matching_gold:
            report["rejected_items"].append({
                "qid": item["qid"],
                "question": item["question"],
                "act": item["act"],
                "section": item["section"],
                "reason": item.get("review_reason", "Unknown"),
                "improvement_blocker": reason,
                "recommendation": "Requires manual review and human-written improved question",
            })
        report_path = Path("benchmark/stratified_eval_sample_v2_rejected_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"REPORT: Rejected/report items written to {report_path}")
else:
    print("No improved items to save")

print()
print("=" * 80)
print("DONE")
print("=" * 80)
print(f"Not improvable: {len(not_improvable)}")
print(f"Already cleaned: {len(already_cleaned)}")
print(f"No gold match:  {len(no_matching_gold)}")
print()
total_handled = len(improved_items) + len(not_improvable) + len(already_cleaned) + len(no_matching_gold)
print(f"Total handled: {total_handled}")
assert total_handled == len(rejected), "Mismatch in item accounting"

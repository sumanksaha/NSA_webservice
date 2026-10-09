import json
from pathlib import Path

# Load the audit data with rejected items
with open("benchmark/stratified_eval_sample_v2_audit.json", encoding="utf-8") as f:
    audit = json.load(f)

# Load gold provisions for context
with open("benchmark/gold_provisions_templates_v1.json", encoding="utf-8") as f:
    gold_provisions = json.load(f)

# Find all rejected items
rejected = [q for q in audit["questions"] if q.get("review_status") == "reject"]
print(f"Total rejected: {len(rejected)}")

# Load the existing cleaned benchmark to extend if needed
with open("benchmark/stratified_eval_sample_v2_cleaned.json", encoding="utf-8") as f:
    cleaned = json.load(f)

existing_qids = {q["qid"] for q in cleaned["questions"]}

improved_items = []
rejected_count = 0
kept_count = 0

for item in rejected:
    qid = item["qid"]
    recommended = item.get("recommended_question")

    if not recommended:
        print(f"\nSKIP {qid}: No recommended question")
        continue

    # Check if recommended is just a repeat or actually improved
    original = item["question"]
    is_improved = recommended != original

    if not is_improved:
        print(f"\nSKIP {qid}: Recommended question identical to original")
        continue

    # Check if qid already exists in cleaned
    if qid in existing_qids:
        print(f"\nSKIP {qid}: Already exists in cleaned")
        continue

    # Find relevant provision in gold_provisions
    relevant_provisions = []
    act = item["act"]
    section = item["section"]

    # Search through gold provisions for relevant content
    for provision in gold_provisions:
        if provision.get("act") == act:
            # Try to match section
            if section in provision.get("section", "") or section in provision.get("full_provision", ""):
                relevant_provisions.append(provision)

    if not relevant_provisions:
        # Try a broader search
        for provision in gold_provisions:
            if act in provision.get("full_provision", "") or act in provision.get("act", ""):
                relevant_provisions.append(provision)

    # Determine if improved question is answerable
    is_answerable = len(relevant_provisions) > 0

    print(f"\n{qid}")
    print(f"   Original: {original}")
    print(f"   Recommended: {recommended}")
    print(f"   Act: {act}, Section: {section}")
    print(f"   Found {len(relevant_provisions)} relevant provisions")
    print(f"   Is answerable: {is_answerable}")

    # Create improved version
    improved = {
        "qid": qid,
        "question": recommended,
        "section": section,
        "act": act,
        "title": item["title"],
        "domain": item["domain"],
        "source_template": item["template"],
        "benchmark_status": "improved",
        "answer_scoring": {
            "2": "Correct legal answer that directly addresses the cited provision.",
            "1": "Substantially correct but incomplete or minor wording/qualification issue.",
            "0": "Incorrect answer or unsupported legal assertion.",
            "NA": "Use only if later source verification establishes that the question itself is invalid.",
        },
    }

    if is_answerable and relevant_provisions:
        best_provision = relevant_provisions[0]
        gold_answer = best_provision.get("template", "")
        if not gold_answer:
            gold_answer = ""

        improved["gold_answer"] = gold_answer
        print("   IMPROVED - Answerable")
        kept_count += 1
        improved_items.append(improved)
    else:
        print("   NOT IMPROVED - No relevant provision")
        rejected_count += 1

# Summary
print("\n" + "=" * 60)
print("IMPROVEMENT SUMMARY")
print("=" * 60)
print(f"Rejected items: {len(rejected)}")
print(f"Kept/Improved: {kept_count}")
print(f"Still Rejected: {rejected_count}")

# Save improved items
if improved_items:
    improved_data = {
        "version": "stratified-sample-v2-improved",
        "summary": {
            "source": "stratified_eval_sample_v1(1).json",
            "n_original": len(rejected),
            "n_improved": len(improved_items),
            "n_still_rejected": rejected_count,
            "n_existing_cleaned": len(cleaned["questions"]),
            "n_total_after": len(cleaned["questions"]) + len(improved_items),
            "recommendation": "Improved items are added to the cleaned benchmark with 'improved' status.",
            "scoring": {
                "2": "Correct legal answer that directly addresses the cited provision.",
                "1": "Substantially correct but incomplete or minor wording/qualification issue.",
                "0": "Incorrect answer or unsupported legal assertion.",
                "NA": "Invalid benchmark question.",
            },
        },
        "questions": improved_items,
    }

    improved_path = Path("benchmark/stratified_eval_sample_v2_improved.json")
    with open(improved_path, "w", encoding="utf-8") as f:
        json.dump(improved_data, f, indent=2)

    print(f"\nSaved improved items to: {improved_path}")
    print("Improved items ready for integration")

print("\n" + "=" * 60)
print("DONE")
print("=" * 60)

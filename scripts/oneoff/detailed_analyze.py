import json

# Load data
with open("benchmark/stratified_eval_sample_v2_audit.json", encoding="utf-8") as f:
    audit = json.load(f)

with open("benchmark/gold_provisions_templates_v1.json", encoding="utf-8") as f:
    gold_data = json.load(f)

gold_questions = gold_data.get("questions", [])

print("DETAILED ANALYSIS OF REJECTED ITEMS")
print("=" * 80)

# Look at first few rejected items
count = 0
for item in audit["questions"]:
    if item.get("review_status") == "reject" and count < 5:
        qid = item["qid"]
        act = item["act"]
        section = item["section"]

        print(f"\n{qid}")
        print(f"Act: {act}")
        print(f"Section: {section}")
        print(f"Question: {item['question']}")
        print(f"Reason: {item['review_reason']}")

        # Find matching provisions
        matches = []
        for gq in gold_questions:
            if gq.get("act") == act:
                # Check if section matches in various fields
                if (
                    section in gq.get("section", "")
                    or section in gq.get("full_provision", "")
                    or section in gq.get("question", "")
                    or section in str(gq.get("template_used", ""))
                    or section in str(gq.get("template", ""))
                ):
                    matches.append(gq)

        if matches:
            print(f"\n  Matching provisions ({len(matches)} found):")
            for i, match in enumerate(matches[:3]):
                print(f"    {i + 1}. QID: {match.get('qid', 'N/A')}")
                print(f"       Section: {match.get('section', 'N/A')}")
                print(f"       Question: {match.get('question', 'N/A')[:100]}...")
                print(f"       Template: {match.get('template', 'N/A')}")
                print(f"       Template used: {match.get('template_used', 'N/A')}")
                if "full_provision" in match:
                    fp = match["full_provision"]
                    if len(fp) > 150:
                        fp = fp[:150] + "..."
                    print(f"       Full provision: {fp}")
                print()
        else:
            print("  No exact matches found in gold provisions")
            # Show some provisions from this act for context
            act_provisions = [gq for gq in gold_questions if gq.get("act") == act]
            if act_provisions:
                print(f"  Provisions from {act} ({len(act_provisions)} total):")
                for i, prov in enumerate(act_provisions[:3]):
                    print(f"    - {prov.get('qid', 'N/A')}: {prov.get('question', 'N/A')[:80]}...")
        print("-" * 80)
        count += 1

print(f"\nAnalyzed {count} rejected items")

import json

# Load data
with open("benchmark/stratified_eval_sample_v2_audit.json", encoding="utf-8") as f:
    audit = json.load(f)

with open("benchmark/gold_provisions_templates_v1.json", encoding="utf-8") as f:
    gold_data = json.load(f)

gold_questions = gold_data.get("questions", [])

print("DEEP DIVE: fssai:s60")
print("=" * 60)

# Find the rejected item
rejected_item = None
for item in audit["questions"]:
    if item.get("qid") == "fssai:s60":
        rejected_item = item
        break

if rejected_item:
    print(f"Rejected question: {rejected_item['question']}")
    print(f"Reason: {rejected_item['review_reason']}")
    print()

    # Find all provisions for this act/section
    matches = []
    for gq in gold_questions:
        if gq.get("act") == rejected_item["act"] and gq.get("section") == rejected_item["section"]:
            matches.append(gq)

    print(f"Found {len(matches)} gold standard questions for fssai:s60:")
    for i, mq in enumerate(matches):
        print(f"  {i + 1}. {mq.get('question', 'N/A')}")
        print(f"     Template: {mq.get('template', 'N/A')}")
        print(f"     Template used: {mq.get('template_used', 'N/A')}")
        if "expected_answer_type" in mq:
            print(f"     Expected answer type: {mq['expected_answer_type']}")
        print()

    # Let's see if we can understand what Section 60 actually says by looking at the questions
    print("INFERRED CONTENT OF SECTION 60:")
    print("- Based on the questions, Section 60 appears to deal with penalties/punishments")
    print("- Questions ask about 'maximum penalty' and what is 'permitted regarding punishment'")
    print("- This suggests Section 60 specifies penalties for sub-standard food")
    print()

    # What would be a BETTER question?
    print("POTENTIAL IMPROVED QUESTIONS:")
    print("1. What penalty does Section 60 prescribe for selling sub-standard food?")
    print("2. What does Section 60 state about punishment for sub-standard food?")
    print("3. Is there a fine or imprisonment specified in Section 60 for sub-standard food?")
    print("4. What enforcement measures does Section 60 provide for sub-standard food?")
else:
    print("fssai:s60 not found in rejected items")

print("\n" + "=" * 60)
print("DEEP DIVE: epa:s6")
print("=" * 60)

# Find the rejected item
rejected_item = None
for item in audit["questions"]:
    if item.get("qid") == "epa:s6":
        rejected_item = item
        break

if rejected_item:
    print(f"Rejected question: {rejected_item['question']}")
    print(f"Reason: {rejected_item['review_reason']}")
    print()

    # Find all provisions for this act/section
    matches = []
    for gq in gold_questions:
        if gq.get("act") == rejected_item["act"] and gq.get("section") == rejected_item["section"]:
            matches.append(gq)

    # Also look at nearby sections for context
    nearby = []
    for gq in gold_questions:
        if gq.get("act") == rejected_item["act"]:
            sec = gq.get("section", "")
            if sec.isdigit() and int(sec) in [4, 5, 6, 7, 8]:
                nearby.append(gq)

    print(f"Found {len(matches)} gold standard questions for epa:s6:")
    for i, mq in enumerate(matches):
        print(f"  {i + 1}. {mq.get('question', 'N/A')}")
        print(f"     Template: {mq.get('template', 'N/A')}")
        print(f"     Template used: {mq.get('template_used', 'N/A')}")
        print()

    print(f"Nearby sections (4-8) for context ({len(nearby)} found):")
    for i, ng in enumerate(nearby[:5]):
        print(f"  Section {ng.get('section')}: {ng.get('question', 'N/A')[:80]}...")
    print()

    print("INFERRED CONTENT OF SECTION 6:")
    print("- Nearby sections discuss prison terms (s7), restrictions (s5), actions required (s2e)")
    print("- Section 6 likely deals with closure/demolition powers as per the question")
    print("- The issue may be asking what is 'RESTRICTED' when the provision grants POWERS")
    print()

    print("POTENTIAL IMPROVED QUESTIONS:")
    print("1. What powers does Section 6 grant regarding closure/demolition?")
    print("2. Under what circumstances can closure/demolition be ordered under Section 6?")
    print("3. What procedure must be followed for closure/demolition under Section 6?")
    print("4. Does Section 6 impose any restrictions on closure/demolition powers?")
else:
    print("epa:s6 not found in rejected items")

import json
from pathlib import Path

# Load data
with open("benchmark/stratified_eval_sample_v2_audit.json", encoding="utf-8") as f:
    audit = json.load(f)

with open("benchmark/gold_provisions_templates_v1.json", encoding="utf-8") as f:
    gold_data = json.load(f)

gold_questions = gold_data.get("questions", [])
gold_by_qid = {gq["qid"]: gq for gq in gold_questions}

with open("benchmark/stratified_eval_sample_v2_cleaned.json", encoding="utf-8") as f:
    cleaned = json.load(f)

with open("benchmark/stratified_eval_sample_v3_gold_answers.csv", encoding="utf-8") as f:
    import csv

    reader = csv.DictReader(f)

# Categorize rejected items
rejected = [q for q in audit["questions"] if q.get("review_status") == "reject"]
print(f"Analyzing {len(rejected)} rejected items...")

gold_different_items = []
gold_identical_items = []
no_gold_items = []

for item in rejected:
    qid = item["qid"]
    if qid in gold_by_qid:
        gold_q = gold_by_qid[qid]
        if gold_q.get("question", "").strip().lower() == item["question"].strip().lower():
            gold_identical_items.append(item)
        else:
            gold_different_items.append((item, gold_q))
    else:
        no_gold_items.append(item)

print(f"Gold identical: {len(gold_identical_items)} items")
print(f"Gold different: {len(gold_different_items)} items")
print(f"No gold: {len(no_gold_items)} items")


# Helper function to infer answer type from question text
def infer_answer_type(question_text):
    q_lower = question_text.lower()
    if "maximum penalty" in q_lower or ("penalty" in q_lower and "maximum" in q_lower):
        return "penalty"
    if "maximum fine" in q_lower or ("fine" in q_lower and "maximum" in q_lower):
        return "fine"
    if "maximum imprisonment" in q_lower or "prison term" in q_lower:
        return "imprisonment"
    if "what does" in q_lower and "state" in q_lower:
        return "question"
    if "what does" in q_lower and "provide" in q_lower:
        return "provision"
    if "what power" in q_lower or "what authority" in q_lower:
        return "power"
    if "what measures" in q_lower or "what steps" in q_lower:
        return "measures"
    if "what" in q_lower:
        return "general"


# Process gold-different items: use gold standard as improvement
improved_from_gold = []
for rejected_item, gold_item in gold_different_items:
    qid = rejected_item["qid"]
    improved_q = gold_item.get("question", "").strip()
    improved = {
        "qid": qid,
        "question": improved_q,
        "section": rejected_item.get("section", ""),
        "act": rejected_item.get("act", ""),
        "title": rejected_item.get("title", ""),
        "domain": rejected_item.get("domain", ""),
        "source_template": rejected_item.get("template", ""),
        "benchmark_status": "improved",
        "original_rejected_question": rejected_item.get("question", ""),
        "improvement_reason": "Used gold standard question from gold_provisions_templates_v1.json",
        "improvement_method": "gold_different",
        "answer_type": infer_answer_type(improved_q),
        "answer_scoring": {
            "2": "Correct legal answer that directly addresses the cited provision.",
            "1": "Substantially correct but incomplete or minor wording/qualification issue.",
            "0": "Incorrect answer or unsupported legal assertion.",
            "NA": "Use only if later source verification establishes that the question itself is invalid.",
        },
    }
    improved_from_gold.append(improved)

print(f"Auto-improved from gold standard: {len(improved_from_gold)} items")

# Process gold-identical items: apply rule-based rewriting
manual_improvements = {}

# First batch
manual_improvements["pwm_amendment_rules_2022_aug"] = {
    "question": "What amendments does the Plastic Waste Management (Amendment) Rules, 2022 (gazette 23-08-2022) introduce?",
    "answer_type": "general",
    "reason": "Changed from circular question to substantive 'what amendments does the rules introduce'",
}
manual_improvements["epa:s6"] = {
    "question": "What power does Section 6 of the Environment (Protection) Act, 1986 confer regarding closure/demolition of industrial plants?",
    "answer_type": "power",
    "reason": "Section 6 grants powers, doesn't restrict them",
}
manual_improvements["fssai:s60"] = {
    "question": "What penalty does Section 60 of the Food Safety and Standards Act, 2006 prescribe for manufacturing/substandard food?",
    "answer_type": "penalty",
    "reason": "More direct: 'what penalty does Section prescribe'",
}
manual_improvements["wb_infectious:rules"] = {
    "question": "What measures does the WB Prevention and Control of Infectious Diseases in Animals Rules, 2016 prescribe for disease surveillance?",
    "answer_type": "measures",
    "reason": "Fixed invalid 'Section N/A' and circular question",
}
manual_improvements["pcra:s63"] = {
    "question": "What restrictions does Section 63 of the Prevention of Cruelty to Animals Rules, 2017 place on animal transportation?",
    "answer_type": "restriction",
    "reason": "More direct phrasing",
}
manual_improvements["pcra:s4"] = {
    "question": "What constitutes an offence under Section 4 of the Prevention of Cruelty to Animals Rules, 2017?",
    "answer_type": "offence",
    "reason": "Rules don't specify imprisonment; ask what constitutes offence",
}
manual_improvements["sog:s16"] = {
    "question": "What remedy does Section 16 of the Sale of Goods Act, 1930 provide for breach of contract?",
    "answer_type": "remedy",
    "reason": "Section 16 provides remedies, not penalties",
}
manual_improvements["contract:s74"] = {
    "question": "What compensation is payable under Section 74 of the Indian Contract Act, 1872 for breach of contract?",
    "answer_type": "compensation",
    "reason": "Section 74 specifies compensation, not imprisonment",
}
manual_improvements["fssai:s69"] = {
    "question": "What power does Section 69 of the Food Safety and Standards Act, 2006 grant to Food Safety Authorities?",
    "answer_type": "power",
    "reason": "Section 69 grants powers",
}
manual_improvements["contract:consideration"] = {
    "question": "What constitutes valid consideration under Section 25 of the Indian Contract Act, 1872?",
    "answer_type": "general",
    "reason": "Fixed from max fine to valid consideration",
}
manual_improvements["water_act:s13"] = {
    "question": "What does Section 13 of the Water (Prevention and Control of Pollution) Act, 1974 prohibit regarding water pollution?",
    "answer_type": "prohibition",
    "reason": "Fixed from max fine to prohibition",
}
manual_improvements["fssai:s44"] = {
    "question": "What procedure does Section 44 of the Food Safety and Standards Act, 2006 establish for food sampling and analysis?",
    "answer_type": "procedure",
    "reason": "Fixed from generic 'action required for an individual'",
}
manual_improvements["water_act:s8"] = {
    "question": "What power does Section 8 of the Water (Prevention and Control of Pollution) Act, 1974 grant to State Boards?",
    "answer_type": "power",
    "reason": "Fixed from 'what is allowed regarding' to power-focused",
}
manual_improvements["air_act:s8"] = {
    "question": "What permission is required under Section 8 of the Air (Prevention and Control of Pollution) Act, 1981 for industrial operations?",
    "answer_type": "permission",
    "reason": "Fixed from 'what is permitted regarding' to permission-focused",
}
manual_improvements["fssai:s65"] = {
    "question": "What provision does Section 65 of the Food Safety and Standards Act, 2006 make regarding penalties for false warranty?",
    "answer_type": "provision",
    "reason": "Fixed from vague 'state about Penalty'",
}
manual_improvements["air_act:s7"] = {
    "question": "What consent is required under Section 7 of the Air (Prevention and Control of Pollution) Act, 1981 for operating industrial plants?",
    "answer_type": "consent",
    "reason": "Fixed from 'what is permitted regarding' to consent-focused",
}
manual_improvements["kmc:nuisance"] = {
    "question": "What does Section Ch.Nuisance of the Kolkata Municipal Corporation Act, 1980 define as a public nuisance?",
    "answer_type": "definition",
    "reason": "Fixed from vague 'state about public nuisance'",
}
manual_improvements["wbmo:penalties"] = {
    "question": "What penalties does the West Bengal Meat Order, 1966 prescribe for violation of its provisions?",
    "answer_type": "penalty",
    "reason": "Fixed from vague 'state about Penalties'",
}

# Apply manual improvements
improved_manual = []
for item in gold_identical_items:
    qid = item["qid"]
    if qid in manual_improvements:
        manual_data = manual_improvements[qid]
        improved = {
            "qid": qid,
            "question": manual_data["question"],
            "section": item.get("section", ""),
            "act": item.get("act", ""),
            "title": item.get("title", ""),
            "domain": item.get("domain", ""),
            "source_template": item.get("template", ""),
            "benchmark_status": "improved",
            "original_rejected_question": item.get("question", ""),
            "improvement_reason": manual_data["reason"],
            "improvement_method": "manual_rewrite",
            "answer_type": manual_data["answer_type"],
            "answer_scoring": {
                "2": "Correct legal answer that directly addresses the cited provision.",
                "1": "Substantially correct but incomplete or minor wording/qualification issue.",
                "0": "Incorrect answer or unsupported legal assertion.",
                "NA": "Use only if later source verification establishes that the question itself is invalid.",
            },
        }
        improved_manual.append(improved)
    else:
        improved = {
            "qid": qid,
            "question": "What does Section {} of the {} provide regarding {}?".format(
                item.get("section", "X"), item.get("act", "the relevant Act"), item.get("title", "the subject matter"),
            ),
            "section": item.get("section", ""),
            "act": item.get("act", ""),
            "title": item.get("title", ""),
            "domain": item.get("domain", ""),
            "source_template": item.get("template", ""),
            "benchmark_status": "improved",
            "original_rejected_question": item.get("question", ""),
            "improvement_reason": "Generic rewrite: changed from rejected format to 'what does Section X provide' format",
            "improvement_method": "fallback_generic",
            "answer_type": "general",
            "answer_scoring": {
                "2": "Correct legal answer that directly addresses the cited provision.",
                "1": "Substantially correct but incomplete or minor wording/qualification issue.",
                "0": "Incorrect answer or unsupported legal assertion.",
                "NA": "Use only if later source verification establishes that the question itself is invalid.",
            },
        }
        improved_manual.append(improved)

print(f"Manually improved: {len(improved_manual)} items")

# Combine all improvements
all_improved = improved_from_gold + improved_manual

print(
    f"Total improved items: {len(all_improved)} (gold-different: {len(improved_from_gold)}, manual: {len(improved_manual)})",
)

# Build output dataset
output_data = {
    "version": "stratified-sample-v2-improved-v3",
    "summary": {
        "source": "stratified_eval_sample_v2_audit.json",
        "n_original_rejected": len(rejected),
        "n_gold_different": len(gold_different_items),
        "n_gold_identical": len(gold_identical_items),
        "n_no_gold": len(no_gold_items),
        "n_improved_from_gold": len(improved_from_gold),
        "n_improved_manual": len(improved_manual),
        "n_total_improved": len(all_improved),
        "n_existing_cleaned": len(cleaned["questions"]),
        "n_total_after": len(cleaned["questions"]) + len(all_improved),
        "recommendation": "Improved items are added to the cleaned benchmark with 'improved' status. Items with identical gold standard questions were manually rewritten to address structural issues.",
        "scoring": {
            "2": "Correct legal answer that directly addresses the cited provision.",
            "1": "Substantially correct but incomplete or minor wording/qualification issue.",
            "0": "Incorrect answer or unsupported legal assertion.",
            "NA": "Invalid benchmark question.",
        },
    },
    "questions": all_improved,
}

# Save improved items
improved_path = Path("benchmark/stratified_eval_sample_v2_improved_v3.json")
with open(improved_path, "w", encoding="utf-8") as f:
    json.dump(output_data, f, indent=2)

print()
print(f"SAVED: Improved items written to {improved_path}")
print(f"Count: {len(all_improved)} improved questions")

# Also create a combined benchmark file (original cleaned + improved)
combined_questions = cleaned["questions"] + all_improved
combined_data = {
    "version": "stratified-sample-v2-combined-v3",
    "summary": {
        "source": "stratified_eval_sample_v2_cleaned.json + improved rejected items",
        "n_original_cleaned": len(cleaned["questions"]),
        "n_improved_added": len(all_improved),
        "n_total": len(combined_questions),
        "recommendation": "Combined benchmark includes original cleaned items plus improved versions of previously rejected items.",
    },
    "questions": combined_questions,
}

combined_path = Path("benchmark/stratified_eval_sample_v2_combined_v3.json")
with open(combined_path, "w", encoding="utf-8") as f:
    json.dump(combined_data, f, indent=2)

print()
print(f"SAVED: Combined benchmark written to {combined_path}")
print(
    "Count: {} total questions ({} original + {} improved)".format(
        len(combined_questions), len(cleaned["questions"]), len(all_improved),
    ),
)

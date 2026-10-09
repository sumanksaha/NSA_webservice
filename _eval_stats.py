import json
from collections import Counter

with open("benchmark/gold_provisions_templates_v1.json") as f:
    templates = json.load(f)
with open("benchmark/stratified_eval_sample_v1.json") as f:
    sample = json.load(f)

qs = templates["questions"]
sample_qs = sample["questions"]

print("=== gold_provisions_templates_v1.json ===")
print(f"version: {templates['version']}")
print(f"generated: {templates['generated']}")
print(f"template_names: {templates['template_names']}")
print(f"total questions: {len(qs)}")

t = Counter()
for q in qs:
    t.update([q.get("template")])
print("\nTemplate distribution (str field):")
for k, c in t.most_common():
    print(f"  {k}: {c}")

print(f"\nHas 'expected_answer_type' field: {sum(1 for q in qs if 'expected_answer_type' in q)}")
print(f"Has 'template_used' field: {sum(1 for q in qs if 'template_used' in q)}")

d = Counter(q["domain"] for q in qs)
print("\nDomain distribution:")
for dmn, c in d.most_common():
    print(f"  {dmn}: {c}")

acts = Counter(q["act"] for q in qs)
print(f"\nActs (unique): {len(set(q['act'] for q in qs))}")
for a, c in acts.most_common():
    print(f"  {a}: {c}")

print(f"\nUnique sections: {len(set(q['section'] for q in qs))}")
print(f"Sections with 'N/A': {[q['qid'] for q in qs if q['section'] == 'N/A']}")
print(f"Titles missing: {sum(1 for q in qs if not q.get('title'))}")

# Check for duplicate qids
qids = [q["qid"] for q in qs]
print(f"\nDuplicate qids: {Counter(x for x in qids if qids.count(x) > 1)}")

print("\n=== stratified_eval_sample_v1.json ===")
print(f"version: {sample['version']}")
print(f"n_selected: {sample['n_selected']}")
print(f"total questions: {len(sample_qs)}")

st = Counter(q.get("template") for q in sample_qs)
print("\nTemplate distribution in sample:")
for k, c in st.most_common():
    print(f"  {k}: {c}")

sample_qids = {q["qid"] for q in sample_qs}
template_qids = {q["qid"] for q in qs}
print(f"\nAll 80 sample qids present in templates: {sample_qids.issubset(template_qids)}")
print(f"Intersection: {len(sample_qids & template_qids)}")

q1 = [q for q in sample_qs if q["question"].startswith("Under Section")]
q2 = [q for q in sample_qs if q["question"].startswith("What is the maximum")]
q3 = [q for q in sample_qs if q["question"].startswith("According to Section")]
q4 = [q for q in sample_qs if q["question"].startswith("What does Section")]
print(
    f"\nQuestion-type counts: make_question_1={len(q1)}, make_question_2={len(q2)}, make_question_3={len(q3)}, make_question_4={len(q4)}",
)

mismatches = []
for q in sample_qs:
    tag = q.get("template")
    qt = q["question"]
    ok = (
        ((tag == "make_question_1") == qt.startswith("Under Section"))
        or ((tag == "make_question_2") == qt.startswith("What is the maximum"))
        or ((tag == "make_question_3") == qt.startswith("According to Section"))
        or ((tag == "make_question_4") == qt.startswith("What does Section"))
    )
    if not ok:
        mismatches.append(q["qid"])
print(f"Template-tag vs question-shape mismatches: {mismatches}")

# Example of each template type
print("\n=== Example questions (one per template type) ===")
for qt in [q1[0], q2[0], q3[0], q4[0]]:
    print(f"[{qt['template']}] {qt['question']}")
    print(f"      qid={qt['qid']} section={qt['section']} act={qt['act'][:40]} domain={qt['domain']}")
    print()

import json
import sys

sys.path.insert(0, r"C:/github/NSA_webservice")
from evaluation.answer_error_taxonomy import classify_answer_failure
from evaluation.answer_scoring import score_answer
from evaluation.benchmark import load_questions

# load gate lists
with open(r"evaluation/out/ceiling_v5/experiment_F_gate_lists.json", encoding="utf-8") as f:
    gates = json.load(f)
f1_gate = set(gates["f1_abstention_gate"])

# insufficient evidence qids
questions = {q.raw["question_id"]: q for q in load_questions()}
ie_qids = {qid for qid, q in questions.items() if q.insufficient_evidence}

# machine-incorrect F1 qids
machine_f1 = f1_gate - ie_qids
sys.stdout.write(f"machine f1 count: {len(machine_f1)}\n")

# load per-question evaluator v2 records
records = {}
jsonl_path = r"evaluation/out/ceiling_v5/evaluator_v2_per_question.jsonl"
with open(jsonl_path, encoding="utf-8") as f:
    for line in f:
        rec = json.loads(line)
        records[rec["qid"]] = rec

# process each
results = []
for qid in sorted(machine_f1):
    rec = records.get(qid)
    if not rec:
        sys.stdout.write(f"{qid} missing record\n")
        continue
    answer = rec.get("answer") or ""
    expected = rec.get("expected") or ""
    inc = rec.get("insufficient_evidence", False)
    # score
    sc = score_answer(answer, expected, inc)
    # classify
    cats = classify_answer_failure(rec)
    results.append({
        "qid": qid,
        "soft": sc["answer_correctness"],
        "binary_correct": sc["binary_correct"],
        "abstention_credited": sc["binary_correct_abstention_credit"],
        "categories": cats,
    })
    sys.stdout.write(
        f"{qid} soft {sc['answer_correctness']} binary {sc['binary_correct']} "
        f"abstention {sc['binary_correct_abstention_credit']} {cats}\n"
    )

# summary
from collections import Counter

cats_counts = Counter()
for r in results:
    cats_counts.update(r["categories"])
sys.stdout.write(f"\nCategory counts: {dict(cats_counts)}\n")

sys.stdout.write("\nsoft distribution:\n")
for r in results:
    sys.stdout.write(f"{r['qid']}: soft={r['soft']}, binary={r['binary_correct']}, abstain={r['abstention_credited']}\n")

# count how many have binary_correct == 1
correct_count = sum(1 for r in results if r["binary_correct"] == 1)
sys.stdout.write(f"\nbinary_correct == 1 among the 29: {correct_count}/29\n")

import json, sys
sys.path.insert(0, r"C:/github/NSA_webservice")
from evaluation.answer_scoring import score_answer
from evaluation.answer_error_taxonomy import classify_answer_failure
from evaluation.experiment_f_calibration_eval import load_gate_lists
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
print("machine f1 count:", len(machine_f1))

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
        print(qid, "missing record")
        continue
    answer = rec.get("answer") or ""
    expected = rec.get("expected") or ""
    inc = rec.get("insufficient_evidence", False)
    # score
    sc = score_answer(answer, expected, inc)
    # classify
    cats = classify_answer_failure(rec)
    results.append(
        {
            "qid": qid,
            "soft": sc["answer_correctness"],
            "binary_correct": sc["binary_correct"],
            "abstention_credited": sc["binary_correct_abstention_credit"],
            "categories": cats,
        }
    )
    print(
        qid,
        "soft",
        sc["answer_correctness"],
        "binary",
        sc["binary_correct"],
        "abstention",
        sc["binary_correct_abstention_credit"],
        cats
    )

# summary
from collections import Counter
cats_counts = Counter()
for r in results:
    cats_counts.update(r["categories"])
print("\nCategory counts:", dict(cats_counts))

print("\nsoft distribution:")
for r in results:
    print(f"{r['qid']}: soft={r['soft']}, binary={r['binary_correct']}, abstain={r['abstention_credited']}")

# count how many have binary_correct == 1
correct_count = sum(1 for r in results if r["binary_correct"] == 1)
print(f"\nbinary_correct == 1 among the 29: {correct_count}/29")
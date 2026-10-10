import json

# Load the tabulation entries for the 14 remaining qids
t = json.load(open("evaluation/out/ceiling_v5/full_review_tabulation.json"))
misses = [r for r in t["records"] if r.get("verdict") == "evaluator_miss"]
no_arm = [
    r for r in misses if not any((r.get("machine") or {}).get(a, {}).get("v2") for a in ["C-O3", "D2", "D3", "E1"])
]
no_arm_qids = set(r["qid"] for r in no_arm)

remaining = [
    "Q012",
    "Q028",
    "Q068",
    "Q071",
    "Q083",
    "Q090",
    "Q119",
    "Q128",
    "Q132",
    "Q133",
    "Q134",
    "Q135",
    "Q137",
    "Q146",
]

for q in remaining:
    r = next(r for r in misses if r["qid"] == q)
    machine = r.get("machine", {})
    print(q + ":")
    print("  machine v2 correct: " + str(any(machine.get(a, {}).get("v2") for a in ["C-O3", "D2", "D3", "E1"])))
    print(
        "  machine v2_soft: "
        + str(
            machine.get("C-O3", {}).get("v2_soft", "")
            or machine.get("D2", {}).get("v2_soft", "")
            or machine.get("D3", {}).get("v2_soft", "")
            or machine.get("E1", {}).get("v2_soft", "")
        )
    )
    print("  verdict: " + r.get("verdict"))
    print("  answer_jaccard: " + str(r.get("metrics", {}).get("answer_jaccard", "N/A")))
    print("  answer_correctness: " + str(r.get("metrics", {}).get("answer_correctness", "N/A")))
    print()

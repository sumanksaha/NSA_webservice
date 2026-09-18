"""
Step 1: Stratify 100 questions from the 150-question benchmark based on CE v2 gold rank.
Step 2: Handle deficits by proportional redistribution.
Step 3: Verify 400-call budget (100 questions x 4 K values).
Step 4: Save experiment_B_question_ids.json.
"""
import json
import os
import math

# ─── Paths ───────────────────────────────────────────────────────────────────
REPO = r"C:\github\NSA_webservice"
EXP_A_JSON = os.path.join(REPO, "evaluation", "out", "ceiling_v5", "experiment_a_gold_rank.json")
OUT_JSON = os.path.join(REPO, "evaluation", "out", "ceiling_v5", "experiment_B_question_ids.json")

# ─── Load Experiment A results ───────────────────────────────────────────────
with open(EXP_A_JSON) as f:
    data = json.load(f)

questions = data["per_question"]
print(f"Loaded {len(questions)} questions from Experiment A")

# ─── Classify each question into Group A-E based on CE best gold rank ────────
def classify_group(ce_best_rank):
    """CE gold rank -> Group label."""
    if ce_best_rank is None:
        return "E"
    r = int(ce_best_rank)
    if 1 <= r <= 10:
        return "A"
    elif 11 <= r <= 20:
        return "B"
    elif 21 <= r <= 50:
        return "C"
    elif 51 <= r <= 100:
        return "D"
    else:
        return "E"

group_questions = {"A": [], "B": [], "C": [], "D": [], "E": []}
for e in questions:
    ce_rank = e["stages"]["ce"]["best_gold_rank"]
    grp = classify_group(ce_rank)
    group_questions[grp].append(e["question_id"])

# Sort each group by numeric question ID for deterministic selection
for g in "ABCDE":
    group_questions[g] = sorted(group_questions[g], key=lambda x: int(x[1:]))

print("\n=== Available counts per group ===")
target = {"A": 30, "B": 25, "C": 20, "D": 15, "E": 10}
for g in "ABCDE":
    print(f"  Group {g} (CE rank {'1-10' if g=='A' else '11-20' if g=='B' else '21-50' if g=='C' else '51-100' if g=='D' else '>100/missing'}): {len(group_questions[g])} available, target={target[g]}")

# ─── Step 1: Take as many as possible from each group ────────────────────────
selected = {}
deficits = {}
for g in "ABCDE":
    avail = len(group_questions[g])
    tgt = target[g]
    taken = min(avail, tgt)
    selected[g] = list(group_questions[g][:taken])
    deficits[g] = tgt - taken

total_deficit = sum(deficits[g] for g in "ABCDE")
print(f"\n=== Step 1: Initial selection ===")
for g in "ABCDE":
    print(f"  Group {g}: {len(selected[g])}/{target[g]} (deficit: {deficits[g]})")
print(f"  Total selected: {sum(len(selected[g]) for g in 'ABCDE')}, deficit: {total_deficit}")

# ─── Step 2: Redistribute deficit proportionally across groups with surplus ─
if total_deficit > 0:
    print(f"\n=== Step 2: Proportional redistribution of {total_deficit} deficit ===")
    
    # Groups with surplus (can take more)
    surplus_groups = {}
    for g in "ABCDE":
        avail = len(group_questions[g])
        used = len(selected[g])
        surplus = avail - used
        if surplus > 0:
            surplus_groups[g] = surplus
    
    total_surplus = sum(surplus_groups.values())
    print(f"  Groups with surplus: {surplus_groups}, total_surplus={total_surplus}")
    
    if total_surplus > 0:
        # Proportional allocation using largest remainder method
        raw_alloc = {}
        for g in surplus_groups:
            raw_alloc[g] = total_deficit * surplus_groups[g] / total_surplus
        
        # Floor allocation
        floor_alloc = {g: int(math.floor(raw_alloc[g])) for g in surplus_groups}
        remaining = total_deficit - sum(floor_alloc.values())
        
        # Distribute remaining by fractional remainder
        ordered = sorted(surplus_groups.keys(), key=lambda g: raw_alloc[g] - floor_alloc[g], reverse=True)
        for g in ordered:
            if remaining <= 0:
                break
            floor_alloc[g] += 1
            remaining -= 1
        
        # Apply redistribution
        additional = {}
        for g in "ABCDE":
            add = floor_alloc.get(g, 0)
            additional[g] = add
            if add > 0:
                # Take next available questions using stride sampling from the remainder
                remaining_questions = group_questions[g][len(selected[g]):]
                # Stride to spread across the remaining pool
                stride = max(1, len(remaining_questions) // add)
                for i in range(add):
                    idx = i * stride
                    if idx < len(remaining_questions):
                        selected[g].append(remaining_questions[idx])
                print(f"  Group {g}: +{add} redistributed (stride={stride})")
            else:
                print(f"  Group {g}: +0 redistributed")
        
        print(f"  Remaining deficit after redistribution: {remaining}")
    else:
        print("  No surplus groups available for redistribution!")

# ─── Step 3: Build final sample ──────────────────────────────────────────────
all_selected = []
for g in "ABCDE":
    all_selected.extend(selected[g])

# Sort by numeric question ID for stable ordering
sorted_sampled = sorted(all_selected, key=lambda x: int(x[1:]))
assert len(sorted_sampled) == len(set(sorted_sampled)), "Duplicate question IDs detected!"

final_dist = {g: len(selected[g]) for g in "ABCDE"}
total = len(sorted_sampled)
total_calls = total * 4

print(f"\n=== Final sampling scheme ===")
for g in "ABCDE":
    print(f"  Group {g}: {final_dist[g]} questions (target was {target[g]})")
print(f"  Total: {total} questions")
print(f"  Total LLM calls: {total} x 4 = {total_calls}")
print(f"  Within 400-call budget: {total_calls <= 400}")
print(f"  Exactly 100 questions: {total == 100}")
print(f"  Exactly 400 calls: {total_calls == 400}")

# ─── Save ────────────────────────────────────────────────────────────────────
result = {
    "experiment": "Experiment B - 400-call stratified top-K evaluation",
    "ce_model": "legal_ce_v2_K500",
    "benchmark_size": 150,
    "sampled_size": total,
    "selection_method": "deterministic (sorted QIDs, first-N per group; stride sampling for redistribution)",
    "stratification_basis": "Experiment A CE v2 best-gold rank",
    "target_distribution": target,
    "final_distribution": final_dist,
    "deficits": {g: deficits[g] for g in "ABCDE"},
    "redistribution": {
        "total_deficit": total_deficit,
        "total_surplus": total_surplus if total_deficit > 0 else 0,
        "additional_per_group": additional if total_deficit > 0 else {},
        "method": "largest remainder proportional",
    },
    "call_budget": {
        "n_questions": total,
        "n_K_values": 4,
        "K_values": [10, 20, 50, 100],
        "total_calls": total_calls,
        "max_allowed": 400,
        "within_budget": total_calls <= 400,
        "exactly_400": total_calls == 400,
    },
    "question_ids": sorted_sampled,
}

with open(OUT_JSON, "w") as f:
    json.dump(result, f, indent=2)

print(f"\nSaved to: {OUT_JSON}")
print(f"Question IDs: {sorted_sampled}")

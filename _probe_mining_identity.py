"""Probe mining-record identity fields (section/clause_number/act_name coverage)."""
import collections
import json

recs = [json.loads(l) for l in open("evaluation/out/ceiling_v5/hard_negative_mining.jsonl", encoding="utf-8")]
secs = collections.Counter()
clauses = collections.Counter()
acts = collections.Counter()
n_pos = n_neg = 0
for r in recs:
    for p in r.get("positives", []):
        n_pos += 1
        secs["pos_section"] += 1 if p.get("section") else 0
        clauses["pos_clause"] += 1 if p.get("clause_number") else 0
        acts["pos_act"] += 1 if p.get("act_name") else 0
    for n in r.get("negatives", []):
        n_neg += 1
        secs["neg_section"] += 1 if n.get("section") else 0
        clauses["neg_clause"] += 1 if n.get("clause_number") else 0
        acts["neg_act"] += 1 if n.get("act_name") else 0
print(f"positives={n_pos} negatives={n_neg}")
print("section:", dict(secs))
print("clause_number:", dict(clauses))
print("act_name:", dict(acts))
# sample keys of a positive + negative
p0 = recs[0]["positives"][0]
n0 = recs[0]["negatives"][0]
print("pos keys:", sorted(p0.keys()))
print("neg keys:", sorted(n0.keys()))

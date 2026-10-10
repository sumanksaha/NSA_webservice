def prim_rate(r):
    if r.get("n_gold_scored", 0) > 0:
        return r["baseline_primary_first"] / r["n_gold_scored"]
    return 0.0


import json

data = json.load(open("evaluation/out/ceiling_v5/p0_1_packing_sweep.json"))
valid = [r for r in data if r["n_questions"] > 0]
print("=" * 100)
print(f"P0-1 PACKING STRUCTURAL BENCH - summary ({len(valid)} valid runs)")
print("=" * 100)
hdr = "{:>8} {:>6} {:>9} {:>9} {:>9} {:>9} {:>9} {:>9}".format(
    "arm", "mc", "goldBase", "goldPack", "prim1st_b", "prim1st_p", "changed", "esFit"
)
print(hdr)
print("-" * 100)
for r in sorted(valid, key=lambda r: (r["arm"], r["max_chunks"])):
    print(
        "{:>8} {:>6} {:>9.4f} {:>9.4f} {:>9.4f} {:>9.4f} {:>9.4f} {:>9.4f}".format(
            r["arm"],
            r["max_chunks"],
            r["baseline_gold_rate"],
            r["packed_gold_rate"],
            prim_rate(r),
            r["packed_primary_first_rate"],
            r["prompt_changed_rate"],
            r["evidence_set_fit_rate"],
        )
    )
print()
print("CONCLUSIONS:")
for r in sorted(valid, key=lambda r: (r["arm"], r["max_chunks"])):
    if r["arm"] == "D_kg":
        continue
    gold_ok = r["packed_gold_rate"] >= r["baseline_gold_rate"]
    gold_d = r["packed_gold_rate"] - r["baseline_gold_rate"]
    lead = r["packed_primary_first_rate"] - prim_rate(r)
    fit_ok = r["evidence_set_fit_rate"] > 0.9
    status = "OK" if (gold_ok and lead > 0.2 and fit_ok) else ""
    print(
        "  %s mc=%2d: goldNotDropped=%s (d=%.4f)  leadDelta=%.4f  esFit=%.4f %s"
        % (r["arm"], r["max_chunks"], gold_ok, gold_d, lead, r["evidence_set_fit_rate"], status)
    )

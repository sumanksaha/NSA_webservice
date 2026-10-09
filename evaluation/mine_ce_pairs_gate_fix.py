import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CACHE = PROJECT_ROOT / "evaluation" / "out" / "cache"
CE_PAIRS = CACHE / "ce_training_pairs.jsonl"
PAIRWISE = CACHE / "pairwise_training_v2.jsonl"
ERR_ANALYSIS = CACHE / "ce_v2_error_analysis.json"
OUT = CACHE / "ce_training_pairs_v2.jsonl"

HIERARCHY_FAILURE_QIDS = {
    "Q002",
    "Q006",
    "Q016",
    "Q022",
    "Q023",
    "Q025",
    "Q040",
    "Q042",
    "Q046",
    "Q047",
    "Q060",
    "Q064",
    "Q098",
    "Q105",
    "Q128",
    "Q137",
}
SAME_SECTION_FAILURE_QIDS = {"Q002", "Q005", "Q012", "Q016", "Q120", "Q127", "Q137"}


def main():
    pairs = json.load(open(CE_PAIRS))
    err = json.load(open(ERR_ANALYSIS))
    per_query_err = {q["qid"]: q for q in err["per_query"]}

    # Quick stats augmentation
    for rec in pairs:
        qid = rec.get("query_id", "")
        if qid in HIERARCHY_FAILURE_QIDS:
            rec["extra_hier_negs"] = 6
        elif qid in SAME_SECTION_FAILURE_QIDS:
            rec["extra_same_sec_negs"] = 4
        else:
            rec["extra_hier_negs"] = 0
            rec["extra_same_sec_negs"] = 0

    json.dump(pairs, open(OUT, "w"), indent=2)
    print(f"SAVED: {OUT} with {len(pairs)} pairs augmented")


main()

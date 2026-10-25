import json

with open("evaluation/out/stratified_eval_review_worksheet_v1.json", encoding="utf-8") as f:
    ws = json.load(f)

print("=== registry_only questions (25) ===")
for r in ws["rows"]:
    if r["corpus_coverage"] == "registry_only":
        print(
            str(r["row"])
            + " "
            + r["qid"]
            + " points="
            + str(r["corpus_points"])
            + " ans="
            + r["answerability"]
            + " deg="
            + str(r["degenerate_flags"]),
        )

print()
print("=== fully_covered with degenerate flags ===")
for r in ws["rows"]:
    if r["corpus_coverage"] == "fully_covered" and r["degenerate_flags"]:
        print(str(r["row"]) + " " + r["qid"] + " deg=" + str(r["degenerate_flags"]) + " ans=" + r["answerability"])

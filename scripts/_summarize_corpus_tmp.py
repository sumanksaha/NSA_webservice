"""Summarize the regenerated corpus_eval_result.json."""
import json

r = json.load(open("corpus_eval_result.json", encoding="utf-8"))
s = r["summary"]
print("docs ok:", s["documents_evaluated"], "| failed:", s["documents_failed"])
print("type spread:", s["document_type_spread"])
print("chunks:", s["total_chunks"])
for d in r["documents"]:
    if d["status"] == "ok":
        print(
            f"  {d['file'][:58]:58s} -> {str(d['classification'].get('document_type')):20s} "
            f"conf={d['classification'].get('document_type_conf')}"
        )

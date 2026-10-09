import json
from pathlib import Path

text = Path("benchmark/benchmark_workflow_v1.0.jsonl").read_text(encoding="utf-8")
# entries span multiple lines; split on blank-line + --- separators, parse blocks starting with {
print("total lines:", len(text.splitlines()))
blocks = [b.strip() for b in text.replace("\r", "").split("---")]
print("blocks:", len(blocks))
for b in blocks:
    s = b.strip()
    if s.startswith("{"):
        try:
            o = json.loads(s)
            print("OK:", repr(o.get("question", ""))[:100])
        except Exception as e:
            print("FAIL:", str(e)[:150], repr(s[:150]))

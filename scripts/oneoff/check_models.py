"""Find model slugs matching substrings (0 LLM budget). Usage: check_models.py haiku claude"""
import sys

import httpx

wanted = [s.lower() for s in sys.argv[1:]] or ["haiku"]
r = httpx.get("https://openrouter.ai/api/v1/models", timeout=30, verify=False)
r.raise_for_status()
models = r.json().get("data", [])
print(f"total: {len(models)}")
for w in wanted:
    hits = [m for m in models
            if w in str(m.get("id", "")).lower() or w in str(m.get("name", "")).lower()]
    print(f"--- {w!r}: {len(hits)} ---")
    for m in hits[:15]:
        print(f"  {m.get('id')}")

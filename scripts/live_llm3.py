"""Call 2 only: appeal Q WITH workflow context (post-ingest state)."""

import json
import os
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path("C:/github/NSA_webservice")
os.chdir(ROOT)
from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL = os.getenv("RAG_LLM_MODEL", "poolside/laguna-s-2.1:free")
FRE = re.compile(r"\bform\s+(ii|iii|iv|v|vi|vii|viia|viii)\b", re.IGNORECASE)
WF = open(ROOT / "sample_workflow.md", encoding="utf-8").read().split("---")[0][:1500]
body = json.dumps({
    "model": MODEL,
    "messages": [
        {
            "role": "system",
            "content": "You are a food-safety legal assistant. Answer with exact Form numbers and regulation clauses. Cite [n]. If unsure, say so.",
        },
        {
            "role": "user",
            "content": f"Question: How can the FBO appeal to the designated officer?\n\nContext:\n{WF}\n\nAnswer:",
        },
    ],
    "temperature": 0.1,
    "max_tokens": 300,
}).encode()
req = urllib.request.Request(
    "https://openrouter.ai/api/v1/chat/completions",
    data=body,
    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
)
t = time.time()
try:
    d = json.load(urllib.request.urlopen(req, timeout=60))
    msg = d["choices"][0].get("message", {})
    text = msg.get("content") or msg.get("reasoning") or ""
    print(f"CALL 2 ({time.time() - t:.1f}s)")
    print(text)
    print("usage:", d.get("usage", {}))
    print("forms:", sorted({m.group(1).lower() for m in FRE.finditer(text)}))
except Exception as e:
    print(f"FAIL {type(e).__name__}: {str(e)[:200]}")

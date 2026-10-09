"""Live LLM probe via raw OpenRouter REST (no app import = no 30s boot hang).

Budget: 2 calls max (appeal Q with no-context, appeal Q with workflow context).
Uses: OPENROUTER_API_KEY + RAG_LLM_MODEL from .env. Prints usage both calls.
"""

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
QUERY = "How can the FBO appeal to the designated officer?"
GOLD = "The FBO can appeal to the designated officer in **Form VIII**, Regulation 2.4.6."
FRE = re.compile(r"\bform\s+(ii|iii|iv|v|vi|vii|viia|viii)\b", re.IGNORECASE)


def call(system, user, max_tokens=300):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
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
        return text, d.get("usage", {}), time.time() - t, None
    except Exception as e:
        return "", {}, time.time() - t, f"{type(e).__name__}: {str(e)[:200]}"


SYS = "You are a food-safety legal assistant. Answer with exact Form numbers and regulation clauses. Cite [n]. If unsure, say so."
print(f"model={MODEL} key_len={len(KEY)}")
print(f"GOLD: {GOLD}\n")

# CALL 1: no context (today's RAG state — workflow absent from Qdrant)
t1, u1, dt1, e1 = call(SYS, f"Question: {QUERY}\n\nContext:\n(no retrieved context)\n\nAnswer:")
print(f"--- CALL 1 (no context, {dt1:.1f}s, err={e1}) ---")
print(t1 or "(empty)")
print("usage:", u1, "forms:", sorted({m.group(1).lower() for m in FRE.finditer(t1)}))

# CALL 2: with workflow context (post-ingest state — proves trainability)
WF = open(ROOT / "sample_workflow.md", encoding="utf-8").read().split("---")[0][:1500]
t2, u2, dt2, e2 = call(SYS, f"Question: {QUERY}\n\nContext:\n{WF}\n\nAnswer:")
print(f"\n--- CALL 2 (workflow context, {dt2:.1f}s, err={e2}) ---")
print(t2 or "(empty)")
print("usage:", u2, "forms:", sorted({m.group(1).lower() for m in FRE.finditer(t2)}))
tot = (u1.get("total_tokens", 0) or 0) + (u2.get("total_tokens", 0) or 0)
print(f"\nBUDGET: 2 calls, {tot} tokens total")

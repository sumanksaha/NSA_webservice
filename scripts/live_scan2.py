"""Live Qdrant scan via chunk_text scroll (REST, small pages, progress)."""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path("C:/github/NSA_webservice")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

U = os.getenv("RAG_QDRANT_URL").rstrip("/")
H = {"api-key": os.getenv("RAG_QDRANT_API_KEY"), "Content-Type": "application/json"}
COL = os.getenv("RAG_QDRANT_COLLECTION", "fssai_legal_768")
FRE = re.compile(r"form\s+(ii|iii|iv|v|vi|vii|viia|viii)", re.IGNORECASE)

off, total, f8, wf, form_any = None, 0, 0, 0, 0
n_pages = 0
while True:
    payload = {"limit": 100, "with_payload": ["chunk_text", "document_type", "document_title"], "with_vector": False}
    if off is not None:
        payload["offset"] = off
    req = urllib.request.Request(U + f"/collections/{COL}/points/scroll", data=json.dumps(payload).encode(), headers=H)
    res = json.load(urllib.request.urlopen(req, timeout=25))["result"]
    pts = res.get("points", [])
    if not pts:
        break
    for p in pts:
        pl = p.get("payload", {}) or {}
        t = (pl.get("chunk_text", "") or "") + " " + (pl.get("document_title", "") or "")
        total += 1
        if "workflow" in t.lower():
            wf += 1
        if "form viii" in t.lower():
            f8 += 1
            if f8 <= 3:
                print("FORMVIII:", t[:200].replace("\n", " "))
        if FRE.search(t):
            form_any += 1
    n_pages += 1
    print(f"  ... page {n_pages}: {total} scanned, workflow={wf} formVIII={f8} anyForm={form_any}", flush=True)
    off = res.get("next_page_offset")
    if off is None or total >= 3000:
        break
print(f"DONE total={total} workflow={wf} formVIII={f8} anyForm={form_any}")

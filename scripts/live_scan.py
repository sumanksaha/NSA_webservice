"""Live Qdrant scan: full-collection Form/Workflow presence (REST, paged)."""

import json
import os
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


def scroll(off):
    body = json.dumps(
        {"limit": 500, "with_payload": True, "with_vector": False, "offset": off}
        if off
        else {"limit": 500, "with_payload": True, "with_vector": False},
    ).encode()
    req = urllib.request.Request(U + f"/collections/{COL}/points/scroll", data=body, headers=H)
    return json.load(urllib.request.urlopen(req, timeout=30))["result"]


off, total, f8, wf, form_any = None, 0, 0, 0, 0
import re

FRE = re.compile(r"form\s+(ii|iii|iv|v|vi|vii|viia|viii)", re.IGNORECASE)
while True:
    res = scroll(off)
    pts = res.get("points", [])
    if not pts:
        break
    for p in pts:
        t = json.dumps(p.get("payload", {}))
        tl = t.lower()
        total += 1
        if "workflow" in tl:
            wf += 1
        if "form viii" in tl:
            f8 += 1
        if FRE.search(t):
            form_any += 1
    print(f"  ... {total} scanned, workflow={wf} formVIII={f8} anyForm={form_any}", flush=True)
    off = res.get("next_page_offset")
    if off is None:
        break
print(f"DONE total={total} workflow={wf} formVIII={f8} anyForm={form_any}")

"""Count points matching text (server-side MatchText) for form/workflow terms."""

import json
import os
import time
import urllib.request
from pathlib import Path

ROOT = Path("C:/github/NSA_webservice")
os.chdir(ROOT)
from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

U = os.getenv("RAG_QDRANT_URL").rstrip("/")
H = {"api-key": os.getenv("RAG_QDRANT_API_KEY"), "Content-Type": "application/json"}
COL = os.getenv("RAG_QDRANT_COLLECTION", "fssai_legal_768")

for term in ["Form VIII", "Form V", "Form II", "workflow", "designated officer", "2.4.6"]:
    body = json.dumps({"filter": {"must": [{"key": "chunk_text", "match": {"text": term}}]}, "exact": True}).encode()
    t = time.time()
    try:
        req = urllib.request.Request(U + f"/collections/{COL}/points/count", data=body, headers=H)
        d = json.load(urllib.request.urlopen(req, timeout=25))
        print(f"{term!r}: count={d['result']['count']} ({time.time() - t:.1f}s)")
    except Exception as e:
        print(f"{term!r}: FAIL {type(e).__name__}: {str(e)[:150]}")

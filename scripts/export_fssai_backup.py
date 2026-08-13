"""STEP-1 (plan §3.1): export the current (stale) fssai_legal_768 with vectors.

Read-only against Qdrant; writes reports/fssai_legal_768_pre_reingest_backup.json.
Required by scripts/reingest_fssai_from_db.py --delete-collection (backup guard).
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from qdrant_client import QdrantClient  # noqa: E402


def safe_vector(v):
    """JSON-safe copy of a Qdrant vector (dense list / SparseVector / named dict)."""
    if hasattr(v, "indices") and hasattr(v, "values"):  # SparseVector (duck-typed)
        return {"_sparse": {"indices": list(v.indices), "values": list(v.values)}}
    if isinstance(v, dict):
        return {k: safe_vector(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [safe_vector(x) for x in v]
    return v


c = QdrantClient(
    url=os.environ["RAG_QDRANT_URL"],
    api_key=os.environ.get("RAG_QDRANT_API_KEY") or None,
)
out: list[dict] = []
offset = None
while True:
    page, offset = c.scroll(
        collection_name="fssai_legal_768",
        limit=1000,
        with_payload=True,
        with_vectors=True,
        offset=offset,
    )
    out.extend(
        [{"id": str(r.id), "payload": r.payload, "vector": safe_vector(r.vector)} for r in page]
    )
    if not offset:
        break

Path("reports").mkdir(exist_ok=True)
path = Path("reports") / "fssai_legal_768_pre_reingest_backup.json"
with open(path, "w") as f:
    json.dump(out, f)
print(f"BACKUP {path}: {len(out)} points (with vectors)")

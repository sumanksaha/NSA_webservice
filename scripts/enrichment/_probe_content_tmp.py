"""Temp probe: dump text for candidate sections across docs."""
from __future__ import annotations

import json
import sqlite3

DB = "instance/app.db"
FSS_ACT = "60939e3b253847b9990a93fc10f5d723"
NUTRACEUTICALS = "8b2f12f1fa5f40a9b4ae80eebda35e63"

c = sqlite3.connect(DB)


def load_doc(did: str) -> list[dict]:
    rows = c.execute(
        "SELECT data FROM chunk_enrichment WHERE json_extract(data, '$._document.document_id') = ?",
        (did,),
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


def dump(did: str, section: str, max_chunks: int = 4) -> None:
    rs = [
        r for r in load_doc(did)
        if (r.get("legal_location") or {}).get("section", {}).get("value") == section
    ]
    rs.sort(key=lambda r: r.get("chunk_index") or 0)
    print(f"\n===== {did[:8]} sec {section} ({len(rs)} chunks) =====")
    for r in rs[:max_chunks]:
        t = (r.get("original_text") or "").strip().replace("\n", " ")
        print(f"  [{r['chunk_id'][:8]}] {t[:200]}")


for s in ["6", "7", "13", "25", "28", "29", "47", "48", "49", "52", "54", "56"]:
    dump(FSS_ACT, s)
dump(NUTRACEUTICALS, "22", 3)

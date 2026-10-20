"""Collection sizes, live, and what the cached mirror actually contains.

``verify_remediation_live`` printed, before its scroll was reset by the remote
host::

    live points_count : 12842
    mirror entries    : 34745

So the mirror is ~2.7x the FSSAI collection. Either it is a multi-collection
snapshot, or the live collection has been pruned since it was built. The
difference decides whether the 230 "absent" candidates were a mistake or
belong to other collections that still need remediating.

The scroll loop also matters: several earlier probes reported counts *above*
their own cap (e.g. 40062 against a 40000 limit), which is the signature of a
loop that restarts when ``offset`` returns ``None`` instead of stopping. This
asserts the page-termination contract so the numbers can be trusted.

Live Qdrant, no LLM. Usage::

    python -m evaluation.collection_census
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"
COLLECTIONS = (
    "fssai_legal_768", "env_legal_768", "wb_state_legal_768",
    "criminal_legal_768", "commercial_legal_768", "animal_legal_768",
)


def scroll_all(client, collection: str, page: int = 256):
    """Yield every point, stopping when the server stops sending a next offset.

    The naive ``while True`` restart is the bug this guards against: Qdrant
    returns ``offset=None`` on the final page, and looping on that re-reads the
    collection from the start.
    """
    offset = None
    seen_pages = 0
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=page, offset=offset,
            with_payload=True, with_vectors=False,
        )
        if not batch:
            return
        seen_pages += 1
        yield from batch
        if offset is None:
            return
        if seen_pages > 2000:
            return


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from qdrant_client import QdrantClient

    from evaluation.bench_p0_1_packing import _load_payload_index

    client = QdrantClient(
        url=os.environ.get("RAG_QDRANT_URL"),
        api_key=os.environ.get("RAG_QDRANT_API_KEY") or None,
    )

    print(f"{'collection':<26} {'points_count':>13} {'scrolled':>9} {'stamped':>8} {'stamped+amend':>14}")
    print("-" * 74)
    live_ids: set[str] = set()
    totals = {}
    for coll in COLLECTIONS:
        info = client.get_collection(coll)
        declared = getattr(info, "points_count", None)
        n = 0
        stamped = 0
        for pt in scroll_all(client, coll):
            n += 1
            live_ids.add(str(pt.id))
            if (pt.payload or {}).get("section_number"):
                stamped += 1
        totals[coll] = {"declared": declared, "scrolled": n, "stamped": stamped}
        print(f"{coll:<26} {declared:>13} {n:>9} {stamped:>8}")

    mirror = _load_payload_index()
    mirror_ids = set(mirror)
    print()
    print(f"mirror entries        : {len(mirror)}")
    print(f"live points (all 6)   : {len(live_ids)}")
    print(f"in mirror only        : {len(mirror_ids - live_ids)}")
    print(f"in live only          : {len(live_ids - mirror_ids)}")

    (OUT / "collection_census.json").write_text(json.dumps({
        "collections": totals,
        "mirror_entries": len(mirror),
        "live_points_all": len(live_ids),
        "mirror_only": len(mirror_ids - live_ids),
        "live_only": len(live_ids - mirror_ids),
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'collection_census.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

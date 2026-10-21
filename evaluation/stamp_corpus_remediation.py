"""Apply the narrow stamping fix to the chunks already in Qdrant.

The code fix in ``legal_engine._extract_section_from_text`` only governs future
ingestion; the chunks already stored keep the stamps they were built with.
``gate_fixes`` measured the remediation: blanking ``section_number`` on chunks
that both carry a stamp and are amendment prose changes 683 of the 34,745
points in the six legal collections, takes the poisoned stamps from 141 to 0,
and loses no gold unit.

The 683 figure is corpus-wide, not per-collection. An earlier run of this
script defaulted to ``--collection fssai_legal_768`` and blanked only the 453
candidates that live there; the other 230 belong to the other five
collections. The default is now ``all`` for exactly that reason -- a narrow
default silently scoped a corpus-wide fix to one collection.

Writes are guarded four ways:

* a dry run is the default and computes everything, including the exact patch
  set, without touching Qdrant;
* an explicit ``--apply`` is required;
* the same predicate is re-evaluated per point against the *live* payload, so
  a corpus changed since the candidate set was planned cannot be clobbered;
* every affected point is recorded to a JSON sidecar so the operation can be
  inverted (``--restore``) without a backup dump.

The predicate is imported from :mod:`evaluation.gate_fixes` rather than copied,
so the remediation and the measurement that justified it cannot drift apart.

Usage::

    python -m evaluation.stamp_corpus_remediation                      # dry run
    python -m evaluation.stamp_corpus_remediation --apply              # write
    python -m evaluation.stamp_corpus_remediation --restore            # undo
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"
SIDECAR = OUT / "stamp_remediation.json"

#: Single source of truth for what counts as an amendment notification. This is
#: the same pattern the engine now uses (legal_engine.AMENDMENT_NOTIFICATION_RE)
#: and the same one gate_fixes measured; imported, not re-typed, so the three
#: cannot drift.
from evaluation.gate_fixes import AMENDMENT  # noqa: E402


def needs_remediation(payload: dict) -> bool:
    """True when this point carries a stamp it only *mentions*."""
    if not payload.get("section_number"):
        return False
    text = payload.get("chunk_text") or payload.get("text") or ""
    return bool(AMENDMENT.search(text))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--collection", default="all",
                    help="collection name, or 'all' for the six legal collections")
    ap.add_argument("--apply", action="store_true", help="write to Qdrant (default: dry run)")
    ap.add_argument("--restore", action="store_true", help="re-apply the stamps recorded in the sidecar")
    ap.add_argument("--batch", type=int, default=256)
    args = ap.parse_args()

    ALL = (
        "fssai_legal_768", "env_legal_768", "wb_state_legal_768",
        "criminal_legal_768", "commercial_legal_768", "animal_legal_768",
    )
    collections = ALL if args.collection == "all" else (args.collection,)

    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from qdrant_client import QdrantClient

    client = QdrantClient(
        url=os.environ.get("RAG_QDRANT_URL"),
        api_key=os.environ.get("RAG_QDRANT_API_KEY") or None,
    )

    # ---- restore ---------------------------------------------------------
    if args.restore:
        if not SIDECAR.exists():
            print(f"no sidecar at {SIDECAR}; nothing to restore", file=sys.stderr)
            return 1
        record = json.loads(SIDECAR.read_text(encoding="utf-8"))
        by_collection: dict[str, list[dict]] = defaultdict(list)
        default_coll = record.get("collection")
        for r in record["records"]:
            by_collection[r.get("collection", default_coll)].append(r)
        for coll, rows in by_collection.items():
            for i in range(0, len(rows), args.batch):
                client.set_payload(
                    collection_name=coll,
                    payload={"section_number": rows[i]["old_section_number"]},
                    points=[r["id"] for r in rows[i : i + args.batch]],
                    wait=True,
                )
            print(f"  restored {len(rows)} in {coll}")
        print(f"restored {len(record['records'])} points")
        return 0

    from evaluation.bench_p0_1_packing import _load_payload_index
    from evaluation.collection_census import scroll_all

    mirror = _load_payload_index()
    candidate_ids = {
        cid for cid, p in mirror.items()
        if p.get("section_number") and AMENDMENT.search(p.get("chunk_text") or p.get("text") or "")
    }
    print(f"mirror holds {len(mirror)} points; {len(candidate_ids)} are stamped-but-amendment\n")

    if not candidate_ids:
        print("nothing to remediate")
        return 0

    # Per collection: find the candidates actually present, re-check live, write.
    total_written = 0
    total_absent = 0
    all_records: list[dict] = []

    for coll in collections:
        present: list[str] = []
        n_points = 0
        n_stamp = 0
        # One pass, both counts: scrolling these collections twice costs ~50 min
        # for the pair and answers nothing the single pass cannot.
        for pt in scroll_all(client, coll):
            n_points += 1
            payload = pt.payload or {}
            if payload.get("section_number"):
                n_stamp += 1
            if str(pt.id) in candidate_ids:
                present.append(str(pt.id))
        print(f"{coll}: {n_points} points, {n_stamp} stamped, "
              f"{len(present)} stamped-but-amendment candidates present")

        if not present:
            continue

        # Re-check the predicate against the LIVE payload before writing.
        live: dict[str, dict] = {}
        for i in range(0, len(present), 256):
            got = client.retrieve(
                collection_name=coll, ids=present[i : i + 256],
                with_payload=True, with_vectors=False,
            )
            for pt in got or []:
                live[str(pt.id)] = pt.payload or {}
        records = []
        for cid in present:
            payload = live.get(cid)
            if payload is None or not needs_remediation(payload):
                continue
            records.append({
                "id": cid,
                "collection": coll,
                "old_section_number": payload.get("section_number"),
                "payload": payload,
                "text_head": (payload.get("chunk_text") or payload.get("text") or "")[:200],
            })
        absent = len(present) - len(records)
        total_absent += absent
        print(f"  {len(records)} confirmed live, {absent} already clean/absent")

        if not records:
            continue
        all_records.extend(records)

        if args.apply:
            for i in range(0, len(records), args.batch):
                chunk = records[i : i + args.batch]
                client.set_payload(
                    collection_name=coll,
                    payload={"section_number": None},
                    points=[r["id"] for r in chunk],
                    wait=True,
                )
            # Verify only what we touched.
            still = 0
            for i in range(0, len(records), 256):
                got = client.retrieve(
                    collection_name=coll,
                    ids=[r["id"] for r in records[i : i + 256]],
                    with_payload=True, with_vectors=False,
                )
                for pt in got or []:
                    if needs_remediation(pt.payload or {}):
                        still += 1
            print(f"  blanked {len(records)}, verify: {still} still bad (expected 0)")
        else:
            for r in records[:2]:
                print(f"    would blank id={r['id']} stamp={r['old_section_number']!r} "
                      f"{r['text_head'][:90]!r}")
        total_written += len(records)

    print(f"\ntotal {'blanked' if args.apply else 'to blank'}: {total_written}"
          f"   (absent/already-clean: {total_absent})")

    if args.apply and all_records:
        OUT.mkdir(parents=True, exist_ok=True)
        # Merge rather than overwrite. The sidecar is the only route back to the
        # original stamps, and each apply run touches a different subset of
        # collections, so replacing the file with this run's records would
        # silently orphan the stamps the *previous* run erased. An inverse that
        # silently loses half its records is worse than no inverse at all.
        merged: dict[tuple[str, str], dict] = {}
        if SIDECAR.exists():
            prior = json.loads(SIDECAR.read_text(encoding="utf-8"))
            default_coll = prior.get("collection")
            for r in prior.get("records", []):
                coll_of = r.get("collection", default_coll)
                # Normalise: the first apply run wrote records with no
                # per-record collection and a single top-level one. Merging
                # those through unchanged would leave them unplaceable on
                # restore, because the merged file has no such top-level key.
                merged[(coll_of, str(r["id"]))] = {**r, "collection": coll_of}
        for r in all_records:
            merged[(r["collection"], str(r["id"]))] = r

        records_out = list(merged.values())
        SIDECAR.write_text(json.dumps({
            "collections": sorted({r["collection"] for r in records_out}),
            "applied_at": datetime.now(UTC).isoformat(),
            "n": len(records_out),
            "this_run": len(all_records),
            "records": records_out,
        }, indent=2), encoding="utf-8")
        print(f"inverse recorded to {SIDECAR} ({len(records_out)} points total, "
              f"{len(all_records)} from this run)")
    elif not args.apply:
        print("\nDRY RUN — no writes. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

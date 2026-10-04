"""Corpus gap check — manifest vs. live Qdrant (read-only).

Answers "what still needs OCR'ing and ingesting?" without touching either
side. Cross-references ``other domain/manifest.json`` against the points
actually present in each per-domain Qdrant collection and reports:

- **missing**     — manifest says ``ingest`` (default) but 0 points exist
- **skipped**     — ``ingest: false`` (intentional duplicates) — listed, not flagged
- **absent file** — manifest row whose PDF is not on disk
- **orphan**      — ``document_id`` in Qdrant that the manifest never declared
- **stale notes** — a manifest note asserting a completed OCR/ingest while the
  document has 0 points (the failure mode that made
  ``wb_fire_services_act_1950`` look done when it was not)

Read-only: scrolls collections, never upserts or deletes. Safe to run any time.

Usage::

    python scripts/corpus_gap_check.py                     # human summary
    python scripts/corpus_gap_check.py --json              # machine summary
    python scripts/corpus_gap_check.py --out reports/corpus_gap.json
    python scripts/corpus_gap_check.py --domain animal     # one domain

Exit codes: 0 no gaps, 1 gaps found, 2 usage/connection error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure the project root is on sys.path so that "from app" imports work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.collections import collection_for_domain

DEFAULT_MANIFEST = Path("other domain/manifest.json")

#: Notes asserting a *completed* OCR/ingest. A document carrying one of these
#: but holding 0 points has drifted — the claim is stale. Kept narrow on
#: purpose: only unambiguous assertions, never descriptive prose.
_INGEST_CLAIM_RE = re.compile(
    r"applied at ingest"
    r"|ocr(?:ed)? (?:and )?ingested"
    r"|indexed (?:in|into) qdrant"
    r"|upserted"
    r"|ingested (?:in|into) qdrant",
    re.IGNORECASE,
)


def _env_collection_config() -> dict[str, str]:
    """Map ``RAG_QDRANT_COLLECTION_<DOMAIN>`` env vars to the config shape
    ``collection_for_domain`` expects, so env overrides work outside Flask."""
    return {k: v for k, v in os.environ.items() if k.startswith("RAG_QDRANT_COLLECTION_")}


def _qdrant_client() -> Any:
    """Build a ``QdrantClient`` from the environment (no Flask app needed)."""
    from qdrant_client import QdrantClient

    url = os.environ.get("RAG_QDRANT_URL", "")
    if not url:
        raise RuntimeError("RAG_QDRANT_URL not set — cannot reach Qdrant")
    api_key = os.environ.get("RAG_QDRANT_API_KEY", "") or None
    return QdrantClient(url=url, api_key=api_key)


def scroll_document_counts(client: Any, collection: str, batch_size: int = 1000) -> tuple[Counter, int]:
    """Scroll an entire collection once, counting points per ``document_id``.

    Returns ``(counts, total_points)``. A missing collection yields an empty
    counter rather than an error — that is itself a reportable gap.
    """
    counts: Counter[str] = Counter()
    total = 0
    next_offset: Any = None
    while True:
        kwargs: dict[str, Any] = {
            "collection_name": collection,
            "limit": batch_size,
            "with_payload": True,
            "with_vectors": False,
        }
        if next_offset is not None:
            kwargs["offset"] = next_offset
        records, next_offset = client.scroll(**kwargs)
        for rec in records or []:
            total += 1
            payload = getattr(rec, "payload", None) or {}
            did = payload.get("document_id") or payload.get("doc_id") or "<missing>"
            counts[str(did)] += 1
        if not next_offset:
            break
    return counts, total


def build_report(manifest_path: Path, domains: list[str] | None = None) -> dict[str, Any]:
    """Compare the manifest against live Qdrant state."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = manifest.get("documents") or []
    base_dir = manifest_path.parent
    env_config = _env_collection_config()

    # Restrict the rows FIRST. Filtering only the collections we scroll would
    # leave the other domains' rows unevaluated against an empty counter and
    # report every out-of-scope document as "missing".
    if domains:
        selected = set(domains)
        rows = [r for r in rows if str(r.get("domain") or "") in selected]

    # Which collections do we need to touch? Derived from the (filtered) rows.
    wanted_domains = sorted({str(r.get("domain") or "") for r in rows})

    client = _qdrant_client()

    # One scroll per collection, reused across every document in it.
    live: dict[str, Counter[str]] = {}
    collection_totals: dict[str, int] = {}
    for domain in wanted_domains:
        coll = collection_for_domain(domain, config=env_config)
        if coll in live:
            continue
        try:
            if not client.collection_exists(coll):
                live[coll] = Counter()
                collection_totals[coll] = 0
                continue
            counts, total = scroll_document_counts(client, coll)
            live[coll] = counts
            collection_totals[coll] = total
        except Exception as exc:
            raise RuntimeError(f"failed to scroll collection {coll!r}: {exc}") from exc

    findings: dict[str, list[dict[str, Any]]] = {
        "missing": [],
        "skipped": [],
        "absent_file": [],
        "orphan": [],
        "stale_notes": [],
    }
    declared_ids: set[str] = set()

    for row in rows:
        document_id = str(row.get("document_id") or "")
        declared_ids.add(document_id)
        domain = str(row.get("domain") or "")
        coll = collection_for_domain(domain, config=env_config)
        counts = live.get(coll, Counter())
        n_points = counts.get(document_id, 0)

        record: dict[str, Any] = {
            "file": row.get("file"),
            "document_id": document_id,
            "domain": domain,
            "collection": coll,
            "points": n_points,
            "requires_ocr": bool(row.get("requires_ocr")),
            "ingest": row.get("ingest", True),
        }

        # The source file is needed to (re-)ingest, so surface it alongside.
        if base_dir.is_dir() and row.get("file"):
            record["file_on_disk"] = (base_dir / str(row["file"])).is_file()

        if row.get("ingest") is False:
            record["reason"] = "ingest=false (intentional duplicate)"
            findings["skipped"].append(record)
            continue

        if not n_points:
            findings["missing"].append(record)
            # Stale-note detection is INDEPENDENT of the branches above: a note
            # asserting a completed ingest is only "stale" when the document has
            # 0 points. Checked here, not under the ``elif`` chain, so an
            # ingested document is never reported.
            if _INGEST_CLAIM_RE.search(str(row.get("notes") or "")):
                findings["stale_notes"].append(record)
        elif record.get("file_on_disk") is False:
            findings["absent_file"].append(record)

    for coll, counts in live.items():
        for did, n in counts.items():
            if did not in declared_ids:
                findings["orphan"].append({"collection": coll, "document_id": did, "points": n})

    ingestible = [r for r in rows if r.get("ingest") is not False]
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "manifest": str(manifest_path),
        "mode": "READ_ONLY",
        "collection_totals": collection_totals,
        "totals": {
            "manifest_entries": len(rows),
            "ingestible": len(ingestible),
            "ingested": len(ingestible) - len(findings["missing"]),
            "missing": len(findings["missing"]),
            "skipped": len(findings["skipped"]),
            "orphans": len(findings["orphan"]),
            "stale_notes": len(findings["stale_notes"]),
        },
        "findings": findings,
    }


def render(report: dict[str, Any]) -> str:
    """Render the human-readable summary."""
    t = report["totals"]
    lines = [
        "=" * 68,
        f"CORPUS GAP CHECK  (read-only, {report['generated_at']})",
        f"manifest: {report['manifest']}",
        "=" * 68,
        "",
        f"  entries {t['manifest_entries']}  |  ingestible {t['ingestible']}"
        f"  |  ingested {t['ingested']}  |  MISSING {t['missing']}",
        "",
        "Collection sizes:",
    ]
    for coll, n in sorted(report["collection_totals"].items()):
        lines.append(f"    {n:>8}  {coll}")

    def section(title: str, rows: list[dict[str, Any]]) -> None:
        lines.append("")
        if not rows:
            lines.append(f"{title}: none")
            return
        lines.append(f"{title}: {len(rows)}")
        for r in rows:
            ocr = " [requires_ocr]" if r.get("requires_ocr") else ""
            detail = f"{r.get('points', 0)} pts in {r.get('collection', r.get('collection', '?'))}"
            lines.append(f"    - {r.get('document_id')}{ocr}")
            lines.append(f"        file: {r.get('file')}  ({detail})")

    f = report["findings"]
    section("MISSING (0 points — needs OCR + ingest)", f["missing"])
    section("STALE NOTES (claims a completed ingest but has 0 points)", f["stale_notes"])
    section("ABSENT FILE (indexed, but source PDF is gone)", f["absent_file"])
    section("ORPHANS (in Qdrant, not in manifest)", f["orphan"])

    lines.append("")
    if f["skipped"]:
        lines.append("Skipped by design (ingest=false):")
        for r in f["skipped"]:
            lines.append(f"    - {r.get('document_id')}  ({r.get('reason')})")
    lines.append("")
    lines.append(
        "RESULT: " + ("OK — nothing missing" if t["missing"] == 0 else f"{t['missing']} document(s) need ingest")
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Report manifest documents missing from Qdrant (read-only).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"Path to the corpus manifest (default: {DEFAULT_MANIFEST}).",
    )
    parser.add_argument(
        "--domain",
        action="append",
        help="Restrict to one domain (repeatable): env | commercial | animal | wb_state | criminal.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Print the JSON report to stdout instead of the human summary.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Also write the JSON report to this path.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 (no gaps), 1 (gaps found) or 2 (error)."""
    from dotenv import load_dotenv

    load_dotenv()
    os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")

    args = build_parser().parse_args(argv)

    try:
        report = build_report(args.manifest, domains=args.domain)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.out:
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        except OSError as exc:
            print(f"warning: could not write {args.out}: {exc}", file=sys.stderr)

    print(json.dumps(report, indent=2, default=str) if args.as_json else render(report))
    return 1 if report["totals"]["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

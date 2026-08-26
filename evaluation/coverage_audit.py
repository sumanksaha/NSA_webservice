"""Corpus-wide identity coverage audit (2026-08-18).

Measures how much of the corpus carries a retrievable legal identity and
classifies the remaining gap so it can be closed:

  * **identity** — a chunk is *identified* when it carries the identity its
    document class needs for retrieval:
      - ``act`` documents  -> ``section_number``
      - regulation/rule/notification/etc. -> ``clause_number``
    (G8 semantics: dotted clause numbers are regulation identity, Act
    sections are statute identity; ``section_number`` on non-act chunks was
    proven noise and stripped — see ``scripts/strip_reg_section_noise.py``).
  * **substantive** — ``hierarchy_level >= 2`` (hl1 headers/boilerplate/OCR
    fragments semantically carry no identity; G6 established that coverage
    must be reported on substantive chunks).
  * **gap classification** — for unidentified substantive chunks, buckets by
    the *reason* identity is missing (paren fragment after a stamped header,
    rule-doc with dot-less/merged headers, space-stripped OCR, transliterated
    Hindi, doc with zero extractable headers…), per document, so each bucket
    maps to a concrete remediation path (see ``docs/COVERAGE_COMPLETENESS.md``).
  * **document_title** — backfill potential from ``document_uri`` filenames
    (G8 finding: fssai titles are empty corpus-wide).

Reads the frozen payload cache (``out/cache/payload_index.jsonl``) by
default; ``--live`` scrolls Qdrant and refreshes it.  Writes JSON to
``out/cache/coverage_audit.json``.

Usage:
    python -m evaluation.coverage_audit                # from the frozen cache
    python -m evaluation.coverage_audit --live         # scroll Qdrant, refresh cache
    python -m evaluation.coverage_audit --json out/cache/coverage_audit.json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CACHE = PROJECT_ROOT / "evaluation" / "out" / "cache" / "payload_index.jsonl"
OUT_DEFAULT = PROJECT_ROOT / "evaluation" / "out" / "cache" / "coverage_audit.json"

#: legal_domain -> collection (matches collections_from_config in the
#: backfill scripts).
DOMAIN_TO_COLLECTION = {
    "FOOD_SAFETY": "fssai_legal_768",
    "ENVIRONMENT_POLLUTION": "env_legal_768",
    "BUSINESS_CIVIL": "commercial_legal_768",
    "ANIMAL_SLAUGHTER": "animal_legal_768",
    "MUNICIPAL": "wb_state_legal_768",
    "LAND_PREMISES": "wb_state_legal_768",
    "CRIMINAL": "criminal_legal_768",
}

#: Document types whose identity is an Act section.
ACT_TYPES = ("act",)
#: Document types whose identity is a clause/rule number.
CLAUSE_TYPES = ("regulation", "rule", "notification", "circular")

_PAREN_RE = re.compile(r"^\s*\([^)]*\)")
_DOTTED_RE = re.compile(r"^\s*\d{1,3}(?:\.\d{1,2}){1,3}(?=[\s.:])")
_NUMHEAD_RE = re.compile(r"^\s*\d{1,4}(?:\.\s+|\s+)[A-Z]")
_GAZETTE_RE = re.compile(r"^(?:\d+\s+)?(THE GAZETTE|GAZETTE OF INDIA|\[PART|PART [IVXLC]+|Sec\.?\s*\d)", re.I)
_STRIPPED_RE = re.compile(r"^[A-Za-z]{8,}[a-z]{4,}(?:,[a-z]+){2,}")  # space-stripped OCR prose


def _collection_of(payload: dict) -> str:
    return DOMAIN_TO_COLLECTION.get(payload.get("legal_domain") or "", "?")


def is_identified(payload: dict) -> bool:
    dt = payload.get("document_type")
    if dt in ACT_TYPES:
        return bool(payload.get("section_number"))
    if dt in CLAUSE_TYPES:
        return bool(payload.get("clause_number"))
    # unknown/other document types: accept either field (the corpus has one
    # unknown chunk stamped with a section_number).
    return bool(payload.get("clause_number") or payload.get("section_number"))


def _gap_bucket(payload: dict) -> str:
    """Classify WHY an unidentified substantive chunk lacks identity."""
    t = (payload.get("chunk_text") or "").strip()
    dt = payload.get("document_type") or "?"
    if not t:
        return "empty_text"
    if _PAREN_RE.match(t):
        return "paren_fragment"  # "(a) ..." body fragment, no own marker
    if _DOTTED_RE.match(t):
        return "dotted_unstamped"  # dotted clause the guard/derive missed
    if dt in ("rule", "circular") and _NUMHEAD_RE.match(t) and not _GAZETTE_RE.match(t):
        return "rule_header"  # "4 Rural areas" style dot-less rule heading
    if _GAZETTE_RE.match(t):
        return "gazette_header"  # "40 THE GAZETTE OF INDIA : EXTRAORDINARY"
    if _STRIPPED_RE.match(t):
        return "stripped_ocr"  # space-stripped OCR prose (BNS)
    if re.match(r"^[A-Za-zāīūṛḷṅñṭḍḥṣś]{3,}[\s\-\.]", t) and re.search(r"[āīūṛḷṅñṭḍḥṣś]", t):
        return "transliterated"  # Romanized-Hindi (Nutraceuticals)
    if len(t) <= 2:
        return "short_noise"
    return "prose"  # plain continuation prose


def load_payloads(live: bool) -> list[dict]:
    if not live and CACHE.exists():
        out = []
        try:
            with open(CACHE, encoding="utf-8") as f:
                for line in f:
                    out.append(json.loads(line)["payload"])
        except (OSError, ValueError, KeyError) as exc:
            # corrupt/partial cache — fall through to a live fetch instead of crashing
            logging.getLogger(__name__).warning("coverage cache unreadable (%s); refetching live", exc)
        else:
            return out
    from app import create_app
    from app.rag.qdrant_client import QdrantStore
    from evaluation.resolution import build_payload_index

    app = create_app()
    with app.app_context():
        collections = list(
            dict.fromkeys([
                app.config.get("RAG_QDRANT_COLLECTION", "fssai_legal_768"),
                app.config.get("RAG_QDRANT_COLLECTION_ENV", "env_legal_768"),
                app.config.get("RAG_QDRANT_COLLECTION_COMMERCIAL", "commercial_legal_768"),
                app.config.get("RAG_QDRANT_COLLECTION_ANIMAL", "animal_legal_768"),
                app.config.get("RAG_QDRANT_COLLECTION_WB_STATE", "wb_state_legal_768"),
                app.config.get("RAG_QDRANT_COLLECTION_CRIMINAL", "criminal_legal_768"),
            ])
        )
        index = build_payload_index(
            lambda coll: QdrantStore(collection_name=coll),
            collections,
            force=True,
        )
        return list(index.values())


def audit(payloads: list[dict]) -> dict:
    total = len(payloads)
    identified = [p for p in payloads if is_identified(p)]
    substantive = [p for p in payloads if (p.get("hierarchy_level") or 1) >= 2]
    hl1 = [p for p in payloads if (p.get("hierarchy_level") or 1) < 2]
    subst_idn = [p for p in substantive if is_identified(p)]

    # per-collection + per-document-type identity coverage (substantive)
    coll_rows: dict[str, dict] = {}
    for coll in sorted({_collection_of(p) for p in payloads}):
        pls = [p for p in substantive if _collection_of(p) == coll]
        idn = sum(1 for p in pls if is_identified(p))
        coll_rows[coll] = {
            "chunks": len(pls),
            "identified": idn,
            "pct": round(idn / len(pls) * 100, 1) if pls else 0.0,
        }
    type_rows: dict[str, dict] = {}
    doc_types = {p.get("document_type") for p in payloads} - {None}
    for dt in sorted(doc_types):  # type: ignore[type-var]
        pls = [p for p in substantive if p.get("document_type") == dt]
        idn = sum(1 for p in pls if is_identified(p))
        type_rows[dt] = {
            "chunks": len(pls),
            "identified": idn,
            "pct": round(idn / len(pls) * 100, 1) if pls else 0.0,
        }

    # per-document table (worst offenders first by unidentified substantive)
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for p in payloads:
        by_doc[str(p.get("document_id") or "?")].append(p)
    doc_rows = []
    for did, pls in by_doc.items():
        sub = [p for p in pls if (p.get("hierarchy_level") or 1) >= 2]
        idn = sum(1 for p in sub if is_identified(p))
        doc_rows.append({
            "document_id": did,
            "document_type": pls[0].get("document_type"),
            "collection": _collection_of(pls[0]),
            "uri": (pls[0].get("document_uri") or "").rsplit("\\", 1)[-1].rsplit("/", 1)[-1][:60],
            "chunks": len(pls),
            "substantive": len(sub),
            "identified": idn,
            "unidentified_substantive": len(sub) - idn,
            "hl1": len(pls) - len(sub),
            "pct": round(idn / len(sub) * 100, 1) if sub else 0.0,
        })
    doc_rows.sort(key=lambda r: -r["unidentified_substantive"])

    # gap classification over unidentified substantive chunks
    gap: Counter = Counter()
    gap_by_doc: dict[str, Counter] = defaultdict(Counter)
    for p in substantive:
        if is_identified(p):
            continue
        b = _gap_bucket(p)
        gap[b] += 1
        gap_by_doc[str(p.get("document_id") or "?")][b] += 1
    gap_rows = [{"bucket": b, "chunks": n} for b, n in gap.most_common()]

    # title backfill potential
    uri_map: dict[str, str] = {}
    for p in payloads:
        uri = p.get("document_uri") or ""
        did = str(p.get("document_id") or "")
        if uri and did and did not in uri_map:
            uri_map[did] = uri
    title_missing = sum(1 for p in payloads if not p.get("document_title"))
    title_recoverable = sum(
        1 for p in payloads if not p.get("document_title") and uri_map.get(str(p.get("document_id") or ""))
    )

    return {
        "n_chunks": total,
        "identified": len(identified),
        "pct_identified": round(len(identified) / total * 100, 1) if total else 0.0,
        "substantive_chunks": len(substantive),
        "substantive_identified": len(subst_idn),
        "pct_substantive": round(len(subst_idn) / len(substantive) * 100, 1) if substantive else 0.0,
        "hl1_chunks": len(hl1),
        "by_collection": coll_rows,
        "by_document_type": type_rows,
        "documents": doc_rows,
        "gap_buckets": gap_rows,
        "gap_by_document": {
            did: [{"bucket": b, "chunks": n} for b, n in c.most_common()]
            for did, c in sorted(gap_by_doc.items(), key=lambda kv: -sum(kv[1].values()))
            if sum(c.values())
        },
        "document_title_missing": title_missing,
        "document_title_recoverable_from_uri": title_recoverable,
    }


def render(report: dict) -> str:
    lines = []
    lines.append(f"Corpus identity coverage audit  ({report['n_chunks']:,} chunks)")
    lines.append("=" * 76)
    lines.append(
        f"  identified (act-sec + reg-clause) : {report['identified']:,}/{report['n_chunks']:,} "
        f"({report['pct_identified']}%)"
    )
    lines.append(
        f"  substantive (hl>=2) identified    : {report['substantive_identified']:,}/"
        f"{report['substantive_chunks']:,} ({report['pct_substantive']}%)"
    )
    lines.append(f"  hl1 header/boilerplate floor      : {report['hl1_chunks']:,} (semantically N/A)")
    lines.append("")
    lines.append(f"{'collection':<20}{'subst':>7}{'idn':>7}{'%':>7}")
    for coll, r in report["by_collection"].items():
        lines.append(f"{coll:<20}{r['chunks']:>7}{r['identified']:>7}{r['pct']:>6.1f}%")
    lines.append("")
    lines.append(f"{'doc_type':<14}{'subst':>7}{'idn':>7}{'%':>7}")
    for dt, r in report["by_document_type"].items():
        lines.append(f"{dt:<14}{r['chunks']:>7}{r['identified']:>7}{r['pct']:>6.1f}%")
    lines.append("")
    lines.append("Worst documents (by unidentified substantive chunks):")
    lines.append(f"{'uri':<34}{'type':<13}{'subst':>6}{'idn':>6}{'miss':>6}{'%':>6}{'hl1':>6}")
    for r in report["documents"][:14]:
        lines.append(
            f"{r['uri'][:32]:<34}{r['document_type']!s:<13}{r['substantive']:>6}"
            f"{r['identified']:>6}{r['unidentified_substantive']:>6}{r['pct']:>5.0f}%{r['hl1']:>6}"
        )
    lines.append("")
    lines.append("Remaining gap buckets (unidentified substantive chunks):")
    for g in report["gap_buckets"]:
        lines.append(f"  {g['bucket']:<20}{g['chunks']:>6}")
    lines.append("")
    lines.append(
        f"document_title missing: {report['document_title_missing']:,} "
        f"(recoverable from document_uri: {report['document_title_recoverable_from_uri']:,})"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Corpus identity coverage audit (2026-08-18).")
    parser.add_argument("--live", action="store_true", help="scroll Qdrant + refresh cache (default: frozen cache)")
    parser.add_argument("--json", type=Path, default=OUT_DEFAULT, help="JSON output path")
    parser.add_argument("--no-write", action="store_true", help="print only; do not write JSON")
    args = parser.parse_args()

    payloads = load_payloads(args.live)
    report = audit(payloads)
    print(render(report))
    if not args.no_write:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nJSON -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

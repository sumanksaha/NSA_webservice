"""Subsection/clause coverage audit over the payload index (G5/G6, 2026-08-17).

Reproduces the G5 audit numbers and adds the substantive-vs-hl1 split that
G6 identified as the correct metric:

  * overall ``subsection`` coverage (the headline, dragged down by hl1
    headers/boilerplate that semantically have no subsection),
  * substantive (``hierarchy_level >= 2``) coverage — the number that
    actually matters for P2 / failure-taxonomy precision,
  * per-domain coverage (overall AND substantive),
  * cross-section collisions (subsection values appearing in >=2 sections)
    and within-section distinctness (sections where every chunk shares a
    single subsection value),
  * dotted-clause recovery potential (``clause_number`` — how many chunks
    would gain a value if ``_extract_clause_number`` were applied).

Reads the frozen payload cache (``out/cache/payload_index.jsonl``) by
default; ``--live`` scrolls Qdrant and refreshes the cache.  Writes a JSON
report to ``out/cache/subsection_audit.json``.

Usage:
    python -m evaluation.subsection_audit               # from the frozen cache
    python -m evaluation.subsection_audit --live        # scroll Qdrant, refresh cache
    python -m evaluation.subsection_audit --json out/cache/subsection_audit.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CACHE = PROJECT_ROOT / "evaluation" / "out" / "cache" / "payload_index.jsonl"
OUT_DEFAULT = PROJECT_ROOT / "evaluation" / "out" / "cache" / "subsection_audit.json"


def _domain_of(payload: dict) -> str:
    """Coarse domain bucket for reporting (matches G5/G6 tables)."""
    act = str(payload.get("act_name") or payload.get("document_title") or "?")
    if "Food Safety" in act:
        return "fssai"
    if "Companies" in act:
        return "companies"
    if any(k in act for k in ("Environment", "Water", "Air")):
        return "env"
    if "Contract" in act:
        return "contract"
    if "Specific Relief" in act:
        return "srf"
    if "Kolkata" in act or "Municipal" in act:
        return "kmc"
    if "Cruelty" in act:
        return "pca"
    return "other"


def load_payloads(live: bool) -> list[dict]:
    """Load payloads from the frozen cache, or scroll Qdrant and refresh it."""
    if not live and CACHE.exists():
        out = []
        with open(CACHE, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                out.append(rec["payload"])
        return out
    from app import create_app
    from app.rag.qdrant_client import QdrantStore
    from evaluation.resolution import build_payload_index

    app = create_app()
    with app.app_context():
        collections = list(dict.fromkeys([
            app.config.get("RAG_QDRANT_COLLECTION", "fssai_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_ENV", "env_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_COMMERCIAL", "commercial_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_ANIMAL", "animal_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_WB_STATE", "wb_state_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_CRIMINAL", "criminal_legal_768"),
        ]))
        index = build_payload_index(
            lambda coll: QdrantStore(collection_name=coll),
            collections, force=True,
        )
        return list(index.values())


def audit(payloads: list[dict]) -> dict:
    """Compute the full coverage report over *payloads*."""
    from app.rag.chunker import _extract_clause_number

    total = len(payloads)
    overall_ss = sum(1 for p in payloads if p.get("subsection"))
    substantive = [p for p in payloads if (p.get("hierarchy_level") or 1) >= 2]
    hl1 = [p for p in payloads if (p.get("hierarchy_level") or 1) < 2]
    subst_ss = sum(1 for p in substantive if p.get("subsection"))
    hl1_ss = sum(1 for p in hl1 if p.get("subsection"))

    # per-domain (overall + substantive)
    dom_total: Counter = Counter()
    dom_ss: Counter = Counter()
    dom_subst: Counter = Counter()
    dom_subst_ss: Counter = Counter()
    for p in payloads:
        d = _domain_of(p)
        dom_total[d] += 1
        if p.get("subsection"):
            dom_ss[d] += 1
        if (p.get("hierarchy_level") or 1) >= 2:
            dom_subst[d] += 1
            if p.get("subsection"):
                dom_subst_ss[d] += 1

    # cross-section collisions + within-section distinctness
    value_by_section: defaultdict[tuple[str, str], Counter] = defaultdict(Counter)
    for p in payloads:
        sec = p.get("section_number")
        ss = p.get("subsection")
        if sec is not None and ss:
            value_by_section[(str(sec), _domain_of(p))][ss] += 1
    collisions: dict[str, int] = {}
    for (sec, dom), values in value_by_section.items():
        for v, n in values.items():
            collisions[v] = collisions.get(v, 0) + n
    colliding_values = sorted(
        (v for v, n in collisions.items() if sum(
            1 for (s, _d), vals in value_by_section.items() if v in vals
        ) >= 2),
    )
    # distinctness: sections with >=2 chunks where every chunk shares a
    # single subsection value (G5 semantics — a 1-chunk section is trivially
    # "distinct" and must not count).
    degenerate = [
        (sec, dom, next(iter(values)))
        for (sec, dom), values in value_by_section.items()
        if len(values) == 1 and sum(values.values()) >= 2
    ]

    # dotted-clause recovery potential (clause_number)
    dotted_gain = 0
    dotted_by_dom: Counter = Counter()
    for p in payloads:
        if p.get("subsection"):
            continue
        if _extract_clause_number(p.get("chunk_text") or ""):
            dotted_gain += 1
            dotted_by_dom[_domain_of(p)] += 1

    dom_rows = {}
    for d in sorted(dom_total):
        dom_rows[d] = {
            "chunks": dom_total[d],
            "with_subsection": dom_ss[d],
            "pct_overall": round(dom_ss[d] / dom_total[d] * 100, 1) if dom_total[d] else 0.0,
            "substantive_chunks": dom_subst[d],
            "substantive_with_subsection": dom_subst_ss[d],
            "pct_substantive": round(dom_subst_ss[d] / dom_subst[d] * 100, 1) if dom_subst[d] else 0.0,
        }

    return {
        "n_chunks": total,
        "subsection_overall": overall_ss,
        "pct_overall": round(overall_ss / total * 100, 1) if total else 0.0,
        "hl1_chunks": len(hl1),
        "hl1_with_subsection": hl1_ss,
        "substantive_chunks": len(substantive),
        "substantive_with_subsection": subst_ss,
        "pct_substantive": round(subst_ss / len(substantive) * 100, 1) if substantive else 0.0,
        "by_domain": dom_rows,
        "distinct_subsection_values": len({p.get("subsection") for p in payloads if p.get("subsection")}),
        "cross_section_colliding_values": len(colliding_values),
        "degenerate_sections": len(degenerate),
        "degenerate_sections_sample": degenerate[:10],
        "clause_number_recovery": {
            "chunks_gaining": dotted_gain,
            "by_domain": dict(dotted_by_dom),
            "note": "chunks with no subsection whose text starts with a guarded dotted clause number",
        },
    }


def render(report: dict) -> str:
    """ASCII table rendering of the audit report (cp1252-safe)."""
    lines = []
    lines.append(f"Subsection coverage audit  ({report['n_chunks']:,} chunks)")
    lines.append("=" * 72)
    lines.append(f"  overall subsection coverage : {report['subsection_overall']:,}/{report['n_chunks']:,} "
                 f"({report['pct_overall']}%)")
    lines.append(f"  substantive (hl>=2)         : {report['substantive_with_subsection']:,}/"
                 f"{report['substantive_chunks']:,} ({report['pct_substantive']}%)")
    lines.append(f"  hl1 header/boilerplate      : {report['hl1_with_subsection']:,}/{report['hl1_chunks']:,} "
                 f"(semantically N/A)")
    lines.append(f"  distinct subsection values  : {report['distinct_subsection_values']}")
    lines.append(f"  cross-section collisions    : {report['cross_section_colliding_values']} values "
                 f"appear in >=2 sections")
    lines.append(f"  degenerate sections         : {report['degenerate_sections']} (all chunks share one value)")
    lines.append("")
    lines.append(f"{'domain':<12}{'chunks':>8}{'ss':>6}{'%':>7}{'subst':>8}{'subst_ss':>10}{'%':>7}")
    for d, row in report["by_domain"].items():
        lines.append(f"{d:<12}{row['chunks']:>8}{row['with_subsection']:>6}{row['pct_overall']:>6.1f}%"
                     f"{row['substantive_chunks']:>8}{row['substantive_with_subsection']:>10}"
                     f"{row['pct_substantive']:>6.1f}%")
    rec = report["clause_number_recovery"]
    lines.append("")
    lines.append(f"dotted-clause recovery (clause_number): {rec['chunks_gaining']} chunks "
                 f"{dict(rec['by_domain'])}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Subsection coverage audit (G5/G6).")
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

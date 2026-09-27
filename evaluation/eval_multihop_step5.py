"""Step 5 eval: universal-multihop decision layer against benchmark gold.

What it measures (no LLM, no dense encoder — both unavailable here):
  1. Routing/classification audit (pure): per-question legacy query_type,
     legal_type, effective type, intent, complexity, evidence requirements,
     and route_strategy outcome. Quantifies the Part A blast radius and the
     Step-1 routing coverage per benchmark family.
  2. Firing simulation (real decision code, real corpus text): for each
     question with supporting provisions, pass-1 chunks = the PRIMARY
     provision chunks fetched live from Qdrant (the realistic single-pass
     outcome for a section-citing query), mined with the REAL
     ``_mined_followup_targets`` + ``_build_followup_query`` at
     MEDIUM and LOW floors. Supporting-recall = mined target sections that
     cover SUPPORTING gold sections (would the hop fetch the missing gold?).
     Round 2 repeats from primary+supporting texts (chain resolution).

Writes ``evaluation/out/step5_multihop_eval.json``.

Usage:
    ./venv/bin/python evaluation/eval_multihop_step5.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=True)

from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from app.rag.agent.nodes.linear import (
    _MULTIHOP_CONFIDENCE_RANK,
    _build_followup_query,
    _mined_followup_targets,
)
from app.rag.agent.routing_economics import route_strategy
from app.rag.planning.query_planner import QueryPlanner
from app.rag.retrieval import effective_query_type, normalize_query_type, understand

BENCHMARK = PROJECT_ROOT / "benchmark" / "benchmark_v1.0.jsonl"
OUT = PROJECT_ROOT / "evaluation" / "out" / "step5_multihop_eval.json"

SECTION_BASE_RE = re.compile(r"s(\d+)", re.IGNORECASE)
CANON_SECTION_RE = re.compile(r"section\s+(\d+)", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-z0-9]+")
MAX_CHUNKS_PER_PROVISION = 4
CANDIDATE_POOL = 12


def token_overlap(query: str, text: str) -> int:
    """Shared-token count (transparent proxy for what the ranker prefers)."""
    q = set(TOKEN_RE.findall(query.lower()))
    t = set(TOKEN_RE.findall(text.lower()))
    return len(q & t)


def provision_base(prov: str) -> str | None:
    """Base section number from a gold id (``fssai:s31(1)`` -> ``31``)."""
    m = SECTION_BASE_RE.search(prov.split(":")[-1])
    return m.group(1) if m else None


def canon_sections(canons: list[str]) -> set[str]:
    out: set[str] = set()
    for c in canons:
        m = CANON_SECTION_RE.search(c)
        if m:
            out.add(m.group(1))
    return out


class GoldStore:
    """Gold provision chunk texts, fetched live from Qdrant."""

    def __init__(self) -> None:
        self.client = QdrantClient(
            url=os.environ["RAG_QDRANT_URL"],
            api_key=os.environ["RAG_QDRANT_API_KEY"],
            timeout=20,
        )
        self.cache: dict[tuple[str, str], list[dict]] = {}
        self.misses: list[str] = []

    def fetch(self, collection: str, prov: str, query: str = "") -> list[dict]:
        """Chunk texts for a gold provision.

        Bare section numbers collide across documents in one collection, and
        the benchmark's document ids are stale against the live payload —
        so candidates are ranked by query token overlap (documented proxy
        for what the ranker would prefer) and the top-K kept.
        """
        key = (collection, prov)
        if key in self.cache:
            return self.cache[key]
        suffix = prov.split(":")[-1]
        base = provision_base(prov)
        pooled: list[dict] = []
        attempts: list[tuple[str, str]] = [("provision_id", suffix)]
        if base and base != suffix:
            attempts.append(("provision_id", base))
        if base:
            attempts.append(("section_number", base))
        for field, value in attempts:
            try:
                pts, _ = self.client.scroll(
                    collection,
                    limit=CANDIDATE_POOL,
                    with_payload=True,
                    with_vectors=False,
                    scroll_filter=Filter(must=[FieldCondition(key=field, match=MatchValue(value=value))]),
                )
            except Exception:
                continue
            for p in pts:
                payload = p.payload or {}
                text = payload.get("chunk_text") or ""
                if text and all(c.get("chunk_id") != p.id for c in pooled):
                    pooled.append({
                        "chunk_id": str(p.id),
                        "text": text,
                        "section_number": payload.get("section_number"),
                        "act_name": payload.get("act_name"),
                        "document_title": payload.get("document_title"),
                        "document_type": payload.get("document_type"),
                    })
            if pooled:
                break
        pooled.sort(key=lambda c: token_overlap(query, c["text"]), reverse=True)
        chunks = pooled[:MAX_CHUNKS_PER_PROVISION]
        if not chunks:
            self.misses.append(f"{collection}:{prov}")
        self.cache[key] = chunks
        return chunks


def audit_question(query: str) -> dict:
    u = understand(query)
    plan = QueryPlanner().plan(query)
    decision = route_strategy(
        {"complexity": plan.complexity.value}, u.legal_type, query
    )
    return {
        "legacy_type": u.query_type.value,
        "legal_type": u.legal_type,
        "effective_type": effective_query_type(u.query_type.value, u.legal_type),
        "intent": plan.intent.value,
        "complexity": plan.complexity.value,
        "evidence_requirements": sorted({t.evidence_requirement.value for t in plan.tasks}),
        "strategy": decision["strategy"],
        "tier": decision["tier"],
    }


def simulate(
    store: GoldStore,
    query: str,
    collections: list[str],
    primary: list[str],
    supporting: list[str],
) -> dict:
    """Fire the real mining code on real primary-provision texts."""
    pass1: list[dict] = []
    for prov in primary:
        for coll in collections:
            pass1.extend(store.fetch(coll, prov, query))
            if pass1:
                break
    out: dict = {"pass1_chunks": len(pass1), "pass1_empty": not pass1}
    if not pass1:
        return out
    supp_sections = {b for p in supporting if (b := provision_base(p))}
    out["supporting_sections"] = sorted(supp_sections)
    for floor in ("MEDIUM", "LOW"):
        rank = _MULTIHOP_CONFIDENCE_RANK[floor]
        canons, refs_found, def_rel, covered = _mined_followup_targets(pass1, query, rank)
        out[f"covered_skipped_{floor}"] = covered
        hit = canon_sections(canons) & supp_sections
        out[floor] = {
            "fired": bool(canons),
            "refs_found": refs_found,
            "targets": canons[:4],
            "n_targets": len(canons),
            "definition_relation": def_rel,
            "supporting_hit": sorted(hit),
            "supporting_recall": round(len(hit) / max(len(supp_sections), 1), 3) if supp_sections else None,
        }
        if canons:
            req_check = "definition" in query.lower() and ("define" in query.lower() or "means" in query.lower())
            out[floor]["followup"] = _build_followup_query(
                query, canons[:2], definition_flavor=req_check
            )
    # Round 2 (chain): mine from primary + supporting texts, excluding r1.
    if out.get("MEDIUM", {}).get("fired"):
        r1 = set(out["MEDIUM"]["targets"])
        plus: list[dict] = list(pass1)
        for prov in supporting:
            for coll in collections:
                plus.extend(store.fetch(coll, prov, query))
        canons2, _, _, _ = _mined_followup_targets(
            plus, query, _MULTIHOP_CONFIDENCE_RANK["MEDIUM"], exclude_canons=r1
        )
        out["round2"] = {"new_targets": canons2[:4], "n_new": len(canons2)}
    return out


def main() -> None:
    store = GoldStore()
    questions = [json.loads(line) for line in open(BENCHMARK) if line.strip()]
    per_q: list[dict] = []
    t0 = time.monotonic()
    for d in questions:
        q = d["question"]
        audit = audit_question(q)
        supporting = d.get("supporting_provisions") or []
        primaries = d.get("primary_provisions") or []
        if supporting:
            sim = simulate(store, q, d.get("collections") or [], primaries, supporting)
            sim["mode"] = "supporting"
        elif len(primaries) > 1:
            # Cross-provision shape (e.g. three-regime questions): pass-1 =
            # first primary's chunks, "supporting" = the other primaries.
            sim = simulate(store, q, d.get("collections") or [], primaries[:1], primaries[1:])
            sim["mode"] = "multi-primary"
        else:
            sim = {"skipped": "single-unit question"}
        per_q.append({
            "qid": d["question_id"],
            "families": d.get("question_type") or [],
            "n_gold_units": 1 + len(supporting),
            **audit,
            "simulation": sim,
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "meta": {
            "n_questions": len(per_q),
            "elapsed_s": round(time.monotonic() - t0, 1),
            "fetch_misses": store.misses,
            "note": "pass-1 = primary-provision chunks (single-pass proxy); "
            "supporting-recall = mined sections covering supporting gold.",
        },
        "questions": per_q,
    }
    OUT.write_text(json.dumps(report, indent=1))
    print(f"wrote {OUT} ({len(per_q)} questions)")

    # Console summary per family.
    fam: dict[str, dict] = defaultdict(lambda: {"n": 0, "multi": 0, "fired_med": 0, "recall_sum": 0.0, "recall_n": 0, "fired_low": 0, "direct": 0})
    for q in per_q:
        for f in q["families"]:
            a = fam[f]
            a["n"] += 1
            if q["n_gold_units"] > 1:
                a["multi"] += 1
            if q["strategy"] == "direct":
                a["direct"] += 1
            sim = q["simulation"]
            if isinstance(sim.get("MEDIUM"), dict):
                if sim["MEDIUM"]["fired"]:
                    a["fired_med"] += 1
                if sim["MEDIUM"]["supporting_recall"] is not None:
                    a["recall_sum"] += sim["MEDIUM"]["supporting_recall"]
                    a["recall_n"] += 1
            if isinstance(sim.get("LOW"), dict) and sim["LOW"]["fired"]:
                a["fired_low"] += 1
    print(f"{'family':<22} {'n':>3} {'multi':>5} {'direct':>6} {'fireM':>5} {'fireL':>5} {'supp_recallM':>11}")
    for f, a in sorted(fam.items(), key=lambda kv: -kv[1]["n"]):
        rec = (a["recall_sum"] / a["recall_n"]) if a["recall_n"] else float("nan")
        print(f"{f:<22} {a['n']:>3} {a['multi']:>5} {a['direct']:>6} {a['fired_med']:>5} {a['fired_low']:>5} {rec:>11.3f}")
    print("fetch_misses:", len(store.misses))


if __name__ == "__main__":
    main()

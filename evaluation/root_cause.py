"""Root-cause diagnosis: why hybrid Qdrant + Neo4j does not beat Qdrant-only.

Non-destructive analysis (2026-08-12).  Reads the cached arm results
(A_dense, D_kg_retrieval, E_dense_sparse_kg, F_dense_sparse_kg_rerank) plus
the payload index, and runs a deep-rank Qdrant probe.  Neo4j enrichment
facts are sourced from the cached readiness measurements
(``reports/kg_readiness_measurements_post_rebuild.json``) because the live
Aura database was emptied by a test-suite side effect on 2026-08-12 (see
the incident note in the output); the diagnosis itself uses only captured
data and is unaffected.

Outputs:
    evaluation/out/root_cause_failures.csv   per-question H1-H16 labels
    evaluation/out/hybrid_diagnosis.json     aggregate diagnostics
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("eval.root_cause")

_SECTION_MARKER_RE = re.compile(
    r"(?:^|[\s(])(?:section|sec\.?|s\.?)\s*(\d{1,3})(?:[.)]|\b)", re.IGNORECASE
)


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from app import create_app

    app = create_app()
    with app.app_context():
        return _analyze()


def _analyze() -> int:
    from evaluation.arms import _dense
    from evaluation.benchmark import load_questions
    from evaluation.config import ARMS, OUT_DIR
    from evaluation.metrics import build_ranked_items, item_covers
    from evaluation.report import load_raw
    from evaluation.resolution import FamilyMap, build_payload_index, matches_gold, norm_section

    questions = load_questions()
    q_by_id = {q.question_id: q for q in questions}
    payload_index = build_payload_index(lambda coll: _store(coll), _collections())
    family_map = FamilyMap()
    raw = {arm: load_raw(arm) for arm in ARMS}

    # Neo4j enrichment facts — cached readiness measurements + live status.
    neo = _neo4j_probes()

    rows: list[dict[str, Any]] = []
    deep_rank: dict[str, dict[str, int]] = {}
    text_masked: Counter = Counter()

    for q in questions:
        qid = q.question_id
        a = raw["A_dense"].get(qid, {})
        d = raw["D_kg_retrieval"].get(qid, {})
        e = raw["E_dense_sparse_kg"].get(qid, {})
        f = raw["F_dense_sparse_kg_rerank"].get(qid, {})
        units = q.recall_units()
        relevant = q.relevant_units()

        a_items = build_ranked_items(a, payload_index, family_map)
        d_items = build_ranked_items(d, payload_index, family_map)
        e_items = build_ranked_items(e, payload_index, family_map)

        def covered(items: list) -> set[str]:
            return {u.provision_id for u in units if any(item_covers(i, u) for i in items)}

        dense_units = covered(a_items)
        kg_units = covered(d_items)
        e_units = covered(e_items)

        e_rank: dict[str, int] = {}
        for u in units:
            for i, item in enumerate(e_items, 1):
                if item_covers(item, u):
                    e_rank[u.provision_id] = i
                    break

        kg_novel = kg_units - dense_units
        kg_redundant = kg_units & dense_units
        kg_novel_ranks = [e_rank[u] for u in kg_novel if u in e_rank]

        kg_noise = 0
        for p in d.get("kg_provisions", []):
            item = _kg_item(p, family_map)
            if not any(item_covers(item, u) for u in units):
                kg_noise += 1

        gold_families = {u.family for u in units if u.family}
        kg_families = {
            fam
            for p in d.get("kg_provisions", [])
            for fam in _kg_families(p, family_map)
        }
        wrong_family = bool(kg_families - gold_families) and bool(kg_families)

        from kg.queries import _classify_query_domain, _extract_concept_mentions

        concepts = _extract_concept_mentions(q.question)
        domain_pred = _classify_query_domain(q.question)

        labels: set[str] = set()
        if not kg_units:
            labels.add("H1")
            if not concepts and gold_families:
                labels.add("H13")
        if kg_novel:
            if any(r > 20 for r in kg_novel_ranks):
                labels.add("H2")
                labels.add("H14")
            elif any(r > 5 for r in kg_novel_ranks):
                labels.add("H3")
        if kg_redundant:
            labels.add("H5")
        if kg_noise > 0:
            labels.add("H6")
        if wrong_family:
            labels.add("H7")
        if len(gold_families) >= 2:
            covered_families = {
                u.family for u in units if u.provision_id in e_units and u.family
            }
            if len(covered_families) < 2:
                labels.add("H10")
            if len(concepts) < 2:
                labels.add("H13")
        if "Temporal" in q.question_types:
            statuses = {str(p.get("status") or "") for p in d.get("kg_provisions", [])}
            if any("repeal" in s.lower() or "supersed" in s.lower() for s in statuses):
                labels.add("H11")
        e_r10 = _recall_at(e, e_items, relevant, 10)
        f_r10 = _recall_at(f, build_ranked_items(f, payload_index, family_map), relevant, 10)
        if f_r10 < e_r10:
            labels.add("H15")

        rows.append({
            "question_id": qid,
            "labels": "|".join(sorted(labels)),
            "gold_units": len(units),
            "gold_families": len(gold_families),
            "concepts_matched": len(concepts),
            "domain_predicted": domain_pred or "",
            "dense_gold": len(dense_units),
            "kg_gold": len(kg_units),
            "kg_novel": len(kg_novel),
            "kg_redundant": len(kg_redundant),
            "kg_novel_rank_max": max(kg_novel_ranks) if kg_novel_ranks else None,
            "kg_noise": kg_noise,
            "wrong_family": wrong_family,
            "hybrid_pool_gold": len(e_units),
        })

        # --- deep-rank + section-metadata probe (dense top-50) ---
        collection = q.collections[0] if q.collections else ""
        if not collection:
            continue
        try:
            res = _dense(collection).search(q.question, top_k=50)
        except Exception as exc:
            logger.warning("deep dense failed %s: %s", qid, exc)
            continue
        top50 = [c.chunk_id for c in res.chunks]
        deep_rank[qid] = {}
        for k in (1, 5, 10, 20, 50):
            hit = 0
            for u in relevant:
                for cid in top50[:k]:
                    pl = payload_index.get(str(cid))
                    if pl is not None and matches_gold(pl, u, family_map):
                        hit += 1
                        break
            deep_rank[qid][k] = hit
        for u in relevant:
            for cid in top50:
                pl = payload_index.get(str(cid))
                if pl is None:
                    continue
                fam = family_map.family_s_for_act(
                    pl.get("act_name") or pl.get("document_title") or ""
                )
                if u.family not in fam or u.section is None:
                    continue
                # G6 fix (2026-08-17): ``subsection`` is a leading marker
                # chain, not a section number — drop the old fallback so a
                # dotted clause value (``2.4.15`` → ``"2"``) can never collide
                # with a real section identity.
                sec_meta = norm_section(pl.get("section_number"))
                text = str(pl.get("chunk_text") or "")
                m = _SECTION_MARKER_RE.search(text)
                if sec_meta == u.section:
                    text_masked["exact_metadata_hit"] += 1
                elif not sec_meta and m and m.group(1) == u.section:
                    text_masked["text_only_hit"] += 1
                elif sec_meta and sec_meta != u.section:
                    text_masked["other_section"] += 1

    # ---------------- aggregates ----------------
    n = len(rows)
    label_counts: Counter = Counter()
    for r in rows:
        for lb in r["labels"].split("|"):
            if lb:
                label_counts[lb] += 1

    kg_gold_rate = sum(1 for r in rows if r["kg_gold"]) / n
    kg_novel_rate = sum(1 for r in rows if r["kg_novel"]) / n
    kg_redundant_rate = sum(1 for r in rows if r["kg_redundant"]) / n
    kg_excluded_rate = sum(1 for r in rows if (r["kg_novel"] and (r["kg_novel_rank_max"] or 0) > 20)) / n
    h1_rate = sum(1 for r in rows if "H1" in r["labels"]) / n
    noise_total = sum(r["kg_noise"] for r in rows)
    kg_prov_total = sum(len(d.get("kg_provisions", [])) for d in raw["D_kg_retrieval"].values())
    noise_rate = noise_total / max(kg_prov_total, 1)
    concept_cov = sum(1 for r in rows if r["concepts_matched"] > 0) / n
    domain_acc = sum(
        1 for r in rows
        if r["domain_predicted"] in {d.upper() for d in q_by_id[r["question_id"]].domains}
        or (not r["domain_predicted"] and not q_by_id[r["question_id"]].domains)
    ) / n
    cross_q = [r for r in rows if r["gold_families"] >= 2]
    cross_ok = sum(1 for r in cross_q if "H10" not in r["labels"]) / max(len(cross_q), 1)

    deep_agg: dict[str, Any] = {}
    if deep_rank:
        for k in (1, 5, 10, 20, 50):
            vals = [deep_rank[qid][k] for qid in deep_rank]
            deep_agg[f"recall@{k}"] = round(sum(1 for v in vals if v >= 1) / len(vals), 4)

    diagnosis = {
        "n": n,
        "failure_labels": dict(sorted(label_counts.items(), key=lambda kv: kv[1], reverse=True)),
        "rates": {
            "kg_retrieved_gold": round(kg_gold_rate, 4),
            "kg_novel_gold_vs_dense": round(kg_novel_rate, 4),
            "kg_redundant_gold_vs_dense": round(kg_redundant_rate, 4),
            "kg_novel_excluded_beyond_k20": round(kg_excluded_rate, 4),
            "graph_nothing_useful_H1": round(h1_rate, 4),
            "kg_noise_rate": round(noise_rate, 4),
            "concept_extraction_coverage": round(concept_cov, 4),
            "domain_classification_accuracy": round(domain_acc, 4),
            "cross_domain_questions_ok": round(cross_ok, 4),
        },
        "deep_rank": deep_agg,
        "text_masking": dict(text_masked),
        "neo4j": neo,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "hybrid_diagnosis.json").write_text(
        json.dumps(diagnosis, indent=2, sort_keys=True), encoding="utf-8"
    )
    with open(OUT_DIR / "root_cause_failures.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    return 0


def _recall_at(arm: dict, items: list, relevant, k: int) -> float:
    from evaluation.metrics import item_covers

    if not relevant:
        return 0.0
    hit = 0
    for u in relevant:
        for _i, item in enumerate(items[:k], 1):
            if item_covers(item, u):
                hit += 1
                break
    return hit / len(relevant)


def _kg_item(p: dict, family_map: FamilyMap):  # noqa: F821 - resolved lazily (evaluation.resolution)
    from evaluation.metrics import RankedItem
    from evaluation.resolution import norm_section

    fams = family_map.family_s_for_act(p.get("instrument_title") or p.get("title"))
    return RankedItem(
        kind="kg",
        key=p.get("provision_id") or "",
        family=fams[0] if fams else None,
        section=norm_section(p.get("provision_number")),
    )


def _kg_families(p: dict, family_map: FamilyMap) -> list[str]:  # noqa: F821 - resolved lazily
    return family_map.family_s_for_act(p.get("instrument_title") or p.get("title"))


def _neo4j_probes() -> dict[str, Any]:
    """Enrichment facts from the cached readiness measurements + live status.

    The live Aura database was emptied on 2026-08-12 by a test-suite side
    effect (``push_to_neo4j`` runs ``MATCH (n) DETACH DELETE n``).  All
    diagnosis numbers come from captured data; the enrichment facts below
    are the last known-good measurements (2026-08-11) plus a live probe
    that confirms the current (empty) state so the report can flag it.
    """
    out: dict[str, Any] = {"incident": "live Neo4j emptied by test-suite side effect (2026-08-12)"}

    # Live status (read-only)
    try:
        from kg.queries import LegalKGQueries

        rows = LegalKGQueries()._execute("MATCH (n) RETURN count(n) AS c")
        out["live_total_nodes"] = rows[0]["c"] if rows else None
    except Exception as exc:
        out["live_total_nodes"] = f"error: {str(exc)[:100]}"

    # Cached readiness measurements
    cache = PROJECT_ROOT / "reports" / "kg_readiness_measurements_post_rebuild.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            keep = {
                "semantic_edge_counts": data.get("semantic_edge_counts"),
                "legal_concepts_orphaned": data.get("legal_concepts_orphaned"),
                "provisions_title_only_text": data.get("provisions_title_only_text"),
                "domain_coverage": data.get("domain_coverage"),
                "provision_status_distribution": data.get("provision_status_distribution"),
                "rel_evidence_counts": data.get("rel_evidence_counts"),
                "cross_domain_edges": data.get("cross_domain_edges"),
                "supported_by_coverage": data.get("supported_by_coverage"),
                "authority_coverage": data.get("authority_coverage"),
            }
            out["readiness"] = {k: v for k, v in keep.items() if v is not None}
        except Exception as exc:
            out["readiness_error"] = str(exc)[:100]
    else:
        out["readiness"] = "missing"
    return out


def _store(collection: str):
    from app.rag.qdrant_client import QdrantStore

    return QdrantStore(collection_name=collection)


def _collections() -> list[str]:
    from evaluation.config import _collect_config_snapshot

    return list(dict.fromkeys(_collect_config_snapshot()["qdrant_collections"].values()))


if __name__ == "__main__":
    raise SystemExit(main())

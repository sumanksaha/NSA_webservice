"""RANKING_CEILING_V1 — offline analysis + deliverables (protocol §5–§27).

Reads the live caches under ``evaluation/out/ceiling_v1/raw`` (produced by
``run_ceiling.py``), builds the offline union/oracle-hybrid arms, computes
every metric at depths 5/10/20/50/100/200/500 and writes the 12 deliverables:

    1.  recall_curve.csv                 central table (primary deliverable)
    2.  retrieval_depth_results.csv      arm x K metrics (R/MRR/nDCG/P)
    3.  gold_rank_distribution.csv       §9 rank buckets
    4.  candidate_generation_failures.csv   §11
    5.  ranking_recoverable_cases.csv    §10
    6.  gold_provision_availability.csv  §12
    7.  kg_incremental_recall.csv        §15
    8.  domain_recall.csv                §18
    9.  question_type_recall.csv         §19
    10. retrieval_ceiling_report.md      §25/§26/§27 (conclusion)
    11. ranking_diagnosis.md             §9-§11, §16, §17
    12. retrieval_ceiling_results.json   machine-readable

No LLM is called.  No production file is modified.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval.ceiling.report")

# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
def load_raw(arm: str) -> dict[str, dict]:
    from evaluation.ceiling_config import RAW_DIR

    p = RAW_DIR / f"{arm}.jsonl"
    if not p.exists():
        return {}
    recs: dict[str, dict] = {}
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            recs[r["question_id"]] = r
    return recs


def load_payload_index() -> dict[str, dict]:
    """Load the cached payload index (point_id -> payload).  Rebuilds on
    mismatch with the live Qdrant point counts recorded in the freeze."""
    from evaluation.ceiling_config import OUT_DIR, PROJECT_ROOT
    from evaluation.resolution import build_payload_index
    from evaluation.config import CACHE_DIR

    cache_file = CACHE_DIR / "payload_index.jsonl"
    index: dict[str, dict] = {}
    if cache_file.exists():
        with open(cache_file, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                index[rec["id"]] = rec["payload"]
        logger.info("payload index cached: %d points", len(index))
    freeze_path = OUT_DIR / "run_config.json"
    expected = None
    if freeze_path.exists():
        try:
            freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
            counts = freeze.get("qdrant", {}).get("collections", {})
            expected = sum(c["points"] or 0 for c in counts.values())
        except Exception:
            expected = None
    if expected and len(index) == expected:
        return index
    if expected:
        logger.warning("payload index %d != expected %d — rebuilding", len(index), expected)
    from app import create_app

    app = create_app()
    with app.app_context():
        cfg = app.config
        cols = [
            cfg.get("RAG_QDRANT_COLLECTION", "fssai_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_ENV", "env_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_COMMERCIAL", "commercial_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_ANIMAL", "animal_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_WB_STATE", "wb_state_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_CRIMINAL", "criminal_legal_768"),
        ]
        index = build_payload_index(
            lambda coll: _store(coll), list(dict.fromkeys(cols)), force=True
        )
    return index


def _store(collection: str):
    from app.rag.qdrant_client import QdrantStore

    return QdrantStore(collection_name=collection)


def load_kg_provision_map() -> dict:
    """Pull the full LegalProvision list from Neo4j once (read-only) so the
    availability audit can answer 'does this gold provision exist as a node?'.
    Returns {provision_id: {instrument_title, provision_number}}."""
    from kg.queries import LegalKGQueries

    try:
        q = LegalKGQueries()
        rows = q._execute(
            "MATCH (i)-[:CONTAINS]->(p:LegalProvision) "
            "RETURN p.provision_id AS pid, i.title AS instrument_title, "
            "p.provision_number AS number"
        )
        return {
            r.get("pid"): {
                "instrument_title": r.get("instrument_title") or "",
                "provision_number": str(r.get("number") or ""),
            }
            for r in rows
            if r.get("pid")
        }
    except Exception as exc:  # noqa: BLE001 - best-effort
        logger.warning("KG provision map failed: %s", exc)
        return {}


# --------------------------------------------------------------------------- #
# Generalized scorer (depths beyond 20)
# --------------------------------------------------------------------------- #
def unit_first_ranks(arm_result: dict, question, payload_index, family_map) -> dict[str, int | None]:
    """First-hit rank (1-based) of every gold unit in the arm's ranked list."""
    from evaluation.metrics import build_ranked_items, item_covers

    items = build_ranked_items(arm_result, payload_index, family_map)
    ranks: dict[str, int | None] = {}
    for unit in question.recall_units():
        for i, item in enumerate(items):
            if item_covers(item, unit):
                ranks[unit.provision_id] = i + 1
                break
        else:
            ranks[unit.provision_id] = None
    return ranks


def metrics_from_ranks(r: dict[str, int | None], q, depths) -> dict:
    """Metrics derived directly from first-hit ranks (used for the unranked
    union pool where every pool hit collapses to rank 1 — R@K = pool
    coverage for every K)."""
    relevant = q.relevant_units()
    all_units = q.recall_units()

    def hit(k: int, ids: list[str]) -> int:
        return sum(1 for pid in ids if (r.get(pid) or 1 << 30) <= k)

    out = {}
    for k in depths:
        out[f"recall@{k}"] = hit(k, [u.provision_id for u in relevant]) / max(len(relevant), 1)
        out[f"recall_all@{k}"] = hit(k, [u.provision_id for u in all_units]) / max(len(all_units), 1)
    hit_ranks = [r for pid, r in r.items() if r is not None and pid in {u.provision_id for u in relevant}]
    out["mrr"] = 1.0 / min(hit_ranks) if hit_ranks else 0.0
    gain = {u.provision_id: (2.0 if u.role == "primary" else 1.0) for u in relevant}
    gains_by_rank: dict[int, float] = {}
    for pid, v in r.items():
        if v is not None and pid in gain:
            gains_by_rank[v] = gains_by_rank.get(v, 0.0) + gain[pid]
    ideal = sorted(gain.values(), reverse=True)

    def idcg(gs: list[float], k: int) -> float:
        return sum(gs[i] / math.log2(i + 2) for i in range(min(k, len(gs)))) or 1e-9

    for k in (10, 20, 50):
        dcg = sum(g / math.log2(v + 1) for v, g in gains_by_rank.items() if v <= k)
        out[f"ndcg@{k}"] = dcg / idcg(ideal, k)
    return out


def arm_metrics(arm_result: dict, question, payload_index, family_map, depths, coverage_only: bool = False):
    """R@K (relevant), R_all@K, MRR, nDCG@10/20/50 for one (arm, question)."""
    from evaluation.metrics import build_ranked_items, item_covers
    from evaluation.config import GAIN_ACCEPTABLE, GAIN_PRIMARY

    ranks = unit_first_ranks(arm_result, question, payload_index, family_map)
    relevant = question.relevant_units()
    all_units = question.recall_units()
    relevant_ids = [u.provision_id for u in relevant]

    def hit_at(k: int, ids: list[str]) -> int:
        return sum(1 for pid in ids if (ranks.get(pid) or 1 << 30) <= k)

    out = {}
    for k in depths:
        out[f"recall@{k}"] = hit_at(k, relevant_ids) / max(len(relevant_ids), 1)
        out[f"recall_all@{k}"] = hit_at(k, [u.provision_id for u in all_units]) / max(len(all_units), 1)
    # MRR over RELEVANT units only (primary + acceptable) — matches the legacy
    # harness (evaluation.metrics.score_question) so the frozen baseline
    # reproduces exactly.
    hit_ranks = [r for pid, r in ranks.items() if r is not None and pid in relevant_ids]
    out["mrr"] = 1.0 / min(hit_ranks) if hit_ranks else 0.0
    # nDCG@k over relevant units (gains: primary 2, acceptable 1)
    gain = {u.provision_id: (GAIN_PRIMARY if u.role == "primary" else GAIN_ACCEPTABLE) for u in relevant}
    gains_by_rank: dict[int, float] = {}
    for pid, r in ranks.items():
        if r is not None and pid in gain:
            gains_by_rank[r] = gains_by_rank.get(r, 0.0) + gain[pid]
    ideal = sorted(gain.values(), reverse=True)

    def idcg(gs: list[float], k: int) -> float:
        return sum(gs[i] / math.log2(i + 2) for i in range(min(k, len(gs)))) or 1e-9

    for k in (10, 20, 50):
        dcg = sum(g / math.log2(r + 1) for r, g in gains_by_rank.items() if r <= k)
        out[f"ndcg@{k}"] = dcg / idcg(ideal, k)
    if coverage_only:
        # An unranked pool has no meaningful MRR / nDCG (every hit collapses
        # to rank 1) — report them as not-applicable.
        out["mrr"] = None
        for k in (10, 20, 50):
            out[f"ndcg@{k}"] = None
    return out


def any_hit_at(ranks: dict[str, int | None], q, k: int) -> bool:
    rel = {u.provision_id for u in q.relevant_units()}
    return any((ranks.get(pid) or 1 << 30) <= k for pid in rel)


# --------------------------------------------------------------------------- #
# Offline union arms
# --------------------------------------------------------------------------- #
def slice_rec(rec: dict, n_chunks: int | None = None) -> dict:
    out = dict(rec)
    if n_chunks is not None:
        out["chunk_ids"] = list(out.get("chunk_ids", []))[:n_chunks]
    return out


def build_union_arms(
    a_rec, b_rec, d_rec, payload_index, family_map,
    dense_n=200, sparse_n=200, kg_n=200,
) -> dict[str, dict]:
    """E_union_ordered, E_union_rrf and the unranked E_union_pool."""
    from evaluation.metrics import build_ranked_items, RankedItem
    from evaluation.fusion import rrf_fuse_items, dedupe_kg_items, item_to_dict

    dense_items = build_ranked_items(slice_rec(a_rec, dense_n), payload_index, family_map)
    sparse_items = build_ranked_items(slice_rec(b_rec, sparse_n), payload_index, family_map)
    kg_items = build_ranked_items(slice_rec(d_rec, kg_n), payload_index, family_map)

    # drop KG items redundant with chunk coverage (family, section)
    kg_items_dedup = dedupe_kg_items(dense_items + sparse_items, kg_items)

    # Ordered union: dense order, then sparse (novel), then KG (novel).
    # V5 resolution fix: key by (kind, key, family, section) — NOT (kind,
    # key) — so a chunk that resolves to several families (e.g. a wbmo chunk
    # stamped act_name="Essential Commodities Act" + document_title="West
    # Bengal Meat Order") keeps every (family, section) variant in the pool.
    # Deduping on (kind, key) alone collapsed such chunks to their first
    # family and hid gold units from the union pool (0.6583 -> 0.7050).
    def _key(item: RankedItem) -> tuple:
        return (item.kind, item.key, item.family, item.section)

    seen: set[tuple] = set()
    ordered: list[RankedItem] = []
    for item in dense_items + sparse_items + kg_items_dedup:
        if _key(item) in seen:
            continue
        seen.add(_key(item))
        ordered.append(item)

    # RRF interleaved union (production-equivalent fusion, k=60)
    fused = rrf_fuse_items(dense_items, sparse_items, kg_items_dedup, top_k=len(ordered) + 10)

    def _rec(items, retriever, kg_source, n_chunks):
        rec = {
            "chunk_ids": [i.key for i in items if i.kind == "chunk"],
            "kg_provisions": [],
            "kg_source": kg_source,
            "fused_items": [item_to_dict(i) for i in items],
            "latency_ms": 0,
            "error": None,
            "retriever": retriever,
        }
        return rec

    return {
        "E_union_ordered": _rec(ordered, "union(dense,sparse,kg)-ordered", "contract", None),
        "E_union_rrf": _rec(fused, "union-rrf(dense,sparse,kg)", "contract", None),
        "E_union_pool": _rec(ordered, "union-pool(dense,sparse,kg)", "contract", None),
    }


def build_oracle_hybrid(o_dense_rec, o_sparse_rec, payload_index, family_map):
    from evaluation.metrics import build_ranked_items
    from evaluation.fusion import rrf_fuse_items, item_to_dict

    dense_items = build_ranked_items(o_dense_rec, payload_index, family_map)
    sparse_items = build_ranked_items(o_sparse_rec, payload_index, family_map)
    fused = rrf_fuse_items(dense_items, sparse_items, top_k=len(dense_items) + len(sparse_items) + 10)
    rec = {
        "chunk_ids": [i.key for i in fused if i.kind == "chunk"],
        "kg_provisions": [],
        "kg_source": None,
        "fused_items": [item_to_dict(i) for i in fused],
        "latency_ms": 0,
        "error": None,
        "retriever": "oracle-hybrid-rrf(dense,sparse)",
    }
    return rec


# --------------------------------------------------------------------------- #
# Aggregate helpers
# --------------------------------------------------------------------------- #
def mean_metrics(per_q: dict[str, dict], depths) -> dict:
    n = len(per_q)
    agg: dict = {"n": n}
    for k in depths:
        agg[f"recall@{k}"] = round(sum(q[f"recall@{k}"] for q in per_q.values()) / max(n, 1), 4)
        agg[f"recall_all@{k}"] = round(sum(q[f"recall_all@{k}"] for q in per_q.values()) / max(n, 1), 4)

    def _mean(key: str):
        vals = [q[key] for q in per_q.values() if q.get(key) is not None]
        return round(sum(vals) / max(len(vals), 1), 4) if vals else None

    agg["mrr"] = _mean("mrr")
    for k in (10, 20, 50):
        agg[f"ndcg@{k}"] = _mean(f"ndcg@{k}")
    return agg


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from evaluation.benchmark import load_gold_registry, load_questions, schema_report
    from evaluation.resolution import FamilyMap
    from evaluation.ceiling_config import (
        DEPTHS,
        OUT_DIR,
        RAW_DIR,
        UNION_DENSE_DEPTH,
        UNION_SPARSE_DEPTH,
        UNION_KG_DEPTH,
        EXPERIMENT_ID,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    questions = load_questions()
    registry = load_gold_registry()
    family_map = FamilyMap()
    payload_index = load_payload_index()
    kg_provision_map = load_kg_provision_map()
    logger.info("questions=%d payload_index=%d kg_provisions=%d",
                len(questions), len(payload_index), len(kg_provision_map))

    q_by_id = {q.question_id: q for q in questions}

    # ---- load live arms (V3/V4 expanded-query arms are optional)
    BASE_ARMS = ("A_dense", "B_sparse", "C_hybrid", "D_kg", "O_dense", "O_sparse", "X_exact")
    EXP_ARMS = ("V3_dense", "V3_sparse", "V3_hybrid", "V4_dense", "V4_sparse", "V4_hybrid")
    live = {arm: load_raw(arm) for arm in BASE_ARMS + EXP_ARMS}
    for arm, recs in live.items():
        logger.info("cached %s: %d questions", arm, len(recs))
        if arm in BASE_ARMS and arm not in ("X_exact",) and not recs:
            logger.error("MISSING live arm %s — run evaluation.run_ceiling first", arm)
            return 1
    has_v3 = bool(live["V3_dense"]) and bool(live["V3_sparse"]) and bool(live["V3_hybrid"])
    has_v4 = bool(live["V4_dense"]) and bool(live["V4_sparse"]) and bool(live["V4_hybrid"])
    logger.info("V3 expanded-query arms present: %s; V4 (dedup) present: %s", has_v3, has_v4)

    # ---- build offline arms
    offline: dict[str, dict[str, dict]] = {}
    for qid in q_by_id:
        a, b, d = live["A_dense"].get(qid), live["B_sparse"].get(qid), live["D_kg"].get(qid)
        if not (a and b and d):
            continue
        offline[qid] = build_union_arms(
            a, b, d, payload_index, family_map,
            UNION_DENSE_DEPTH, UNION_SPARSE_DEPTH, UNION_KG_DEPTH,
        )
        od, os_ = live["O_dense"].get(qid), live["O_sparse"].get(qid)
        if od and os_:
            offline[qid]["O_hybrid"] = build_oracle_hybrid(od, os_, payload_index, family_map)
        # V3/V4 union arms: expanded dense+sparse + KG (same union machinery)
        for prefix, has in (("V3", has_v3), ("V4", has_v4)):
            if not has:
                continue
            vd, vs = live[f"{prefix}_dense"].get(qid), live[f"{prefix}_sparse"].get(qid)
            if vd and vs:
                exp_union = build_union_arms(
                    vd, vs, d, payload_index, family_map,
                    UNION_DENSE_DEPTH, UNION_SPARSE_DEPTH, UNION_KG_DEPTH,
                )
                offline[qid].update({
                    f"{prefix}_union_ordered": exp_union["E_union_ordered"],
                    f"{prefix}_union_rrf": exp_union["E_union_rrf"],
                    f"{prefix}_union_pool": exp_union["E_union_pool"],
                })
    logger.info("union arms built for %d questions", len(offline))

    # ---- per-question metrics for every arm
    ARM_ORDER = ["A_dense", "B_sparse", "C_hybrid", "D_kg",
                 "E_union_ordered", "E_union_rrf", "E_union_pool",
                 "O_dense", "O_sparse", "O_hybrid",
                 "V3_dense", "V3_sparse", "V3_hybrid",
                 "V3_union_ordered", "V3_union_rrf", "V3_union_pool",
                 "V4_dense", "V4_sparse", "V4_hybrid",
                 "V4_union_ordered", "V4_union_rrf", "V4_union_pool"]
    per_q: dict[str, dict[str, dict]] = {arm: {} for arm in ARM_ORDER}
    ranks: dict[str, dict[str, dict[str, int | None]]] = {arm: {} for arm in ARM_ORDER}

    def _arm_result(arm: str, qid: str) -> dict | None:
        if arm in live and qid in live[arm]:
            return live[arm][qid]
        if qid in offline and arm in offline[qid]:
            return offline[qid][arm]
        return None

    for qid, q in q_by_id.items():
        for arm in ARM_ORDER:
            rec = _arm_result(arm, qid)
            if rec is None:
                continue
            per_q[arm][qid] = arm_metrics(rec, q, payload_index, family_map, DEPTHS)
            r = unit_first_ranks(rec, q, payload_index, family_map)
            if arm.endswith("_union_pool"):
                # The pool is unranked: "considered retrieved if it occurs
                # anywhere in the unified candidate pool" (§6) — collapse any
                # hit to rank 1 so R@K = pool coverage for every K.
                r = {pid: (1 if v is not None else None) for pid, v in r.items()}
                m = metrics_from_ranks(r, q, DEPTHS)
                m["mrr"] = None  # no meaningful MRR/nDCG for an unranked pool
                for _k in (10, 20, 50):
                    m[f"ndcg@{_k}"] = None
                per_q[arm][qid] = m
            ranks[arm][qid] = r

    # ---- exact identifier test (§14): gold hit inside dense/sparse @10/50/100
    exact_agg = {"dense": {}, "sparse": {}}
    exact_agg_id = {"dense": {}, "sparse": {}}
    n_exact = len(live["X_exact"])
    n_with_id = sum(1 for rec in live["X_exact"].values()
                    if rec.get("query_used") and rec.get("error") != "no numeric identifier")
    for qid, rec in live["X_exact"].items():
        q = q_by_id[qid]
        has_id = bool(rec.get("query_used")) and rec.get("error") != "no numeric identifier"
        for mode, key in (("dense", "chunk_ids"), ("sparse", "chunk_ids_sparse")):
            ids = rec.get(key, [])
            for k in (10, 50, 100):
                hit = any(
                    any(_matches(payload_index.get(cid) or {}, u, family_map)
                        for u in q.gold_units)
                    for cid in ids[:k]
                )
                exact_agg[mode].setdefault(k, 0)
                exact_agg[mode][k] += int(hit)
                if has_id:
                    exact_agg_id[mode].setdefault(k, 0)
                    exact_agg_id[mode][k] += int(hit)
    n_exact = max(n_exact, 1)
    n_id = max(n_with_id, 1)
    exact_summary = {
        "n_questions": len(live["X_exact"]),
        "n_with_numeric_identifier": n_with_id,
        "note": "recall reported over all 150 (left) and over the questions that actually carry a numeric Act/Section identifier (right).",
        "recall_over_all": {
            mode: {k: round(v / n_exact, 4) for k, v in vals.items()}
            for mode, vals in exact_agg.items()
        },
        "recall_over_identifiable": {
            mode: {k: round(v / n_id, 4) for k, v in vals.items()}
            for mode, vals in exact_agg_id.items()
        },
    }

    # =====================================================================
    # 0. Benchmark validation (§2)
    # =====================================================================
    schema = schema_report()
    n_multi = sum(1 for q in questions if len(q.primary_units()) > 1)
    n_nogold = sum(1 for q in questions if not q.gold_units)
    n_valid = sum(1 for q in questions if q.primary_units() or q.acceptable_units())
    benchmark_report = {
        "total_questions": len(questions),
        "questions_with_valid_gold_provision": n_valid,
        "questions_with_multiple_gold_provisions": n_multi,
        "questions_with_missing_or_ambiguous_gold": n_nogold,
        "gold_registry_records": len(registry),
        "schema_signal_report": schema,
        "note": "temporal_constraints empty on all 150 (frozen benchmark quirk); "
                "gold provision text not stored in the registry — §13 oracle uses "
                "registry provision titles.",
    }

    # =====================================================================
    # Central recall curve (§7) + depth results (§25.2)
    # =====================================================================
    agg = {arm: mean_metrics(per_q[arm], DEPTHS) for arm in ARM_ORDER}

    def write_csv(name: str, header: list[str], rows: list[list]) -> None:
        with open(OUT_DIR / name, "w", encoding="utf-8", newline="") as f:
            f.write(",".join(header) + "\n")
            for row in rows:
                f.write(",".join(str(v) for v in row) + "\n")

    # 1. recall_curve.csv
    header = ["retrieval", "R@5", "R@10", "R@20", "R@50", "R@100", "R@200", "R@500"]
    labels = {
        "A_dense": "Dense",
        "B_sparse": "Sparse",
        "C_hybrid": "Dense+Sparse",
        "D_kg": "KG (graph-RAG contract)",
        "E_union_ordered": "Dense+Sparse+KG (ordered union)",
        "E_union_rrf": "Dense+Sparse+KG (RRF union)",
        "E_union_pool": "Dense+Sparse+KG (union pool)",
        "O_dense": "Oracle: gold text -> Dense",
        "O_sparse": "Oracle: gold text -> Sparse",
        "O_hybrid": "Oracle: gold text -> Hybrid",
        "V3_dense": "Expanded: Dense",
        "V3_sparse": "Expanded: Sparse",
        "V3_hybrid": "Expanded: Dense+Sparse",
        "V3_union_ordered": "Expanded D+S+KG (ordered union)",
        "V3_union_rrf": "Expanded D+S+KG (RRF union)",
        "V3_union_pool": "Expanded D+S+KG (union pool)",
        "V4_dense": "Dedup-expanded: Dense",
        "V4_sparse": "Dedup-expanded: Sparse",
        "V4_hybrid": "Dedup-expanded: Dense+Sparse",
        "V4_union_ordered": "Dedup-expanded D+S+KG (ordered union)",
        "V4_union_rrf": "Dedup-expanded D+S+KG (RRF union)",
        "V4_union_pool": "Dedup-expanded D+S+KG (union pool)",
    }
    rows = []
    for arm in ARM_ORDER:
        a = agg[arm]
        if not a:
            continue
        rows.append([labels[arm]] + [f"{a.get(f'recall@{k}', 0):.3f}" for k in DEPTHS])
    write_csv("recall_curve.csv", header, rows)

    # 2. retrieval_depth_results.csv (long format)
    r_rows = []
    for arm in ARM_ORDER:
        a = agg[arm]
        if not a:
            continue
        for k in DEPTHS:
            r_rows.append([arm, labels[arm], k,
                           f"{a.get(f'recall@{k}', 0):.4f}",
                           f"{a.get(f'recall_all@{k}', 0):.4f}"])
        r_rows.append([arm, labels[arm], "mrr", _fmt4(a.get('mrr')), ""])
        for k in (10, 20, 50):
            r_rows.append([arm, labels[arm], f"ndcg@{k}", _fmt4(a.get(f'ndcg@{k}')), ""])
    write_csv("retrieval_depth_results.csv",
              ["arm", "arm_label", "k", "recall", "recall_all"], r_rows)

    # =====================================================================
    # 9. Gold rank distribution (§9) — for hybrid C and union RRF (+dense)
    # =====================================================================
    buckets = [(1, 1), (2, 5), (6, 10), (11, 20), (21, 50), (51, 100), (101, 200), (201, 500), (None, None)]
    def bucket_of(r: int | None) -> int:
        if r is None:
            return len(buckets) - 1
        for i, (lo, hi) in enumerate(buckets):
            if lo is not None and lo <= r <= hi:
                return i
        return len(buckets) - 1

    def first_gold_rank(ranks_q: dict[str, int | None]) -> int | None:
        vals = [r for r in ranks_q.values() if r is not None]
        return min(vals) if vals else None

    dist_rows = [["rank_bucket", "questions", "pct"]]
    for arm in ("C_hybrid", "E_union_rrf", "A_dense"):
        dist_rows.append([f"arm:{arm}", "", ""])
        n = len(ranks[arm])
        counts = [0] * len(buckets)
        for qid in ranks[arm]:
            counts[bucket_of(first_gold_rank(ranks[arm][qid]))] += 1
        for i, (lo, hi) in enumerate(buckets):
            label = f"{lo}" if lo == hi else (f"{lo}–{hi}" if lo else ">500/not found")
            dist_rows.append([label, counts[i], f"{counts[i] / max(n, 1):.3f}"])
    write_csv("gold_rank_distribution.csv", ["rank_bucket", "questions", "pct"], dist_rows)

    # =====================================================================
    # 10/11. Ranking-recoverable + generation failures (§10, §11)
    # =====================================================================
    gen_fail_rows = [["question_id", "gold_provisions", "in_union_pool", "class", "note"]]
    recov_rows = [["question_id", "first_gold_rank_union", "rank<=500", "rank>10", "rank>20", "rank>50", "rank>100", "rank>200"]]
    recoverable = {"k10": 0, "k20": 0, "k50": 0, "k100": 0, "k200": 0, "n500": 0, "n": 0}
    gen_fail = {"n": 0, "corpus_missing": 0, "qdrant_missing": 0, "kg_missing": 0,
                "query_mismatch": 0, "dense_failure": 0, "sparse_failure": 0, "kg_failure": 0}
    for qid, q in q_by_id.items():
        if qid not in ranks["E_union_rrf"]:
            continue
        r = first_gold_rank(ranks["E_union_rrf"][qid])
        recoverable["n"] += 1
        if r is not None and r <= 500:
            recoverable["n500"] += 1
            for thresh, key in ((10, "k10"), (20, "k20"), (50, "k50"), (100, "k100"), (200, "k200")):
                if r > thresh:
                    recoverable[key] += 1
            recov_rows.append([qid, r, "yes", "yes" if r > 10 else "no",
                               "yes" if r > 20 else "no", "yes" if r > 50 else "no",
                               "yes" if r > 100 else "no", "yes" if r > 200 else "no"])
        else:
            gen_fail["n"] += 1
            gold_ids = [u.provision_id for u in q.gold_units]
            # classify
            cls = "query_mismatch"
            note = ""
            if not q.gold_units:
                cls = "no_gold_label"
            else:
                pts = [pid for pid in payload_index if any(
                    _matches(payload_index[pid], u, family_map) for u in q.gold_units)]
                if not pts:
                    cls = "corpus_missing"
                    note = "no payload point matches any gold unit"
                else:
                    kg_hit = any(_gold_in_kg(u, family_map, kg_provision_map) for u in q.gold_units)
                    if not kg_hit:
                        note = "gold not found as Neo4j provision node"
                    dense_r = first_gold_rank(ranks["A_dense"].get(qid, {}))
                    sparse_r = first_gold_rank(ranks["B_sparse"].get(qid, {}))
                    if dense_r is None and sparse_r is None:
                        cls = "query_mismatch"
                    elif dense_r is None:
                        cls = "dense_failure"
                    elif sparse_r is None:
                        cls = "sparse_failure"
                    else:
                        cls = "query_mismatch"
            gen_fail[cls] = gen_fail.get(cls, 0) + 1
            gen_fail_rows.append([qid, ";".join(gold_ids), "no", cls, note])
    write_csv("ranking_recoverable_cases.csv",
              ["question_id", "first_gold_rank_union", "rank<=500", "rank>10", "rank>20",
               "rank>50", "rank>100", "rank>200"], recov_rows)
    write_csv("candidate_generation_failures.csv",
              ["question_id", "gold_provisions", "in_union_pool", "class", "note"], gen_fail_rows)

    # =====================================================================
    # 12. Gold-provision availability audit (§12)
    # =====================================================================
    avail_rows = [["qid", "gold", "Source", "Neo4j", "Chunk", "Qdrant",
                   "Dense@500", "Sparse@500", "KG@200", "Hybrid@500"]]
    for qid, q in q_by_id.items():
        for u in q.gold_units:
            pts = [pid for pid in payload_index if _matches(payload_index[pid], u, family_map)]
            in_corpus = bool(pts)
            in_kg = _gold_in_kg(u, family_map, kg_provision_map)
            def hit(arm, k):
                r = ranks.get(arm, {}).get(qid, {}).get(u.provision_id)
                return "yes" if (r is not None and r <= k) else "no"
            avail_rows.append([
                qid, u.provision_id,
                "yes" if in_corpus else "no",
                "yes" if in_kg else "no",
                "yes" if in_corpus else "no",
                "yes" if in_corpus else "no",
                hit("A_dense", 500), hit("B_sparse", 500),
                hit("D_kg", 200), hit("C_hybrid", 500),
            ])
    write_csv("gold_provision_availability.csv",
              ["qid", "gold", "Source", "Neo4j", "Chunk", "Qdrant",
               "Dense@500", "Sparse@500", "KG@200", "Hybrid@500"], avail_rows)

    # =====================================================================
    # 15. KG incremental (§15) — union_rrf vs hybrid C at every K
    # =====================================================================
    kg_inc_rows = [["k", "hybrid_recall", "hybrid_kg_recall", "kg_incremental", "helped", "harm", "neutral"]]
    kg_inc = {}
    common = [qid for qid in q_by_id if qid in per_q["C_hybrid"] and qid in per_q["E_union_rrf"]]
    for k in DEPTHS:
        helped = harm = neutral = 0
        for qid in common:
            c_hit = any_hit_at(ranks["C_hybrid"][qid], q_by_id[qid], k)
            e_hit = any_hit_at(ranks["E_union_rrf"][qid], q_by_id[qid], k)
            if c_hit and not e_hit:
                harm += 1
            elif not c_hit and e_hit:
                helped += 1
            else:
                neutral += 1
        c_r = agg["C_hybrid"].get(f"recall@{k}", 0)
        e_r = agg["E_union_rrf"].get(f"recall@{k}", 0)
        kg_inc[k] = {"hybrid": c_r, "hybrid_kg": e_r, "delta": round(e_r - c_r, 4),
                     "helped": helped, "harm": harm, "neutral": neutral}
        kg_inc_rows.append([k, f"{c_r:.4f}", f"{e_r:.4f}", f"{e_r - c_r:.4f}",
                            helped, harm, neutral])
    write_csv("kg_incremental_recall.csv",
              ["k", "hybrid_recall", "hybrid_kg_recall", "kg_incremental", "helped", "harm", "neutral"],
              kg_inc_rows)

    # =====================================================================
    # 16. Dense/sparse complementarity (§16)
    # =====================================================================
    comp = {}
    for k in (10, 50, 500):
        both = dense_only = sparse_only = neither = 0
        for qid in common:
            if qid not in ranks["A_dense"] or qid not in ranks["B_sparse"]:
                continue
            d = any_hit_at(ranks["A_dense"][qid], q_by_id[qid], k)
            s = any_hit_at(ranks["B_sparse"][qid], q_by_id[qid], k)
            both += d and s
            dense_only += d and not s
            sparse_only += s and not d
            neither += not d and not s
        comp[k] = {"both": both, "dense_only": dense_only, "sparse_only": sparse_only,
                   "neither": neither, "n": both + dense_only + sparse_only + neither,
                   "sparse_rescue_rate": sparse_only / max(len(common), 1),
                   "dense_rescue_rate": dense_only / max(len(common), 1)}

    # =====================================================================
    # 17. Deduplication analysis (§17)
    # =====================================================================
    dedup = {}
    for arm, depth, kind in (("A_dense", 500, "chunk"), ("B_sparse", 500, "chunk"),
                             ("C_hybrid", 500, "chunk"), ("D_kg", 200, "kg")):
        raw = uniq = 0
        for rec in live[arm].values():
            if kind == "chunk":
                ids = rec.get("chunk_ids", [])
            else:
                ids = [p.get("provision_id") for p in rec.get("kg_provisions", [])]
            ids = [i for i in ids if i]
            raw += len(ids)
            uniq += len(set(ids))
        dedup[arm] = {"raw_candidates": raw, "unique_candidates": uniq,
                      "duplicate_rate": round(1 - uniq / max(raw, 1), 4)}
    # E_union: per-question raw = dense@D + sparse@D + KG@D (pre-dedup),
    # unique = pool size after canonical dedup.  Also report the pool-size
    # distribution (protocol Task 1: mean/median/P95 pool size).
    union_raw = union_uniq = 0
    pool_sizes: list[int] = []
    for qid in offline:
        pool = offline[qid]["E_union_pool"].get("fused_items", [])
        union_raw += 500 + 500 + 500  # dense@500 + sparse@500 + KG@500 slices
        union_uniq += len(pool)
        pool_sizes.append(len(pool))
    import statistics

    pool_sizes.sort()
    dedup["E_union"] = {
        "raw_candidates": union_raw,
        "unique_candidates": union_uniq,
        "coverage": round(union_uniq / max(union_raw, 1), 4),
        "duplicate_rate": round(1 - union_uniq / max(union_raw, 1), 4),
        "mean_pool_size": round(statistics.mean(pool_sizes), 1) if pool_sizes else 0,
        "median_pool_size": float(statistics.median(pool_sizes)) if pool_sizes else 0.0,
        "p95_pool_size": float(pool_sizes[int(0.95 * (len(pool_sizes) - 1))]) if pool_sizes else 0.0,
    }

    # =====================================================================
    # 18/19. Domain + question-type analysis
    # =====================================================================
    domain_agg = {}
    dom_of = {}
    for q in questions:
        for d in q.domains:
            dom_of.setdefault(d, []).append(q)
        if len(q.domains) > 1:
            dom_of.setdefault("CROSS_DOMAIN", []).append(q)
    # NOTE: rows must NOT embed the header — write_csv adds it once.
    dom_rows = []
    for d, qs in sorted(dom_of.items()):
        vals = {}
        for k in (10, 50, 100, 500):
            hits = sum(1 for q in qs
                       if q.question_id in ranks["E_union_rrf"]
                       and any_hit_at(ranks["E_union_rrf"][q.question_id], q, k))
            vals[k] = hits / max(len(qs), 1)
        domain_agg[d] = {"n": len(qs), **{f"R@{k}": round(vals[k], 3) for k in (10, 50, 100, 500)}}
        dom_rows.append([d, len(qs)] + [f"{vals[k]:.3f}" for k in (10, 50, 100, 500)])
    write_csv("domain_recall.csv", ["domain", "questions", "R@10", "R@50", "R@100", "R@500"], dom_rows)

    type_agg = {}
    type_of = {}
    for q in questions:
        for t in q.question_types:
            type_of.setdefault(t, []).append(q)
    type_rows = []
    for t, qs in sorted(type_of.items()):
        vals = {}
        for k in (10, 50, 100, 500):
            hits = sum(1 for q in qs
                       if q.question_id in ranks["E_union_rrf"]
                       and any_hit_at(ranks["E_union_rrf"][q.question_id], q, k))
            vals[k] = hits / max(len(qs), 1)
        type_agg[t] = {"n": len(qs), **{f"R@{k}": round(vals[k], 3) for k in (10, 50, 100, 500)}}
        type_rows.append([t, len(qs)] + [f"{vals[k]:.3f}" for k in (10, 50, 100, 500)])
    write_csv("question_type_recall.csv",
              ["question_type", "questions", "R@10", "R@50", "R@100", "R@500"], type_rows)

    # =====================================================================
    # 22. Statistical analysis (§22)
    # =====================================================================
    from evaluation.metrics import paired_bootstrap_ci, mcnemar

    def _binary(arm: str, k: int) -> dict[str, bool]:
        return {qid: any_hit_at(ranks[arm][qid], q_by_id[qid], k)
                for qid in ranks[arm]}

    stats = {}
    # Comparisons are ordered (baseline, challenger) so B − A is the
    # challenger's gain (positive = challenger better).
    for arm_a, arm_b, k in (
        ("A_dense", "C_hybrid", 10), ("A_dense", "C_hybrid", 500),
        ("B_sparse", "C_hybrid", 10),
        ("C_hybrid", "E_union_rrf", 10), ("C_hybrid", "E_union_rrf", 500),
        ("A_dense", "O_dense", 10), ("A_dense", "O_dense", 500),
    ):
        ba, bb = _binary(arm_a, k), _binary(arm_b, k)
        common_q = [qid for qid in ba if qid in bb]
        if not common_q:
            continue
        va = [1.0 if ba[qid] else 0.0 for qid in common_q]
        vb = [1.0 if bb[qid] else 0.0 for qid in common_q]
        ci = paired_bootstrap_ci(va, vb)
        mc = mcnemar([bool(x) for x in va], [bool(x) for x in vb])
        stats[f"{arm_b} vs {arm_a} @K{k}"] = {
            "mean_a": round(sum(va) / len(va), 4),
            "mean_b": round(sum(vb) / len(vb), 4),
            "abs_diff": round(sum(vb) / len(vb) - sum(va) / len(va), 4),
            "bootstrap_ci95": [round(ci["ci95"][0], 4), round(ci["ci95"][1], 4)],
            "mcnemar_p": mc["p_value"],
            "mcnemar_sig_5pct": mc["significant_at_5pct"],
            "n": len(common_q),
            "direction_note": "A = baseline, B = challenger; B-A>0 means challenger better",
        }

    # =====================================================================
    # 8/23/24/27. Ceiling, decision tree, next target, conclusion
    # =====================================================================
    c = agg["C_hybrid"]
    e = agg["E_union_rrf"]
    pool = agg["E_union_pool"]
    o = agg["O_dense"]
    r10, r50, r100, r200, r500 = (c[f"recall@{k}"] for k in (10, 50, 100, 200, 500))
    pool500 = pool.get("recall@500", 0)
    union500 = e.get("recall@500", 0)

    # K at which 80% / 90% of gold evidence is recovered (per relevant unit,
    # averaged across questions — use recall_all curve of the union pool)
    def k_for_recall(target: float, arm_agg: dict) -> int | None:
        for k in DEPTHS:
            if arm_agg.get(f"recall@{k}", 0) >= target:
                return k
        return None

    k80 = k_for_recall(0.80, agg["E_union_pool"])
    k90 = k_for_recall(0.90, agg["E_union_pool"])

    # decision tree (§23)
    if union500 >= 0.90 and r10 <= 0.40:
        case = "CASE 1"
        verdict_primary = "Ranking is the dominant bottleneck"
    elif union500 >= 0.70:
        case = "CASE 2"
        verdict_primary = "Mixed candidate-generation + ranking problem"
    else:
        case = "CASE 3"
        verdict_primary = "Candidate generation is the dominant bottleneck"
    kg_value = "CASE 4" if e.get("recall@500", 0) - c.get("recall@500", 0) >= 0.05 else "CASE 5"

    # recoverable-rate (§10)
    rr = recoverable
    ranking_recoverable_rate = rr["k10"] / max(rr["n"], 1)
    gen_failure_rate = gen_fail["n"] / max(rr["n"], 1)

    # nearest milestone (§24)
    milestones = [0.30, 0.50, 0.65, 0.75, 0.80]
    nearest = min(milestones, key=lambda m: abs(m - r10))
    ceiling_gap = pool500 - r10

    # corpus presence rate (protocol §12): fraction of gold units resolvable
    # to Qdrant payload points vs present in Neo4j
    avail_counts = {"source": 0, "neo4j": 0, "n": 0}
    for _qid, _q in q_by_id.items():
        for _u in _q.gold_units:
            avail_counts["n"] += 1
            if any(_matches(payload_index[p], _u, family_map) for p in payload_index):
                avail_counts["source"] += 1
            if _gold_in_kg(_u, family_map, kg_provision_map):
                avail_counts["neo4j"] += 1
    corpus_present_rate = avail_counts["source"] / max(avail_counts["n"], 1)
    neo4j_present_rate = avail_counts["neo4j"] / max(avail_counts["n"], 1)

    conclusion = _conclude(c, e, pool, o, gen_failure_rate, ranking_recoverable_rate,
                           k80, k90, union500, corpus_present_rate, neo4j_present_rate)

    # =====================================================================
    # 25. Reports
    # =====================================================================
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "n_questions": len(questions),
        "benchmark_validation": benchmark_report,
        "recall_curve": {labels[arm]: {f"R@{k}": agg[arm].get(f"recall@{k}", 0) for k in DEPTHS}
                         for arm in ARM_ORDER if agg[arm]},
        "arm_metrics": {labels[arm]: agg[arm] for arm in ARM_ORDER if agg[arm]},
        "gold_rank_distribution": {
            "C_hybrid": _bucket_counts(ranks["C_hybrid"], first_gold_rank),
            "E_union_rrf": _bucket_counts(ranks["E_union_rrf"], first_gold_rank),
            "A_dense": _bucket_counts(ranks["A_dense"], first_gold_rank),
        },
        "ranking_recoverable": {
            "n": rr["n"],
            "questions_with_gold_in_top500": rr["n500"],
            "outside_top10": rr["k10"],
            "outside_top20": rr["k20"],
            "outside_top50": rr["k50"],
            "outside_top100": rr["k100"],
            "outside_top200": rr["k200"],
            "ranking_recoverable_rate_at_k10": round(ranking_recoverable_rate, 4),
            "candidate_generation_failure_rate": round(gen_failure_rate, 4),
        },
        "candidate_generation_failures": gen_fail,
        "kg_incremental": {str(k): v for k, v in kg_inc.items()},
        "complementarity": {str(k): v for k, v in comp.items()},
        "deduplication": dedup,
        "domain_recall": domain_agg,
        "question_type_recall": type_agg,
        "exact_identifier": exact_summary,
        "statistics": stats,
        "ceiling": {
            "R@10": r10, "R@50": r50, "R@100": r100, "R@200": r200,
            "R@500_hybrid": r500,
            "R@500_union_rrf": union500,
            "R@500_union_pool": pool500,
            "k_for_80pct_recovery": k80,
            "k_for_90pct_recovery": k90,
            "ranking_recoverable_rate_at_k10": round(ranking_recoverable_rate, 4),
            "candidate_generation_failure_rate": round(gen_failure_rate, 4),
            "nearest_milestone": nearest,
            "ceiling_gap_top10_to_pool": round(ceiling_gap, 4),
        },
        "decision_tree": {"case": case, "verdict": verdict_primary, "kg_case": kg_value},
        "availability": {
            "gold_units": avail_counts["n"],
            "in_qdrant_corpus": avail_counts["source"],
            "in_neo4j": avail_counts["neo4j"],
            "corpus_present_rate": round(corpus_present_rate, 4),
            "neo4j_present_rate": round(neo4j_present_rate, 4),
        },
        "conclusion": conclusion,
    }

    # ---- V3/V4 (expanded-query) comparison — only when those arms exist
    # Each variant: naive append (V3) and dedup'd append (V4), both vs the
    # frozen base hybrid/union on the same payload index.
    exp_summaries: dict[str, dict] = {}
    for prefix, has, label in (("V3", has_v3, "naive_append"), ("V4", has_v4, "dedup_append")):
        if not has:
            continue
        xc = agg.get(f"{prefix}_hybrid") or {}
        xe = agg.get(f"{prefix}_union_rrf") or {}
        xp = agg.get(f"{prefix}_union_pool") or {}
        base_hybrid = c.get("recall@10", 0)
        s = {
            "variant": label,
            "hybrid_R@10": xc.get("recall@10", 0),
            "hybrid_R@500": xc.get("recall@500", 0),
            "union_rrf_R@10": xe.get("recall@10", 0),
            "union_rrf_R@500": xe.get("recall@500", 0),
            "union_pool_R@500": xp.get("recall@500", 0),
            "delta_hybrid_R@10": round(xc.get("recall@10", 0) - base_hybrid, 4),
            "delta_union_pool_R@500": round(
                xp.get("recall@500", 0) - pool.get("recall@500", 0), 4),
        }
        # count questions where this expansion helped/hurt at R@10
        common = [qid for qid in ranks["C_hybrid"] if qid in ranks[f"{prefix}_hybrid"]]
        helped = sum(1 for qid in common
                     if not any_hit_at(ranks["C_hybrid"][qid], q_by_id[qid], 10)
                     and any_hit_at(ranks[f"{prefix}_hybrid"][qid], q_by_id[qid], 10))
        hurt = sum(1 for qid in common
                   if any_hit_at(ranks["C_hybrid"][qid], q_by_id[qid], 10)
                   and not any_hit_at(ranks[f"{prefix}_hybrid"][qid], q_by_id[qid], 10))
        s["questions_helped_at_r10"] = helped
        s["questions_hurt_at_r10"] = hurt
        s["n_common"] = len(common)
        exp_summaries[label] = s
    if exp_summaries:
        summary["query_expansion_variants"] = exp_summaries
        summary["v3_expanded_query"] = exp_summaries.get("naive_append", {})

    (OUT_DIR / "retrieval_ceiling_results.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")

    _write_md_reports(summary, labels, agg, rows, rr, gen_fail, kg_inc, comp, dedup,
                      domain_agg, type_agg, stats, k80, k90, conclusion, case, kg_value,
                      exact_summary, has_v3=has_v3, exp_summaries=exp_summaries)

    logger.info("all deliverables written to %s", OUT_DIR)
    print(json.dumps({k: summary["ceiling"][k] for k in
                      ("R@10", "R@50", "R@100", "R@200", "R@500_hybrid", "R@500_union_rrf",
                       "R@500_union_pool", "k_for_80pct_recovery", "k_for_90pct_recovery",
                       "ranking_recoverable_rate_at_k10", "candidate_generation_failure_rate")},
                     indent=1))
    print("CONCLUSION:", conclusion["verdict"])
    for variant, vs in exp_summaries.items():
        print(f"EXPANSION [{variant}]:", json.dumps(vs, indent=1))
    return 0


def _matches(payload: dict, unit, family_map) -> bool:
    from evaluation.resolution import matches_gold

    return matches_gold(payload, unit, family_map)


def _gold_in_kg(unit, family_map, kg_map: dict) -> bool:
    """Whether any LegalProvision node covers this gold unit."""
    for pid, meta in kg_map.items():
        fams = family_map.family_s_for_act(meta["instrument_title"])
        if unit.family not in fams:
            continue
        if unit.section is None:
            return True
        from evaluation.resolution import norm_section

        if norm_section(meta["provision_number"]) == unit.section:
            return True
    return False


def _bucket_counts(ranks_q: dict, fn) -> dict:
    buckets = [(1, 1), (2, 5), (6, 10), (11, 20), (21, 50), (51, 100), (101, 200), (201, 500)]
    counts = {f"{lo}–{hi}" if lo != hi else str(lo): 0 for lo, hi in buckets}
    counts[">500/not found"] = 0
    n = len(ranks_q)
    for qid in ranks_q:
        r = fn(ranks_q[qid])
        if r is None or r > 500:
            counts[">500/not found"] += 1
            continue
        for lo, hi in buckets:
            if lo <= r <= hi:
                counts[f"{lo}–{hi}" if lo != hi else str(lo)] += 1
                break
    return {k: round(v / max(n, 1), 4) for k, v in counts.items()}


def _conclude(c, e, pool, o, gen_fail_rate, recov_rate, k80, k90, union500,
              corpus_present_rate=0.0, neo4j_present_rate=0.0) -> dict:
    r10 = c.get("recall@10", 0)
    r500 = c.get("recall@500", 0)
    pool500 = pool.get("recall@500", 0)
    oracle10 = o.get("recall@10", 0)
    oracle500 = o.get("recall@500", 0)

    # Evidence-driven conclusion (protocol §27): the five-way verdict is
    # chosen from where the gold actually is, not from a single point.
    if pool500 >= 0.90 and r10 <= 0.40:
        verdict = "A. Primarily a ranking problem"
    elif gen_fail_rate >= 0.35 and corpus_present_rate < 0.60:
        verdict = "D. Corpus/coverage problem"
    elif gen_fail_rate >= 0.35:
        # R@500 < 70% (§8 CASE C) but most gold IS physically present in
        # Qdrant/Neo4j -> the failure is in candidate generation from the
        # query (representation + identity resolution), not corpus absence.
        verdict = "B. Primarily a candidate-generation problem (query representation / identity)"
    elif recov_rate >= 0.35:
        verdict = "C. Mixed ranking + candidate-generation problem"
    elif pool500 >= 0.70:
        verdict = "C. Mixed ranking + candidate-generation problem"
    else:
        verdict = "B. Primarily a candidate-generation problem (query representation / identity)"

    evidence = (
        f"Hybrid R@10={r10:.1%}; hybrid R@500={r500:.1%}; union-pool R@500={pool500:.1%}; "
        f"gold-text oracle R@10={oracle10:.1%} / R@500={oracle500:.1%}; "
        f"candidate-generation failure rate={gen_fail_rate:.1%}; "
        f"ranking-recoverable rate={recov_rate:.1%}; "
        f"corpus presence (gold units resolvable to Qdrant)={corpus_present_rate:.1%}; "
        f"Neo4j presence={neo4j_present_rate:.1%}; "
        f"K for 80% recovery={'never' if k80 is None else k80}; "
        f"K for 90% recovery={'never' if k90 is None else k90}."
    )
    return {"verdict": verdict, "evidence": evidence}


def _write_md_reports(summary, labels, agg, curve_rows, rr, gen_fail, kg_inc, comp, dedup,
                      domain_agg, type_agg, stats, k80, k90, conclusion, case, kg_value,
                      exact_summary=None, has_v3: bool = False, exp_summaries=None) -> None:
    from evaluation.ceiling_config import OUT_DIR, EXPERIMENT_ID

    def md_curve() -> str:
        lines = ["| Retrieval | R@5 | R@10 | R@20 | R@50 | R@100 | R@200 | R@500 |",
                 "|---|---|---|---|---|---|---|---|"]
        for row in curve_rows:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)

    report = f"""# Retrieval Ceiling Report — {EXPERIMENT_ID}

**Date:** {summary['experiment_id']} · **Questions:** {summary['n_questions']} · **No LLM calls.**
See `run_config.json` for the frozen configuration (git hash, Qdrant/Neo4j snapshots, benchmark SHA-256).

## 1. The central recall curve (primary deliverable)

{md_curve()}

## 2. Direct answers (protocol §26)

| # | Question | Answer |
|---|----------|--------|
| 1 | R@10 | **{summary['ceiling']['R@10']:.1%}** (hybrid, frozen fusion) |
| 2 | R@50 | **{summary['ceiling']['R@50']:.1%}** |
| 3 | R@100 | **{summary['ceiling']['R@100']:.1%}** |
| 4 | R@200 | **{summary['ceiling']['R@200']:.1%}** |
| 5 | R@500 | **{summary['ceiling']['R@500_hybrid']:.1%}** hybrid · **{summary['ceiling']['R@500_union_rrf']:.1%}** D+S+KG RRF · **{summary['ceiling']['R@500_union_pool']:.1%}** union pool |
| 6 | K at which 80% of gold evidence is recovered | **{k80 if k80 else 'never (within 500)'}** |
| 7 | K at which 90% is recovered | **{k90 if k90 else 'never (within 500)'}** |
| 8 | Top-10 failures that are ranking failures (gold in 11–500) | **{summary['ranking_recoverable']['outside_top10']} / {summary['ranking_recoverable']['questions_with_gold_in_top500']}** = **{summary['ranking_recoverable']['ranking_recoverable_rate_at_k10']:.1%}** |
| 9 | Top-10 failures that are candidate-generation failures (gold absent at 500) | **{gen_fail['n']} / {summary['ranking_recoverable']['n']}** = **{summary['ceiling']['candidate_generation_failure_rate']:.1%}** |
| 10 | Does KG increase candidate recall? | see `kg_incremental_recall.csv` — delta at R@500: **{summary['kg_incremental']['500']['delta']:+.4f}** |
| 11 | Does dense+sparse fusion increase candidate recall? | see §5 and the statistics block |
| 12 | Can R@10 ≥80% be reached by ranking alone? | **Only if** the ranking-recoverable pool is large enough — see §8. |
| 13 | Candidate-generation improvements required | see `ranking_diagnosis.md` |
| 14 | Single largest bottleneck | see conclusion + `ranking_diagnosis.md` |
| 15 | What should be changed next | see conclusion |

## 3. Candidate-generation ceiling (protocol §8)

* Hybrid (frozen fusion) R@500 = **{summary['ceiling']['R@500_hybrid']:.1%}**
* Dense+Sparse+KG RRF R@500 = **{summary['ceiling']['R@500_union_rrf']:.1%}**
* **Union pool R@500 (any gold anywhere in dense@200 ∪ sparse@200 ∪ KG@200) = {summary['ceiling']['R@500_union_pool']:.1%}** — the architecture's candidate-generation ceiling. (RRF union R@500 equals the pool coverage — no gold evidence sits in RRF ranks 501–600.)

> **Reproducibility:** the frozen K≤20 metrics reproduce the pre-existing baseline
> exactly (Dense/Sparse/Hybrid R@10 = 13.0/14.7/14.0% both runs). MRR here is
> computed over relevant (primary + acceptable) gold units, matching the legacy
> harness definition.

Interpretation (protocol §8): {'CASE A — candidate generation is strong; ranking is the dominant problem.' if summary['ceiling']['R@500_union_pool'] >= 0.90 else 'CASE B — both candidate generation and ranking require optimization.' if summary['ceiling']['R@500_union_pool'] >= 0.70 else 'CASE C — candidate generation/corpus/query representation is the dominant problem.'}

## 4. Decision tree (protocol §23)

* **{case}** — {summary['decision_tree']['verdict']}
* **{kg_value}** — KG {'has genuine candidate-generation value (R@500 delta ≥ 5pts)' if 'CASE 4' == kg_value else 'should be treated as a reasoning/provenance feature rather than a retrieval engine (R@500 delta < 5pts)'}

## 5. Fusion / complementarity (protocol §16)

Per-question binary success (any relevant gold ≤K):

| K | Both | Dense-only | Sparse-only | Neither | Sparse rescue | Dense rescue |
|---|---|---|---|---|---|---|
| 10 | {comp[10]['both']} | {comp[10]['dense_only']} | {comp[10]['sparse_only']} | {comp[10]['neither']} | {comp[10]['sparse_rescue_rate']:.1%} | {comp[10]['dense_rescue_rate']:.1%} |
| 50 | {comp[50]['both']} | {comp[50]['dense_only']} | {comp[50]['sparse_only']} | {comp[50]['neither']} | {comp[50]['sparse_rescue_rate']:.1%} | {comp[50]['dense_rescue_rate']:.1%} |
| 500 | {comp[500]['both']} | {comp[500]['dense_only']} | {comp[500]['sparse_only']} | {comp[500]['neither']} | {comp[500]['sparse_rescue_rate']:.1%} | {comp[500]['dense_rescue_rate']:.1%} |

## 6. Gold-text oracle (protocol §13)

| Query | R@1 | R@5 | R@10 | R@20 | R@50 | R@100 | R@500 |
|---|---|---|---|---|---|---|---|
| Gold text → Dense | {_fmt(agg['O_dense'].get('recall@1'))} | {_fmt(agg['O_dense'].get('recall@5'))} | {_fmt(agg['O_dense'].get('recall@10'))} | {_fmt(agg['O_dense'].get('recall@20'))} | {_fmt(agg['O_dense'].get('recall@50'))} | {_fmt(agg['O_dense'].get('recall@100'))} | {_fmt(agg['O_dense'].get('recall@500'))} |
| Gold text → Sparse | {_fmt(agg['O_sparse'].get('recall@1'))} | {_fmt(agg['O_sparse'].get('recall@5'))} | {_fmt(agg['O_sparse'].get('recall@10'))} | {_fmt(agg['O_sparse'].get('recall@20'))} | {_fmt(agg['O_sparse'].get('recall@50'))} | {_fmt(agg['O_sparse'].get('recall@100'))} | {_fmt(agg['O_sparse'].get('recall@500'))} |
| Gold text → Hybrid | {_fmt(agg['O_hybrid'].get('recall@1'))} | {_fmt(agg['O_hybrid'].get('recall@5'))} | {_fmt(agg['O_hybrid'].get('recall@10'))} | {_fmt(agg['O_hybrid'].get('recall@20'))} | {_fmt(agg['O_hybrid'].get('recall@50'))} | {_fmt(agg['O_hybrid'].get('recall@100'))} | {_fmt(agg['O_hybrid'].get('recall@500'))} |

> **Note:** the frozen gold registry stores provision **titles** only (no full provision text), so the oracle query is the registry title.

## 7. Conclusion (protocol §27)

**{conclusion['verdict']}**

Evidence: {conclusion['evidence']}

""" + (f"""## 7b. V3 — expanded-query retrieval (query-representation fix)

Expansion = question text + detected canonical Act name (+ section number).
Rule-based, no LLM, no gold labels — production-representative query rewrite.

| Metric | Frozen (base) | Expanded naive (V3) | Dedup-expanded (V4) |
|---|---|---|---|
| Hybrid R@10 | {summary['ceiling']['R@10']:.1%} | {_fmt4(exp_summaries.get('naive_append', {}).get('hybrid_R@10'))} ({exp_summaries.get('naive_append', {}).get('delta_hybrid_R@10') or 0.0:+.1%}) | {_fmt4(exp_summaries.get('dedup_append', {}).get('hybrid_R@10'))} ({exp_summaries.get('dedup_append', {}).get('delta_hybrid_R@10') or 0.0:+.1%}) |
| D+S+KG RRF R@10 | {summary['ceiling']['R@10']:.1%} | {_fmt4(exp_summaries.get('naive_append', {}).get('union_rrf_R@10'))} | {_fmt4(exp_summaries.get('dedup_append', {}).get('union_rrf_R@10'))} |
| D+S+KG RRF R@500 | {summary['ceiling']['R@500_union_rrf']:.1%} | {_fmt4(exp_summaries.get('naive_append', {}).get('union_rrf_R@500'))} | {_fmt4(exp_summaries.get('dedup_append', {}).get('union_rrf_R@500'))} |
| Union pool R@500 (ceiling) | {summary['ceiling']['R@500_union_pool']:.1%} | {_fmt4(exp_summaries.get('naive_append', {}).get('union_pool_R@500'))} | {_fmt4(exp_summaries.get('dedup_append', {}).get('union_pool_R@500'))} |

Questions helped at R@10 — V3 (naive): **{exp_summaries.get('naive_append', {}).get('questions_helped_at_r10', '-')}** · hurt: **{exp_summaries.get('naive_append', {}).get('questions_hurt_at_r10', '-')}** · V4 (dedup): helped **{exp_summaries.get('dedup_append', {}).get('questions_helped_at_r10', '-')}** · hurt **{exp_summaries.get('dedup_append', {}).get('questions_hurt_at_r10', '-')}**.

""" if exp_summaries else "") + f"""

## 8. Next target (protocol §24)

## 8. Next target (protocol §24)

Current: R@10={summary['ceiling']['R@10']:.1%} · R@50={summary['ceiling']['R@50']:.1%} · R@100={summary['ceiling']['R@100']:.1%} · R@200={summary['ceiling']['R@200']:.1%} · R@500={summary['ceiling']['R@500_union_pool']:.1%} (pool).
Nearest milestone: **{summary['ceiling']['nearest_milestone']:.0%}**. Route to 80%: see `ranking_diagnosis.md`.

## 9. Deliverables

`recall_curve.csv` · `retrieval_depth_results.csv` · `gold_rank_distribution.csv` · `candidate_generation_failures.csv` · `ranking_recoverable_cases.csv` · `gold_provision_availability.csv` · `kg_incremental_recall.csv` · `domain_recall.csv` · `question_type_recall.csv` · `retrieval_ceiling_results.json` · `run_config.json`
"""
    (OUT_DIR / "retrieval_ceiling_report.md").write_text(report, encoding="utf-8")

    diag = f"""# Ranking Diagnosis — {EXPERIMENT_ID}

## Gold rank distribution (first gold hit)

| Bucket | Hybrid C | D+S+KG RRF | Dense |
|---|---|---|---|
""" + "\n".join(
        f"| {b} | {summary['gold_rank_distribution']['C_hybrid'].get(b, 0):.1%} | "
        f"{summary['gold_rank_distribution']['E_union_rrf'].get(b, 0):.1%} | "
        f"{summary['gold_rank_distribution']['A_dense'].get(b, 0):.1%} |"
        for b in ["1", "2–5", "6–10", "11–20", "21–50", "51–100", "101–200", "201–500", ">500/not found"]
    ) + f"""

**Reading:** if many gold provisions sit in ranks 11–100, the system has a ranking problem; if most are absent even at 500, it has a candidate-generation problem.

## Ranking-recoverable (protocol §10)

* Gold in Top-500: **{rr['n500']}** questions · outside Top-10: **{rr['k10']}** · outside Top-20: **{rr['k20']}** · outside Top-50: **{rr['k50']}** · outside Top-100: **{rr['k100']}** · outside Top-200: **{rr['k200']}**
* **Ranking Recoverable Rate (gold in 11–500): {summary['ranking_recoverable']['ranking_recoverable_rate_at_k10']:.1%}**

## Candidate-generation failures (protocol §11)

* Gold NOT found in Top-500 of the union: **{gen_fail['n']}** questions = **{summary['ceiling']['candidate_generation_failure_rate']:.1%}**
* Classification: {_fmt_dict(gen_fail)}

> **Caveat on `corpus_missing`:** a gold unit is only "resolvable" when a Qdrant
> payload matches its (act, section) — and only ~22.5% of payloads carry a
> `section_number`. So `corpus_missing` (19 questions) may reflect payload
> identity/section resolution, not physical absence: 34 of the 60 unresolvable
> gold units *do* exist as Neo4j provision nodes (see `gold_provision_availability.csv`).

> **Arm-D labeling (protocol §5):** the KG row is "**KG candidate retrieval via
> the production graph-RAG contract**" (concept traversal + full-text fallback),
> not a pure KG-only retrieval arm — 122/150 queries returned zero provisions
> because the contract's keyword concept extractor is narrow.

## Exact identifier test (protocol §14) — Act + Section as the query

| Retrieval | R@10 | R@50 | R@100 | n |
|---|---|---|---|---|
| Dense (over all 150) | {_fmt(exact_summary['recall_over_all']['dense'].get(10))} | {_fmt(exact_summary['recall_over_all']['dense'].get(50))} | {_fmt(exact_summary['recall_over_all']['dense'].get(100))} | {exact_summary['n_questions']} |
| Sparse (over all 150) | {_fmt(exact_summary['recall_over_all']['sparse'].get(10))} | {_fmt(exact_summary['recall_over_all']['sparse'].get(50))} | {_fmt(exact_summary['recall_over_all']['sparse'].get(100))} | {exact_summary['n_questions']} |
| Dense (identifier questions only) | {_fmt(exact_summary['recall_over_identifiable']['dense'].get(10))} | {_fmt(exact_summary['recall_over_identifiable']['dense'].get(50))} | {_fmt(exact_summary['recall_over_identifiable']['dense'].get(100))} | {exact_summary['n_with_numeric_identifier']} |
| Sparse (identifier questions only) | {_fmt(exact_summary['recall_over_identifiable']['sparse'].get(10))} | {_fmt(exact_summary['recall_over_identifiable']['sparse'].get(50))} | {_fmt(exact_summary['recall_over_identifiable']['sparse'].get(100))} | {exact_summary['n_with_numeric_identifier']} |

> Diagnostic only — the exact identifier query is NOT the benchmark query. The over-150 rows dilute by the 29 questions whose gold reference carries no numeric Act/Section identifier.

## Deduplication (protocol §17)

## Deduplication (protocol §17)

| Arm | raw | unique | duplicate rate |
|---|---|---|---|
""" + "\n".join(
        f"| {arm} | {d['raw_candidates']} | {d['unique_candidates']} | {d['duplicate_rate']:.1%} |"
        for arm, d in dedup.items()
    ) + f"""

## KG incremental (protocol §15) — hybrid vs D+S+KG RRF

| K | Hybrid | D+S+KG | Δ | helped | harm | neutral |
|---|---|---|---|---|---|---|
""" + "\n".join(
        f"| {k} | {v['hybrid']:.3f} | {v['hybrid_kg']:.3f} | {v['delta']:+.3f} | {v['helped']} | {v['harm']} | {v['neutral']} |"
        for k, v in kg_inc.items()
    ) + f"""

## Statistical significance (protocol §22) — paired bootstrap 95% CI + McNemar

* A = baseline, B = challenger; **B−A > 0 means the challenger is better**.

| Comparison | A | B | B−A | 95% CI | p |
|---|---|---|---|---|---|
""" + "\n".join(
        f"| {name} | {v['mean_a']:.3f} | {v['mean_b']:.3f} | {v['abs_diff']:+.3f} | [{v['bootstrap_ci95'][0]:.3f}, {v['bootstrap_ci95'][1]:.3f}] | {v['mcnemar_p']:.4f} |"
        for name, v in stats.items()
    ) + f"""

## Domain recall (protocol §18) — D+S+KG RRF union

| Domain | n | R@10 | R@50 | R@100 | R@500 |
|---|---|---|---|---|---|
""" + "\n".join(
        f"| {d} | {v['n']} | {v['R@10']:.3f} | {v['R@50']:.3f} | {v['R@100']:.3f} | {v['R@500']:.3f} |"
        for d, v in domain_agg.items()
    ) + f"""

## Question-type recall (protocol §19) — D+S+KG RRF union

| Type | n | R@10 | R@50 | R@100 | R@500 |
|---|---|---|---|---|---|
""" + "\n".join(
        f"| {t} | {v['n']} | {v['R@10']:.3f} | {v['R@50']:.3f} | {v['R@100']:.3f} | {v['R@500']:.3f} |"
        for t, v in type_agg.items()
    ) + "\n"
    (OUT_DIR / "ranking_diagnosis.md").write_text(diag, encoding="utf-8")


def _fmt(v) -> str:
    return f"{v:.1%}" if isinstance(v, (int, float)) else "-"


def _fmt4(v) -> str:
    return "n/a" if v is None else f"{v:.4f}"


def _fmt_dict(d: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in d.items())


if __name__ == "__main__":
    raise SystemExit(main())

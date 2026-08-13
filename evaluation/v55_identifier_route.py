"""RANKING_CEILING_V5.5 — production-realistic identifier route.

V5's ablation measured the identifier route with GOLD-derived queries
(route_queries reads the gold registry) — +13.3pp pool ceiling (0.705 -> 0.838)
is therefore an ORACLE bound.  This experiment measures the production-realistic
version: identifiers detected from the QUESTION TEXT ALONE (evaluation.query_expansion
detect_act / detect_section — the same detector the V3/V4 expanded arms use),
run as a PARALLEL sparse arm and unioned with the base dense+sparse+KG pool.

Protocol:
  P0  base union pool          (A_dense + B_sparse + D_kg @500)  — frozen cache
  P1  base ∪ question-id-route (sparse "{act} section {n}" | "{act}" @500)
  P2  P1 pool + sec_act rerank (R@10 on the question-derived expanded pool)

Outputs (ceiling_v5/):
  v55_identifier_route.csv   — per-question identifier detection + query + recovered units
  v55_pool_ceiling.json      — P0 vs P1 pool R@K, workset recovery, oracle gap
  v55_rerank.json            — P2 rerank R@10/20/50

No LLM.  No production code modified.  No benchmark changes.  The base arms are
read from the frozen V5 raw cache; only the new identifier arm hits live Qdrant
sparse (cached to evaluation/out/cache/v55_ident/).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

CACHE = PROJECT_ROOT / "evaluation" / "out" / "cache" / "v55_ident"
CACHE.mkdir(parents=True, exist_ok=True)
OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
OUT.mkdir(parents=True, exist_ok=True)


def identifier_query(question: str) -> tuple[str | None, dict]:
    """Question-text-only identifier query (production-realistic, no gold).

    Returns (query, meta).  ``meta`` records what was detected so coverage can
    be reported.  Order: act+section when both known; act alone when only the
    act is mentioned (the dominant production case — see the probe: 142/150
    questions mention an act, only 22/150 a section number).
    """
    from evaluation.query_expansion import detect_act, detect_section

    act = detect_act(question)
    section, subsection = detect_section(question)
    meta = {"act": act, "section": section, "subsection": subsection}
    if act and section:
        parts = [act, f"section {section}"]
        if subsection:
            parts.append(f"subsection {subsection}")
        return " ".join(parts), {**meta, "form": "act+section"}
    if act:
        return act, {**meta, "form": "act"}
    if section:
        return f"section {section}", {**meta, "form": "section"}
    return None, {**meta, "form": "none"}


def run_identifier_arm() -> int:
    """Run sparse @500 for each question-derived identifier query (cached)."""
    from app import create_app
    from evaluation.benchmark import load_questions
    from evaluation.v5_routes import _retrieve

    app = create_app()
    with app.app_context():
        questions = load_questions()
        cache: dict[str, dict] = {}
        p = CACHE / "sparse_identifier.jsonl"
        if p.exists():
            for line in p.open(encoding="utf-8"):
                line = line.strip()
                if line:
                    r = json.loads(line)
                    cache[r["question_id"]] = r
        n_new = 0
        with p.open("a", encoding="utf-8") as f:
            for q in questions:
                if q.question_id in cache:
                    continue
                query, meta = identifier_query(q.question)
                collection = q.collections[0] if q.collections else None
                rec = {"question_id": q.question_id, "query": query, "meta": meta,
                       "collection": collection, "chunk_ids": [], "error": None}
                if query and collection:
                    try:
                        res = _retrieve(collection, query, "sparse", top_k=500)
                        rec["chunk_ids"] = res["chunk_ids"]
                    except Exception as exc:  # noqa: BLE001 - per-question isolation
                        rec["error"] = f"{type(exc).__name__}: {exc}"
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_new += 1
        print(f"identifier arm: {len(cache) + n_new} questions cached ({n_new} new)")
    return 0


def analyze() -> int:
    from app import create_app
    from evaluation.benchmark import load_gold_registry, load_questions
    from evaluation.resolution import FamilyMap
    from evaluation.report_ceiling import load_payload_index, load_raw, build_union_arms, \
        unit_first_ranks, metrics_from_ranks
    from evaluation.ceiling_config import DEPTHS

    app = create_app()
    with app.app_context():
        payload_index = load_payload_index()
        family_map = FamilyMap()
        questions = {q.question_id: q for q in load_questions()}
        registry = load_gold_registry()

        dense = load_raw("A_dense")
        sparse = load_raw("B_sparse")
        kg = load_raw("D_kg")

        ident: dict[str, dict] = {}
        p = CACHE / "sparse_identifier.jsonl"
        if p.exists():
            for line in p.open(encoding="utf-8"):
                line = line.strip()
                if line:
                    r = json.loads(line)
                    ident[r["question_id"]] = r

        # ---- P0 (base pool) vs P1 (base ∪ identifier route) ----------------
        # Pool-coverage convention: a gold unit is covered iff ANY chunk in the
        # pool payload-matches it (matches_gold) — identical to the Task-5
        # regression / _v5_verify_fixed_pool, so the P0 number reproduces 0.705.
        from evaluation.resolution import matches_gold

        def pool_cover(pool_ids: set[str], q) -> dict:
            covered = {u.provision_id for u in q.gold_units
                       if any(matches_gold(payload_index.get(c) or {}, u, family_map)
                              for c in pool_ids if c in payload_index)}
            rel = [u.provision_id for u in q.relevant_units()]
            out = {}
            for kk in DEPTHS:
                out[f"recall@{kk}"] = sum(1 for pid in rel if pid in covered) / max(len(rel), 1)
            return out

        p0_r500 = p1_r500 = 0.0
        p1_recall = {k: 0.0 for k in DEPTHS}
        n = 0
        workset_rows = []          # per workset unit: recovered by P1?
        recovered_p1 = set()
        for qid, q in questions.items():
            a, b, k = dense.get(qid), sparse.get(qid), kg.get(qid)
            if not (a and b and k):
                continue
            union0 = build_union_arms(a, b, k, payload_index, family_map, 500, 500, 500)
            pool0_ids = {str(i.get("key")) for i in union0["E_union_pool"].get("fused_items", [])
                         if i.get("kind") == "chunk"}
            r0 = pool_cover(pool0_ids, q)

            # P1 pool = base union + question-derived identifier chunks (raw union)
            rec = ident.get(qid, {})
            pool1_ids = set(pool0_ids) | {str(c) for c in rec.get("chunk_ids", [])}
            r1 = pool_cover(pool1_ids, q)
            p0_r500 += r0.get("recall@500", 0)
            p1_r500 += r1.get("recall@500", 0)
            for kk in DEPTHS:
                p1_recall[kk] += r1.get(f"recall@{kk}", 0)
            n += 1

            # workset recovery: corpus-present, in P1 but not P0
            for u in q.gold_units:
                if not any(matches_gold(pl, u, family_map) for pl in payload_index.values()):
                    continue
                in0 = any(matches_gold(payload_index.get(c) or {}, u, family_map)
                          for c in pool0_ids if c in payload_index)
                in1 = any(matches_gold(payload_index.get(c) or {}, u, family_map)
                          for c in pool1_ids if c in payload_index)
                if not in0 and in1:
                    recovered_p1.add(u.provision_id)
                    workset_rows.append((qid, u.provision_id, u.family,
                                         rec.get("meta", {}).get("form", "?"),
                                         rec.get("query", "")))

        from evaluation.v5_routes import _workset
        ws = _workset(list(questions.values()), registry)
        ws_pids = {pid for pid, _, _, _ in ws}
        ident_meta = {qid: (ident.get(qid, {}).get("meta") or {}) for qid in questions}
        ws_act = sum(1 for pid, q, u, rec in ws
                     if ident_meta.get(q.question_id, {}).get("act"))
        ws_sec = sum(1 for pid, q, u, rec in ws
                     if ident_meta.get(q.question_id, {}).get("section"))

        out = {
            "n_questions": n,
            "pool_R500_base": round(p0_r500 / max(n, 1), 4),
            "pool_R500_base_plus_ident": round(p1_r500 / max(n, 1), 4),
            "pool_R10_base_plus_ident": round(p1_recall.get(10, 0) / max(n, 1), 4),
            "pool_R50_base_plus_ident": round(p1_recall.get(50, 0) / max(n, 1), 4),
            "delta_pool_R500": round((p1_r500 - p0_r500) / max(n, 1), 4),
            "workset_total": len(ws_pids),
            "workset_act_in_question": ws_act,
            "workset_section_in_question": ws_sec,
            "workset_recovered_by_question_ident_route": len(recovered_p1),
            "workset_recovered_pids": sorted(recovered_p1),
            "oracle_workset_recovery": 28,  # V5 ablation: gold identifier recovers 28/28
            "oracle_pool_ceiling": 0.8378,  # V5 ablation step 2 (gold-derived)
        }
        (OUT / "v55_pool_ceiling.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps(out, indent=1))

        with (OUT / "v55_identifier_route.csv").open("w", encoding="utf-8", newline="") as f:
            import csv
            w = csv.writer(f)
            w.writerow(["question_id", "gold_unit", "family", "ident_form", "ident_query"])
            for row in workset_rows:
                w.writerow(row)
        print(f"\nwrote v55_pool_ceiling.json + v55_identifier_route.csv "
              f"({len(workset_rows)} workset rows recovered by P1)")
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="run the identifier arm (live sparse)")
    parser.add_argument("--analyze", action="store_true", help="compute P0 vs P1 pool ceilings")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")

    if args.run:
        return run_identifier_arm()
    if args.analyze:
        return analyze()
    print("use --run and/or --analyze")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

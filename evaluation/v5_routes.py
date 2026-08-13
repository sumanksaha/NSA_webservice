"""RANKING_CEILING_V5 — Tasks 7–12, 14: diagnostic retrieval routes.

For each retrieval-missing gold unit we test which query *representation*
recovers it.  Routes (protocol Task 7):

    A  original natural-language question          (cached: A_dense/B_sparse)
    B  gold provision text (registry title)        (cached: O_dense/O_sparse)
    C  exact section/rule/order identifier          (sparse)
    D  act + identifier                             (= C; documented alias)
    E  document title only                          (sparse)
    F  identifier only                              (sparse)
    G  normalized legal concept terms               (dense + sparse)
    H  authority + legal action                     (sparse)
    I  provision type                               (dense + sparse)
    J  parent provision / neighbour hierarchy       (sparse)
    K  KG-derived (graph-RAG contract)              (cached: D_kg@500)

Usage:
    python -m evaluation.v5_routes --run --scope q   --shard 1/8 --routes C_identifier,E_document,...
    python -m evaluation.v5_routes --run --scope unit --shard 1/2 --routes ...
    python -m evaluation.v5_routes --analyze
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

_STOP = frozenset({
    "the", "a", "an", "of", "and", "or", "to", "in", "for", "under", "with",
    "from", "as", "that", "this", "its", "it", "not", "by", "on", "at", "is",
    "are", "be", "shall", "may", "act", "section", "order", "rule", "regulation",
    "food", "safety", "standards", "act,", "1966", "1980", "2013", "2006", "1986",
    "1974", "1930", "1872", "2019", "1997", "2016", "2022", "2017",
})

_ACTION_TERMS = ["prohibit", "license", "penalise", "punish", "regulate", "require",
                 "authorize", "empower", "impose", "compensate", "define", "extend",
                 "exempt", "provide", "sanction", "restrict", "ban", "control"]
_TYPE_TERMS = ["penalty", "prohibition", "power", "duty", "offence", "offenses",
               "procedure", "licensing", "license", "inspection", "sampling",
               "notice", "registration", "liability", "compensation", "appeal"]

_ROUTE_RETRIEVER = {
    "C_identifier": "sparse", "E_document": "sparse", "F_identifier_only": "sparse",
    "G_concept": "both", "H_authority_action": "sparse", "I_provision_type": "both",
    "J_parent": "sparse",
}


def parse_identifier(sec_raw: str) -> tuple[str | None, str | None]:
    """('section','31') for '31(2)'; ('order','4') for 'Order 4'; ('regulation',None) for 'Reg'."""
    s = str(sec_raw or "").strip()
    if not s or s.upper() in ("N/A", "NA"):
        return None, None
    if s.upper() == "REG":
        return "regulation", None
    m = re.search(r"(\d{1,3})", s)
    if m:
        if re.search(r"order", s, re.IGNORECASE):
            return "order", m.group(1)
        if re.search(r"rule", s, re.IGNORECASE):
            return "rule", m.group(1)
        if re.search(r"reg", s, re.IGNORECASE):
            return "regulation", m.group(1)
        return "section", m.group(1)
    if re.search(r"order", s, re.IGNORECASE):
        return "order", None
    if re.search(r"reg", s, re.IGNORECASE):
        return "regulation", None
    return "section", None


def concept_terms(title: str) -> str:
    toks = [t for t in re.findall(r"[a-z0-9]+", title.lower()) if t not in _STOP]
    return " ".join(toks) or title


def action_verb(title: str) -> str:
    for w in _ACTION_TERMS:
        if re.search(rf"\b{w}", title, re.IGNORECASE):
            return w
    return ""


def provision_type(title: str, sec_raw: str) -> str:
    blob = f"{title} {sec_raw}".lower()
    for w in _TYPE_TERMS:
        if re.search(rf"\b{w}", blob):
            return w
    return "provision"


def route_queries(unit, rec: dict, question: str) -> dict[str, str]:
    """Build every route query for one gold unit (diagnostic — gold allowed)."""
    act = rec.get("act") or unit.act or ""
    sec_raw = str(rec.get("section") or "")
    title = str(rec.get("title") or "")
    marker, num = parse_identifier(sec_raw)
    q: dict[str, str] = {}
    q["A_original"] = question
    q["B_gold_text"] = title
    if num:
        q["C_identifier"] = f"{act} {marker} {num}"
        q["D_act_id"] = q["C_identifier"]
        q["F_identifier_only"] = f"{marker} {num}"
        if num.isdigit():
            n = int(num)
            q["J_parent"] = f"{act} {marker} {n-1} {marker} {n} {marker} {n+1}"
        else:
            q["J_parent"] = title
    else:
        q["C_identifier"] = f"{act} {title}" if title else act
        q["D_act_id"] = q["C_identifier"]
        q["F_identifier_only"] = title
        q["J_parent"] = title
    q["E_document"] = act
    q["G_concept"] = concept_terms(title)
    verb = action_verb(title)
    q["H_authority_action"] = f"{verb} {act}" if verb else title
    q["I_provision_type"] = f"{provision_type(title, sec_raw)} {act}"
    return q


CACHE = PROJECT_ROOT / "evaluation" / "out" / "cache" / "v5_routes"
CACHE.mkdir(parents=True, exist_ok=True)
OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
OUT.mkdir(parents=True, exist_ok=True)


def _load_cache(scope: str, route: str) -> dict[str, dict]:
    p = CACHE / f"{scope}_{route}.jsonl"
    if not p.exists():
        return {}
    out = {}
    for line in p.open(encoding="utf-8"):
        line = line.strip()
        if line:
            r = json.loads(line)
            out[r["key"]] = r
    return out


def _retrieve(collection: str, query: str, retriever: str, top_k: int = 500):
    from evaluation.arms import _dense, _sparse

    if retriever in ("sparse", "both"):
        res = _sparse(collection).retrieve(query, top_k=top_k, threshold=0.0)
        chunks = [c.chunk_id for c in res.chunks]
        if retriever == "sparse":
            return {"chunk_ids": chunks, "retriever": "sparse"}
    else:
        chunks = []
    res = _dense(collection).search(query, top_k=top_k)
    dense_ids = [c.chunk_id for c in res.chunks]
    if retriever == "both":
        merged, seen = [], set()
        for cid in chunks + dense_ids:
            if cid not in seen:
                seen.add(cid)
                merged.append(cid)
        return {"chunk_ids": merged, "retriever": "both"}
    return {"chunk_ids": dense_ids, "retriever": "dense"}


def _workset(questions, registry) -> list[tuple[str, object, object, dict]]:
    """The retrieval-missing workset: corpus-present (corrected resolution),
    absent from the 500-depth union pool.  **Unique per provision_id** — the
    same gold unit may be relevant to several questions; it is counted once,
    with the first question where it is retrieval-missing.

    Unit set: **all gold units** (primary + acceptable + supporting) —
    matching the Task 6 protocol definition ("gold unit ... not retrieved")
    and the ``v5_retrieval_missing.csv`` / ``retrieval_missing_71.csv``
    workset produced by ``v5_audit.py`` / ``_v5_workset_ranks.py``.  Earlier
    V5 iterations restricted the route analysis to relevant (primary +
    acceptable) units; the count differed from the CSV.  V5 final:
    all gold units, so the route table and the workset CSV always agree.
    """
    from evaluation.resolution import FamilyMap, matches_gold
    from evaluation.report_ceiling import load_payload_index, load_raw, build_union_arms, unit_first_ranks

    payload_index = load_payload_index()
    family_map = FamilyMap()
    dense = load_raw("A_dense")
    sparse = load_raw("B_sparse")
    kg = load_raw("D_kg")
    out: list[tuple[str, object, object, dict]] = []
    seen: set[str] = set()
    for q in questions:
        a, b, k = dense.get(q.question_id), sparse.get(q.question_id), kg.get(q.question_id)
        pool_member = {}
        if a and b and k:
            union = build_union_arms(a, b, k, payload_index, family_map, 500, 500, 500)
            ranks = unit_first_ranks(union["E_union_pool"], q, payload_index, family_map)
            pool_member = {pid: r is not None for pid, r in ranks.items()}
        for u in q.gold_units:
            if u.provision_id in seen:
                continue
            present = any(matches_gold(pl, u, family_map) for pl in payload_index.values())
            if present and not pool_member.get(u.provision_id, False):
                seen.add(u.provision_id)
                out.append((u.provision_id, q, u, registry.get(u.provision_id, {})))
    return out


def run(scope: str, routes: list[str], shard: str, limit: int = 0) -> int:
    from app import create_app
    from evaluation.benchmark import load_gold_registry, load_questions

    app = create_app()
    with app.app_context():
        registry = load_gold_registry()
        questions = load_questions()
        if scope == "q":
            items = []
            for q in questions:
                units = q.primary_units() or q.relevant_units()
                unit = units[0] if units else None
                rec = registry.get(unit.provision_id, {}) if unit else {}
                items.append((q.question_id, q, unit, rec))
        else:
            items = _workset(questions, registry)
        idx, nshards = (int(x) for x in shard.split("/"))
        items = items[idx - 1::nshards]
        if limit:
            items = items[:limit]
        for i, (key, q, unit, rec) in enumerate(items, 1):
            if unit is None or not rec:
                continue
            queries = route_queries(unit, rec, q.question)
            collection = rec.get("collection") or (q.collections[0] if q.collections else None)
            if not collection:
                continue
            for route in routes:
                if route not in _ROUTE_RETRIEVER:
                    continue
                cache = _load_cache(scope, route)
                if key in cache:
                    continue
                query = queries.get(route)
                if not query:
                    continue
                try:
                    rec_result = _retrieve(collection, query, _ROUTE_RETRIEVER[route])
                except Exception as exc:  # noqa: BLE001 - per-item isolation
                    rec_result = {"chunk_ids": [], "retriever": "error", "error": str(exc)}
                rec_result.update({"key": key, "route": route, "scope": scope,
                                   "query": query, "collection": collection})
                with open(CACHE / f"{scope}_{route}.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec_result, ensure_ascii=False) + "\n")
        print(f"scope={scope} routes={routes} shard={shard} done")
    return 0


# --------------------------------------------------------------------------- #
# Analysis (Tasks 8–12, 14)
# --------------------------------------------------------------------------- #
def _unit_hit(rec_ids: list[str], unit, payload_index, family_map) -> bool:
    from evaluation.resolution import matches_gold

    return any(matches_gold(payload_index.get(cid) or {}, unit, family_map) for cid in rec_ids)


def _kg_hit(kg_rec, unit, family_map) -> bool:
    from evaluation.resolution import norm_section

    if not kg_rec:
        return False
    for p in kg_rec.get("kg_provisions", []):
        fams = family_map.family_s_for_act(str(p.get("instrument_title") or ""))
        if unit.family not in fams:
            continue
        if unit.section is None:
            return True
        if norm_section(p.get("provision_number")) == unit.section:
            return True
    return False


def analyze() -> int:
    from app import create_app
    from evaluation.benchmark import load_gold_registry, load_questions
    from evaluation.resolution import FamilyMap
    from evaluation.report_ceiling import load_payload_index, load_raw

    app = create_app()
    with app.app_context():
        registry = load_gold_registry()
        questions = {q.question_id: q for q in load_questions()}
        payload_index = load_payload_index()
        family_map = FamilyMap()
        workset = _workset(list(questions.values()), registry)
        print(f"workset: {len(workset)} units")

        a_dense, b_sparse = load_raw("A_dense"), load_raw("B_sparse")
        o_dense, o_sparse = load_raw("O_dense"), load_raw("O_sparse")
        kg_cache = load_raw("D_kg")

        # ---- Task 8: per-route R@K on the workset
        routes = ["A_original", "B_gold_text", "C_identifier", "E_document",
                  "F_identifier_only", "G_concept", "H_authority_action",
                  "I_provision_type", "J_parent", "K_kg"]
        q_caches = {r: _load_cache("q", r) for r in _ROUTE_RETRIEVER}
        u_caches = {r: _load_cache("unit", r) for r in _ROUTE_RETRIEVER}

        per_route, recoveries = {}, {}
        for route in routes:
            hits = {5: 0, 10: 0, 20: 0, 50: 0, 100: 0, 200: 0, 500: 0}
            recovered = []
            for pid, q, unit, rec in workset:
                rec_ids = []
                if route == "A_original":
                    rec_ids = list(a_dense.get(q.question_id, {}).get("chunk_ids", [])) + \
                              list(b_sparse.get(q.question_id, {}).get("chunk_ids", []))
                elif route == "B_gold_text":
                    rec_ids = list(o_dense.get(q.question_id, {}).get("chunk_ids", [])) + \
                              list(o_sparse.get(q.question_id, {}).get("chunk_ids", []))
                elif route == "K_kg":
                    pass
                else:
                    rec_ids = list(u_caches.get(route, {}).get(pid, {}).get("chunk_ids", [])) or \
                              list(q_caches.get(route, {}).get(q.question_id, {}).get("chunk_ids", []))
                for k in hits:
                    if route == "K_kg":
                        if _kg_hit(kg_cache.get(q.question_id), unit, family_map):
                            hits[k] += 1
                    elif _unit_hit(rec_ids[:k], unit, payload_index, family_map):
                        hits[k] += 1
                if route == "K_kg":
                    if _kg_hit(kg_cache.get(q.question_id), unit, family_map):
                        recovered.append(pid)
                elif _unit_hit(rec_ids[:500], unit, payload_index, family_map):
                    recovered.append(pid)
            per_route[route] = {f"R@{k}": round(v / max(len(workset), 1), 4) for k, v in hits.items()}
            recoveries[route] = recovered
            print(f"{route:22s} " + " ".join(f"R{k}={per_route[route][f'R@{k}']:.3f}" for k in (10, 50, 100, 500))
                  + f"  recovered={len(recovered)}")

        with open(OUT / "v5_route_results.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Route", "R@5", "R@10", "R@20", "R@50", "R@100", "R@200", "R@500", "recovered_units"])
            for route in routes:
                r = per_route[route]
                w.writerow([route] + [f"{r[f'R@{k}']:.3f}" for k in (5, 10, 20, 50, 100, 200, 500)] +
                           [len(recoveries.get(route, []))])

        # ---- Task 9: failure taxonomy per workset unit (route evidence)
        best_route = {}
        for pid, q, unit, rec in workset:
            found = None
            for route in routes:
                if route == "K_kg":
                    if _kg_hit(kg_cache.get(q.question_id), unit, family_map):
                        found = "K_kg"
                else:
                    rec_ids = _route_ids(route, pid, q, a_dense, b_sparse, o_dense, o_sparse, q_caches, u_caches)
                    if _unit_hit(rec_ids[:500], unit, payload_index, family_map):
                        found = route
                if found:
                    break
            best_route[pid] = found

        tax_rows = [["gold_unit", "family", "question_id", "recovered_by_route", "failure_class"]]
        tax_counts = {}
        for pid, q, unit, rec in workset:
            r = best_route.get(pid)
            if r == "A_original":
                cls = "already_retrieved"
            elif r:
                cls = f"query_representation ({r})"
            else:
                sec = str(rec.get("section") or "")
                marker, num = parse_identifier(sec)
                if marker == "order" or "order" in str(rec.get("id", "")).lower():
                    cls = "order-clause granularity"
                elif num and not _unit_hit(_route_ids("E_document", pid, q, a_dense, b_sparse, o_dense, o_sparse, q_caches, u_caches), unit, payload_index, family_map):
                    cls = "missing section metadata / chunk fragmentation"
                elif num:
                    cls = "sparse lexical mismatch / embedding failure"
                else:
                    cls = "instrument-level granularity"
            tax_counts[cls] = tax_counts.get(cls, 0) + 1
            tax_rows.append([pid, unit.family, q.question_id, r or "none", cls])
        with open(OUT / "v5_taxonomy.csv", "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows(tax_rows)
        print("\n=== Task 9: taxonomy ===")
        for cls, n in sorted(tax_counts.items(), key=lambda x: -x[1]):
            print(f"  {cls}: {n}")

        # ---- Task 11: multi-route ablation on the FULL benchmark (pool ceiling)
        ablation_order = ["A_original", "C_identifier", "G_concept", "H_authority_action",
                          "I_provision_type", "E_document", "F_identifier_only", "J_parent", "K_kg"]
        pool = {r: set() for r in ablation_order}
        for qid, q in questions.items():
            for route in ablation_order:
                ids = set(_route_ids(route, None, q, a_dense, b_sparse, o_dense, o_sparse, q_caches, u_caches)) \
                    if route != "K_kg" else set()
                pool[route] |= ids
        # per-question incremental union coverage
        step_rows = [["step", "routes", "pool_R500_ceiling", "questions_with_gold_in_pool"]]
        acc_routes = []
        from evaluation.report_ceiling import build_union_arms, unit_first_ranks, metrics_from_ranks
        from evaluation.ceiling_config import DEPTHS
        for route in ablation_order:
            acc_routes.append(route)
            r500 = 0.0
            qcov = 0
            n = 0
            for qid, q in questions.items():
                rec_ids = []
                for r in acc_routes:
                    if r == "K_kg":
                        continue
                    rec_ids += _route_ids(r, None, q, a_dense, b_sparse, o_dense, o_sparse, q_caches, u_caches)
                rec_ids = list(dict.fromkeys(rec_ids))
                ranks = {}
                for u in q.relevant_units():
                    ranks[u.provision_id] = 1 if _unit_hit(rec_ids, u, payload_index, family_map) else None
                from evaluation.report_ceiling import metrics_from_ranks as mfr
                m = mfr(ranks, q, DEPTHS)
                r500 += m.get("recall@500", 0)
                qcov += int(any(v == 1 for v in ranks.values()))
                n += 1
            step_rows.append([len(acc_routes), "+".join(acc_routes),
                              round(r500 / max(n, 1), 4), round(qcov / max(n, 1), 4)])
            print(f"  step {len(acc_routes):2d} {acc_routes[-1]:20s} pool_R500={step_rows[-1][2]:.4f} qcov={step_rows[-1][3]:.4f}")
        with open(OUT / "v5_route_ablation.csv", "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows(step_rows)

        # ---- Task 12: domain analysis of the workset + V5 domain recall
        from collections import Counter
        dom = Counter(q.domains[0] if q.domains else "?" for _, q, _, _ in workset)
        print("\n=== Task 12: workset by domain ===")
        for d, n in dom.most_common():
            print(f"  {d}: {n}")

        # ---- Task 14: cross-encoder readiness
        ws_pids = {pid for pid, _, _, _ in workset}
        recovered_any = {p for r in routes for p in recoveries.get(r, [])} & ws_pids
        recovered_orig = set(recoveries.get("A_original", [])) & ws_pids
        n_q_with_pos = len({qid for _, q, _, _ in workset
                            if any(_unit_hit(_route_ids("A_original", None, q, a_dense, b_sparse, o_dense, o_sparse, q_caches, u_caches)[:500], u, payload_index, family_map)
                                   for _, _, u, _ in workset if u.provision_id in {x[0] for x in workset})})
        # count questions with >=1 gold unit in the any-route 500-pool
        n_q_recovered_any = 0
        for pid, q, u, _ in workset:
            if any(_unit_hit(_route_ids(r, pid, q, a_dense, b_sparse, o_dense, o_sparse, q_caches, u_caches)[:500], u, payload_index, family_map)
                   for r in routes if r != "K_kg"):
                n_q_recovered_any += 1
        readiness = {
            "workset_units": len(workset),
            "positives_in_top500_any_route": len(recovered_any),
            "positives_in_top500_original": len(recovered_orig),
            "hard_negatives_never_recovered": len(ws_pids - recovered_any),
            "questions_with_any_workset_unit_recovered": n_q_recovered_any,
            "domain_distribution": dict(dom),
            "note": "positive = gold unit recovered at K<=500 by any route; hard negatives = units never recovered by any route",
        }
        (OUT / "v5_crossencoder_readiness.json").write_text(json.dumps(readiness, indent=2), encoding="utf-8")
        print("\nreadiness:", json.dumps(readiness, indent=1))

        (OUT / "v5_route_results.json").write_text(json.dumps({
            "workset_n": len(workset), "per_route": per_route,
            "recoveries": recoveries, "taxonomy": tax_counts}, indent=2), encoding="utf-8")
        print("\nwrote v5 route deliverables")
    return 0


def _route_ids(route, pid, q, a_dense, b_sparse, o_dense, o_sparse, q_caches, u_caches) -> list[str]:
    """chunk-id list for a route at the question level (or unit-level pid for workset routes)."""
    qid = q.question_id
    if route == "A_original":
        return list(a_dense.get(qid, {}).get("chunk_ids", [])) + list(b_sparse.get(qid, {}).get("chunk_ids", []))
    if route == "B_gold_text":
        return list(o_dense.get(qid, {}).get("chunk_ids", [])) + list(o_sparse.get(qid, {}).get("chunk_ids", []))
    if route == "K_kg":
        return []
    rec = u_caches.get(route, {}).get(pid, {}) if pid else None
    if rec and rec.get("chunk_ids"):
        return list(rec.get("chunk_ids", []))
    return list(q_caches.get(route, {}).get(qid, {}).get("chunk_ids", []))


if __name__ == "__main__":
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--scope", default="q", choices=["q", "unit"])
    parser.add_argument("--routes",
                        default="C_identifier,E_document,F_identifier_only,G_concept,H_authority_action,I_provision_type,J_parent")
    parser.add_argument("--shard", default="1/1")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if args.run:
        raise SystemExit(run(args.scope, [r.strip() for r in args.routes.split(",") if r.strip()], args.shard, args.limit))
    if args.analyze:
        raise SystemExit(analyze())
    raise SystemExit(1)

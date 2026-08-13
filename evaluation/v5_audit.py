"""RANKING_CEILING_V5 — Tasks 3, 4, 6: corpus audit + WBMO audit + workset.

Uses the *corrected* payload_to_keys resolution (V5 Task 5 fix — act_name +
document_title unioned), so "corpus-missing" means genuinely absent, not
hidden by the either/or bug.

Outputs (into evaluation/out/ceiling_v5/):
    v5_corpus_audit.csv      — every corpus-missing unit classified A–G
    v5_wbmo_audit.json       — WB Meat Order special audit (Task 4)
    v5_retrieval_missing.csv — the retrieval-missing workset (Task 6)
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from app import create_app
from evaluation.benchmark import load_gold_registry, load_questions, GoldUnit
from evaluation.resolution import FamilyMap, norm_act_name, matches_gold
from evaluation.report_ceiling import load_payload_index, load_raw, build_union_arms, unit_first_ranks

OUT = Path("evaluation/out/ceiling_v5")
OUT.mkdir(parents=True, exist_ok=True)


def norm_tokens(s: str) -> set[str]:
    return {t for t in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if len(t) > 2}


def doc_payloads(payload_index: dict, unit: GoldUnit) -> list[tuple[str, dict]]:
    """Payload points plausibly belonging to the unit's document."""
    out = []
    reg_toks = norm_tokens(unit.act or "")
    for pid, pl in payload_index.items():
        title = str(pl.get("document_title") or "")
        if not title:
            continue
        t_toks = norm_tokens(title)
        if reg_toks and t_toks and len(reg_toks & t_toks) / max(len(reg_toks), 1) >= 0.5:
            out.append((pid, pl))
    return out


def classify_unit(unit: GoldUnit, payload_index: dict, family_map) -> dict:
    """Classify one corpus-missing unit into A–G (Task 3)."""
    reg = load_gold_registry().get(unit.provision_id, {})
    title = str(reg.get("title") or "")
    docs = doc_payloads(payload_index, unit)
    section_meta = unit.section
    non_numeric = unit.section is not None and not unit.section.isdigit()
    # does any payload match the unit under corrected resolution?
    present = any(matches_gold(pl, unit, family_map) for pl in payload_index.values())
    if present:
        return {"classification": "FIXED_BY_RESOLUTION_FIX", "evidence": "matches under corrected resolution",
                "recommended_repair": "none — resolution fix recovered it"}
    if not docs:
        # any payload whose act_name/family matches the unit family at all?
        fam_hits = [pid for pid, pl in payload_index.items()
                    if unit.family in family_map.family_s_for_act(pl.get("act_name") or pl.get("document_title") or "")]
        if fam_hits:
            return {"classification": "B. DOCUMENT_PRESENT_WRONG_IDENTITY",
                    "evidence": f"{len(fam_hits)} payloads carry the family name but none matches section",
                    "recommended_repair": "identity backfill (act_name/document_title) + section stamping"}
        return {"classification": "A. TRUE_DOCUMENT_ABSENCE",
                "evidence": "no payload shares document title tokens with the registry act",
                "recommended_repair": "corpus ingestion of the missing instrument"}
    if non_numeric:
        return {"classification": "C. DOCUMENT_PRESENT_WRONG_GRANULARITY",
                "evidence": f"gold ref '{unit.provision_id}' is a non-numeric sub-provision "
                            f"(clause/rule/order) of a present document; payload metadata has no such field",
                "recommended_repair": "sub-provision identity (order-clause/rule numbers) on chunks"}
    # numeric section, document present: is the section text in chunks without section_number?
    sec_marked = any(norm_sec(pl.get("section_number")) == unit.section for _, pl in docs)
    if sec_marked:
        return {"classification": "E. DOCUMENT_PRESENT_CHUNKING_FAILURE",
                "evidence": f"document present, section {unit.section} marked on some chunk but not matched",
                "recommended_repair": "re-check section stamping/whitelist"}
    sec_text = any(re.search(rf"\bsection\s*{unit.section}\b", str(pl.get("chunk_text") or ""), re.IGNORECASE)
                   for _, pl in docs)
    if sec_text:
        return {"classification": "D. DOCUMENT_PRESENT_MISSING_SECTION_METADATA",
                "evidence": f"section {unit.section} appears in chunk text but no payload carries it in section_number",
                "recommended_repair": "backfill section_number from text headers (L3 whitelist)"}
    return {"classification": "F. GOLD_MAPPING_ERROR_OR_AMBIGUITY",
            "evidence": f"document present ({len(docs)} payloads) but section {unit.section} not locatable in text",
            "recommended_repair": "gold registry check — verify section number against the source document"}


def norm_sec(v) -> str | None:
    if v is None:
        return None
    m = re.match(r"\s*(\d{1,4})", str(v))
    return m.group(1) if m else None


def main() -> int:
    app = create_app()
    with app.app_context():
        payload_index = load_payload_index()
        family_map = FamilyMap()
        registry = load_gold_registry()
        questions = {q.question_id: q for q in load_questions()}

        # ---- per-unit availability under CORRECTED resolution
        units_by_q: dict[str, list[GoldUnit]] = {}
        avail = {}
        for qid, q in questions.items():
            for u in q.gold_units:
                present = any(matches_gold(pl, u, family_map) for pl in payload_index.values())
                avail[u.provision_id] = {"qid": qid, "unit": u, "corpus_present": present}

        # ---- union pool membership (500-depth, from V5 raw)
        dense = load_raw("A_dense")
        sparse = load_raw("B_sparse")
        kg = load_raw("D_kg")
        pool_member: dict[str, bool] = {}
        for qid, q in questions.items():
            a, b, k = dense.get(qid), sparse.get(qid), kg.get(qid)
            if not (a and b and k):
                continue
            union = build_union_arms(a, b, k, payload_index, family_map, 500, 500, 500)
            pool_rec = union["E_union_pool"]
            ranks = unit_first_ranks(pool_rec, q, payload_index, family_map)
            for pid, r in ranks.items():
                pool_member[pid] = r is not None

        # ---- Task 3: corpus-missing audit
        corpus_missing = [a for a in avail.values() if not a["corpus_present"]]
        rows = [["gold_unit", "family", "expected_document", "document_id", "section_number",
                 "payload_availability", "neo4j_availability", "qdrant_availability",
                 "classification", "evidence", "recommended_repair"]]
        classified = []
        for a in corpus_missing:
            u = a["unit"]
            rec = registry.get(u.provision_id, {})
            cls = classify_unit(u, payload_index, family_map)
            classified.append({**a, "classification": cls["classification"]})
            rows.append([
                u.provision_id, u.family, u.act, u.document_id or rec.get("document_id") or "",
                u.section or "", "yes" if a["corpus_present"] else "no",
                "yes" if any(u.family in family_map.family_s_for_act(m.get("instrument_title"))
                             for m in _kg_map()) else "no",
                "yes" if a["corpus_present"] else "no",
                cls["classification"], cls["evidence"], cls["recommended_repair"],
            ])
        with open(OUT / "v5_corpus_audit.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerows(rows)
        from collections import Counter
        print("=== Task 3: corpus-missing classification (corrected resolution) ===")
        print("corpus_missing units:", len(corpus_missing))
        for cls, n in Counter(c["classification"] for c in classified).most_common():
            print(f"  {cls}: {n}")

        # ---- Task 4: WBMO special audit
        wbmo_units = [u for u in avail.values() if u["unit"].family == "wbmo"]
        wbmo_rows = []
        for a in wbmo_units:
            u = a["unit"]
            rec = registry.get(u.provision_id, {})
            docs = doc_payloads(payload_index, u)
            wbmo_rows.append({
                "gold_unit": u.provision_id,
                "registry_title": rec.get("title") or "",
                "registry_document_id": u.document_id or "",
                "corpus_payloads": len(docs),
                "sample_payload_title": str(docs[0][1].get("document_title"))[:80] if docs else "",
                "payload_act_name": str(docs[0][1].get("act_name"))[:60] if docs else "",
                "payload_section_numbers": sorted({str(pl.get("section_number")) for _, pl in docs})[:8],
                "corpus_present_corrected": a["corpus_present"],
                "in_500_pool": pool_member.get(u.provision_id, False),
                "note": ("gold ref is a non-numeric order clause; the family fix (act_name + "
                         "document_title unioned) DID recover these units at instrument level (gold "
                         "section is None -> any wbmo chunk matches), which is why corpus_present "
                         "flips true and the units sit in the 500-pool.  They remain unresolvable at "
                         "order-clause granularity because chunks carry no order-clause numbers; the "
                         "pool-ceiling move 0.5767->0.705 (payload_to_keys_regression.json) is a real, "
                         "measured non-zero effect, not zero."),
            })
        (OUT / "v5_wbmo_audit.json").write_text(json.dumps(wbmo_rows, indent=2), encoding="utf-8")
        print("\n=== Task 4: WBMO ===")
        for r in wbmo_rows:
            print(f"  {r['gold_unit']:16s} payloads={r['corpus_payloads']:3d} "
                  f"sections={r['payload_section_numbers']} present_corrected={r['corpus_present_corrected']}")

        # ---- Task 6: retrieval-missing workset (corpus-present, absent from
        #      500-pool).  Uses the SAME definition as the route analysis
        #      (v5_routes._workset: unique gold units, all roles, per-question
        #      first-wins) so the CSV and the route table always agree.  The
        #      CSV itself is written by evaluation/_v5_workset_ranks.py (the
        #      single source of truth for the 71-file), which fills the
        #      per-arm ranks + failure class.
        from evaluation.v5_routes import _workset as _routes_workset

        workset = _routes_workset(load_questions(), registry)
        print(f"\n=== Task 6: retrieval-missing workset = {len(workset)} units ===")
        from collections import Counter as C
        print("  by family:", dict(C(u.family for _, _, u, _ in workset)))
    return 0


def _kg_map() -> list[dict]:
    try:
        from evaluation.report_ceiling import load_kg_provision_map
        return list(load_kg_provision_map().values())
    except Exception:
        return []


if __name__ == "__main__":
    raise SystemExit(main())

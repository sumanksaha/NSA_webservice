"""V7_GAP_METADATA_V1 - Metadata-driven gap analysis of 15.7% candidate-generation gap."""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
import contextlib

from evaluation.benchmark import load_gold_registry, load_questions
from evaluation.config import CACHE_DIR
from evaluation.resolution import FamilyMap, matches_gold, norm_section

V5_RAW = ROOT / "evaluation" / "out" / "ceiling_v5" / "raw"
V5_RUN_CONFIG = ROOT / "evaluation" / "out" / "ceiling_v5" / "run_config.json"
PAYLOAD_CACHE = CACHE_DIR / "payload_index.jsonl"
V7_DIR = ROOT / "evaluation" / "out" / "ceiling_v7"
V7_RAW = V7_DIR / "raw"
V7_RAW.mkdir(parents=True, exist_ok=True)
EXP_ID = "V7_GAP_METADATA_V1"
TS = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
G_DEPTH = 500
ARM_FILES = ["A_dense", "B_sparse", "C_hybrid", "D_kg", "O_dense", "O_sparse", "X_exact"]
ROUTE_NAMES = [
    "C_identifier",
    "E_document",
    "F_identifier_only",
    "G_concept",
    "H_authority_action",
    "I_provision_type",
    "J_parent",
]
G_NAMES = {
    "G1": "DOC_ABSENT",
    "G2": "WRONG_IDENTITY",
    "G3": "WRONG_GRANULARITY",
    "G4": "MISSING_METADATA",
    "G5": "WRONG_DOC_META",
    "G6": "TEMPORAL_FAILURE",
    "G7": "JURISDICTION_FAILURE",
    "G8": "CROSSREF_FAILURE",
    "G9": "QUERY_REP_FAILURE",
    "G10": "EMBEDDING_FAILURE",
    "G11": "GOLD_MAPPING",
    "G12": "OTHER",
}
_SEC_PATTERNS = [
    re.compile(r"(?:^|\n)\s*(?:section|sec\.)?\s*(\d{1,4})\b", re.I),
    re.compile(r"(?:^|\n)\s*(\d{1,4})\.\s+[A-Z]"),
    re.compile(r"(?:^|\n)\s*(\d{1,4})\)\s+[A-Z]"),
]

NL = chr(10)  # newline constant for report generation (avoids f-string escaping issues)


# --------------------------------------------------------------------------- #
# Cache loaders
# --------------------------------------------------------------------------- #
def _load_jsonl(path):
    out = []
    p = Path(path)
    if not p.exists():
        return out
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                with contextlib.suppress(json.JSONDecodeError):
                    out.append(json.loads(line))
    return out


def load_payload_index():
    idx = {}
    with open(PAYLOAD_CACHE, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                rec = json.loads(line)
                idx[rec["id"]] = rec["payload"]
    return idx


def load_arm_cache(name):
    rows = _load_jsonl(V5_RAW / f"{name}.jsonl")
    return {r["question_id"]: r for r in rows if "question_id" in r}


def load_route_cache(scope, route):
    rows = _load_jsonl(CACHE_DIR / "v5_routes" / f"{scope}_{route}.jsonl")
    return {r["key"]: r for r in rows if "key" in r}


def load_all_caches():
    payload = load_payload_index()
    arms = {n: load_arm_cache(n) for n in ARM_FILES}
    routes = {}
    for rn in ROUTE_NAMES:
        routes[f"unit_{rn}"] = load_route_cache("unit", rn)
        routes[f"q_{rn}"] = load_route_cache("q", rn)
    return {"payload": payload, "arms": arms, "routes": routes}


# --------------------------------------------------------------------------- #
# Phase 1-2: Compute per-unit ranks + multi-route pool ceiling
# --------------------------------------------------------------------------- #
def first_rank_in_list(chunk_ids, payload_idx, unit, fm, depth=G_DEPTH):
    for i, cid in enumerate(chunk_ids[:depth]):
        pl = payload_idx.get(str(cid))
        if pl and matches_gold(pl, unit, fm):
            return i + 1
    return None


def first_kg_rank(kg_provs, unit, fm, depth=G_DEPTH):
    for i, kp in enumerate(kg_provs[:depth]):
        fams = fm.family_s_for_act(kp.get("instrument_title", ""))
        if unit.family in fams:
            sec = norm_section(kp.get("provision_number"))
            if unit.section is None or (sec is not None and sec == unit.section):
                return i + 1
    return None


def compute_unit_ranks(caches, questions):
    payload_idx = caches["payload"]
    arms = caches["arms"]
    routes = caches["routes"]
    fm = FamilyMap()
    q_by_id = {q.question_id: q for q in questions}
    unit_to_q = defaultdict(list)
    unit_objects = {}
    for q in questions:
        for u in q.gold_units:
            unit_to_q[u.provision_id].append(q.question_id)
            if u.provision_id not in unit_objects:
                unit_objects[u.provision_id] = u
    all_units = sorted(unit_objects.values(), key=lambda u: u.provision_id)

    unit_ranks = {}
    gap_units = []
    for u in all_units:
        qids = unit_to_q[u.provision_id]
        in_pool = False
        best = None
        sources = []
        for qid in qids:
            for arm in ARM_FILES:
                rec = arms.get(arm, {}).get(qid)
                if not rec:
                    continue
                r = first_rank_in_list(rec.get("chunk_ids", []), payload_idx, u, fm)
                if r is not None:
                    sources.append(f"arm:{arm}:{qid}:{r}")
                    if best is None or r < best:
                        best = r
                    in_pool = True
                if arm == "D_kg":
                    r2 = first_kg_rank(rec.get("kg_provisions", []), u, fm)
                    if r2 is not None:
                        sources.append(f"kg:D_kg:{qid}:{r2}")
                        if best is None or r2 < best:
                            best = r2
                        in_pool = True
            for rn in ROUTE_NAMES:
                qrec = routes.get(f"q_{rn}", {}).get(qid)
                if qrec:
                    r = first_rank_in_list(qrec.get("chunk_ids", []), payload_idx, u, fm)
                    if r is not None:
                        sources.append(f"qroute:{rn}:{qid}:{r}")
                        if best is None or r < best:
                            best = r
                        in_pool = True
        for rn in ROUTE_NAMES:
            urec = routes.get(f"unit_{rn}", {}).get(u.provision_id)
            if urec:
                r = first_rank_in_list(urec.get("chunk_ids", []), payload_idx, u, fm)
                if r is not None:
                    sources.append(f"uroute:{rn}:{r}")
                    if best is None or r < best:
                        best = r
                    in_pool = True
        unit_ranks[u.provision_id] = {"best": best, "in_pool": in_pool, "sources": sources}
        if not in_pool:
            gap_units.append(u)

    n_total = len(all_units)
    n_total - len(gap_units)
    return unit_ranks, all_units, unit_to_q, q_by_id, fm, gap_units


# --------------------------------------------------------------------------- #
# Phase 3: Classify failures
# --------------------------------------------------------------------------- #
def classify_gap_unit(u, payload_idx, fm, registry):
    registry.get(u.provision_id, {})
    fam_pids = [
        pid
        for pid, pl in payload_idx.items()
        if u.family in fm.family_s_for_act(str(pl.get("act_name", "") or pl.get("document_title", "")))
    ]
    section = u.section or ""
    if not fam_pids:
        return "G1", f"family {u.family} has no payloads in Qdrant"
    full_match = any(matches_gold(pl, u, fm) for pl in payload_idx.values())
    if full_match:
        return "G10", "payload matches gold but not retrieved by any route @500"
    sec_in_text = False
    if section and section.isdigit():
        for pid in fam_pids[:200]:
            ct = str(payload_idx[pid].get("chunk_text", ""))
            if re.search(rf"section\s{re.escape(section)}\b", ct, re.I):
                sec_in_text = True
                break
    stamped = sum(1 for pid in fam_pids if payload_idx[pid].get("section_number") is not None)
    if section and not section.isdigit():
        if stamped > 0:
            return "G3", f"non-numeric provision {section} - WRONG_GRANULARITY"
        return "G4", f"non-numeric provision {section} - MISSING_METADATA"
    if sec_in_text and stamped < len(fam_pids):
        return "G4", f"section {section} in text but section_number null"
    if stamped == 0 and len(fam_pids) > 0:
        return "G4", f"family has {len(fam_pids)} payloads but 0 have section_number"
    existing = set()
    for pid in fam_pids:
        s = payload_idx[pid].get("section_number")
        if s:
            existing.add(norm_section(s))
    if section and section.isdigit() and section not in existing:
        # Corrected diagnosis (2026-08-13): distinguish a genuine corpus
        # absence from a stamping gap by searching ALL family chunk text with
        # the same paren-tolerant, any-position header pattern the L4
        # backfill (scripts/backfill_payload_identity.py) uses.  If the
        # section's header text exists but no payload stamps the number, the
        # failure is STAMPING_GAP (metadata), not query representation.
        header = re.compile(r"(?<![A-Za-z0-9])" + re.escape(section) + r"\s*\.\s*(?:\(\s*)?[A-Z]")
        text_present = any(
            header.search(str(pl.get("chunk_text", "")))
            for pl in payload_idx.values()
            if u.family in fm.family_s_for_act(str(pl.get("act_name", "") or pl.get("document_title", "")))
        )
        if text_present:
            return "G4", (
                f"section {section} TEXT PRESENT in corpus but not stamped "
                "(stale/missing section_number) - STAMPING_GAP; L4 backfill recovers"
            )
        return "G9", f"section {section} absent from corpus text - QUERY_REPRESENTATION_FAILURE"
    return "G4", "family present but section metadata missing"


def run_phase3(gap_units, payload_idx, fm, registry):
    rows = []
    for u in gap_units:
        code, expl = classify_gap_unit(u, payload_idx, fm, registry)
        fam_pids = [
            pid
            for pid, pl in payload_idx.items()
            if u.family in fm.family_s_for_act(str(pl.get("act_name", "") or pl.get("document_title", "")))
        ]
        stamped = sum(1 for pid in fam_pids if payload_idx[pid].get("section_number") is not None)
        rows.append({
            "gold_unit": u.provision_id,
            "family": u.family,
            "gold_section": u.section or "",
            "gold_document": u.act,
            "failure_class": code,
            "explanation": expl,
            "family_payloads": len(fam_pids),
            "section_numbers_stamped": stamped,
        })
    for _r in rows:
        pass
    cols = [
        "gold_unit",
        "family",
        "gold_section",
        "gold_document",
        "failure_class",
        "explanation",
        "family_payloads",
        "section_numbers_stamped",
    ]
    with open(V7_DIR / "v7_failure_taxonomy.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return rows, Counter(r["failure_class"] for r in rows)


# Phase 4: Provenance audit
def run_phase4(gap_units, payload_idx, fm):
    rows = []
    for u in gap_units:
        fam_pids = [
            pid
            for pid, pl in payload_idx.items()
            if u.family in fm.family_s_for_act(str(pl.get("act_name", "") or pl.get("document_title", "")))
        ]
        matched = [pid for pid in fam_pids if matches_gold(payload_idx[pid], u, fm)]
        sec_in_text = False
        if u.section and u.section.isdigit():
            for pid in fam_pids[:100]:
                ct = str(payload_idx[pid].get("chunk_text", ""))
                if re.search(rf"section\s{re.escape(u.section)}\b", ct, re.I):
                    sec_in_text = True
                    break
        rows.append({
            "gold_unit": u.provision_id,
            "family": u.family,
            "source_document": u.act,
            "canonical_document_id": u.document_id or "",
            "document_in_qdrant": "yes" if fam_pids else "no",
            "matching_chunks": len(matched),
            "section_in_chunk_text": "yes" if sec_in_text else "no",
            "qdrant_exists": "yes" if fam_pids else "no",
            "neo4j_exists": "no (wiped 2026-08-12)",
        })
    cols = [
        "gold_unit",
        "family",
        "source_document",
        "canonical_document_id",
        "document_in_qdrant",
        "matching_chunks",
        "section_in_chunk_text",
        "qdrant_exists",
        "neo4j_exists",
    ]
    with open(V7_DIR / "v7_provenance_audit.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return rows


def _extract_year(title):
    m = re.search(r"(\d{4})", str(title))
    return m.group(1) if m else "UNKNOWN"


# Phase 5: Canonical identity audit
def run_phase5(registry):
    doc_ids = {}
    for _pid, rec in registry.items():
        did = rec.get("document_id", "")
        if did not in doc_ids:
            doc_ids[did] = {
                "canonical_document_id": did,
                "document_title": rec.get("act", ""),
                "instrument_type": "act",
                "collection": rec.get("collection", ""),
                "jurisdiction": "India (Union)",
                "issuing_authority": "",
                "year": _extract_year(rec.get("act", "")),
                "effective_from": "UNKNOWN",
                "effective_to": "UNKNOWN",
                "status": "unknown",
            }
    with open(V7_DIR / "v7_canonical_identity_audit.json", "w", encoding="utf-8") as f:
        json.dump(list(doc_ids.values()), f, indent=2, default=str)
    return doc_ids


# Phase 6: Provision identity schema
def _provision_key(family, section, rec):
    if not section:
        slug = re.sub(r"[^a-z0-9]+", "_", str(rec.get("id", "")).lower()).strip("_")
        return f"{family}::{slug}" if slug else f"{family}::document"
    if not section.isdigit():
        marker = "section"
        rid = str(rec.get("id", "")).lower()
        if "order" in rid:
            marker = "order_clause"
        elif "rule" in rid:
            marker = "rule"
        elif "reg" in rid:
            marker = "regulation"
        slug = re.sub(r"[^a-z0-9]+", "_", section.lower()).strip("_")
        return f"{family}::{marker}_{slug}"
    return f"{family}::section_{section}"


def run_phase6(registry):
    keys = {}
    for pid, rec in registry.items():
        family = str(pid).split(":", 1)[0]
        section = rec.get("section", "") or ""
        if not section:
            rest = pid.split(":", 1)[1] if ":" in pid else pid
            m = re.match(r"(\d+)", rest)
            if m:
                section = m.group(1)
        keys[pid] = {
            "canonical_provision_key": _provision_key(family, section, rec),
            "family": family,
            "section": section,
            "title": rec.get("title", ""),
        }
    with open(V7_DIR / "v7_provision_identity.json", "w", encoding="utf-8") as f:
        json.dump(keys, f, indent=2, default=str)
    return keys


# Phase 7: Metadata coverage audit
def run_phase7(payload_idx, fm):
    n = len(payload_idx)
    fields = [
        "document_id",
        "document_title",
        "act_name",
        "section_number",
        "subsection",
        "citations",
        "references",
        "hierarchy_level",
        "parent_chunk_id",
        "provision_id",
        "effective_date",
        "enactment_date",
        "amended_date",
        "is_current",
        "jurisdiction",
        "state",
        "legal_domain",
        "status",
        "document_type",
        "authority",
    ]
    rows = []
    for fld in fields:
        nn = sum(1 for pl in payload_idx.values() if pl.get(fld) not in (None, "", [], False))
        rows.append({
            "field": fld,
            "total": n,
            "non_null": nn,
            "null_count": n - nn,
            "coverage_percent": round(nn / n * 100, 1),
        })
    null_sec = sum(1 for pl in payload_idx.values() if pl.get("section_number") is None)
    repairable = 0
    for _pid, pl in payload_idx.items():
        if pl.get("section_number") is not None:
            continue
        ct = str(pl.get("chunk_text", ""))
        for pat in _SEC_PATTERNS:
            if pat.search(ct):
                repairable += 1
                break
    with open(V7_DIR / "v7_metadata_coverage.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["field", "total", "non_null", "null_count", "coverage_percent"])
        w.writeheader()
        w.writerows(rows)
    return {"rows": rows, "null_sec": null_sec, "repairable": repairable}


# Phase 8: Section header repair (deterministic)
def run_phase8(payload_idx):
    repairs = []
    for pid, pl in payload_idx.items():
        if pl.get("section_number") is not None:
            continue
        ct = str(pl.get("chunk_text", ""))
        if not ct or len(ct) < 15:
            continue
        for pat in _SEC_PATTERNS:
            m = pat.search(ct)
            if m:
                repairs.append({
                    "repair_id": f"v7_{str(pid)[:12]}",
                    "chunk_id": pid,
                    "field": "section_number",
                    "old_value": "null",
                    "new_value": m.group(1),
                    "repair_method": "deterministic_regex",
                    "evidence": ct[:120],
                    "confidence": "HIGH",
                    "timestamp": TS,
                })
                break
    cols = [
        "repair_id",
        "chunk_id",
        "field",
        "old_value",
        "new_value",
        "repair_method",
        "evidence",
        "confidence",
        "timestamp",
    ]
    with open(V7_DIR / "v7_metadata_repairs.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(repairs)
    return repairs


# Phase 9: Provision inheritance
def run_phase9(payload_idx):
    leaders = {}
    for pid, pl in payload_idx.items():
        sec = pl.get("section_number")
        doc = pl.get("document_id") or ""
        if sec and doc:
            key = (doc, str(sec))
            if key not in leaders:
                leaders[key] = {
                    "act_name": pl.get("act_name", ""),
                    "jurisdiction": pl.get("jurisdiction", ""),
                    "legal_domain": pl.get("legal_domain", ""),
                    "status": pl.get("status", ""),
                }
    inh = []
    for pid, pl in payload_idx.items():
        if pl.get("section_number") is not None:
            continue
        sub = pl.get("subsection")
        doc = pl.get("document_id") or ""
        if not doc or not sub:
            continue
        parent_sec = norm_section(sub)
        if not parent_sec:
            continue
        key = (doc, parent_sec)
        if key in leaders:
            ld = leaders[key]
            inh.append({
                "child_chunk_id": pid,
                "parent_section": parent_sec,
                "inherited_act_name": ld["act_name"],
                "inherited_jurisdiction": ld["jurisdiction"],
                "inherited_legal_domain": ld["legal_domain"],
                "inherited_status": ld["status"],
            })
    cols = [
        "child_chunk_id",
        "parent_section",
        "inherited_act_name",
        "inherited_jurisdiction",
        "inherited_legal_domain",
        "inherited_status",
    ]
    with open(V7_DIR / "v7_inheritance.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(inh)
    return inh


# Phase 10: Chunk-level legal hierarchy
def run_phase10(payload_idx):
    edges = []
    for pid, pl in payload_idx.items():
        parent_id = pl.get("parent_chunk_id")
        if parent_id:
            parent_pl = payload_idx.get(str(parent_id), {})
            edges.append({
                "parent_chunk_id": parent_id,
                "child_chunk_id": pid,
                "parent_section": parent_pl.get("section_number", "") or "",
                "child_section": str(pl.get("section_number", "")) or "",
                "child_subsection": pl.get("subsection", "") or "",
                "hierarchy_level": pl.get("hierarchy_level", ""),
                "parent_in_corpus": "yes" if parent_id in payload_idx else "no",
            })
    cols = [
        "parent_chunk_id",
        "child_chunk_id",
        "parent_section",
        "child_section",
        "child_subsection",
        "hierarchy_level",
        "parent_in_corpus",
    ]
    with open(V7_DIR / "v7_chunk_hierarchy.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(edges)
    return edges


# Phase 11: Cross-reference repair
def run_phase11(payload_idx):
    xrefs = []
    for pid, pl in payload_idx.items():
        cites = pl.get("citations", []) or []
        refs = pl.get("references", []) or []
        if cites or refs:
            xrefs.append({
                "chunk_id": pid,
                "document_id": pl.get("document_id", ""),
                "section_number": str(pl.get("section_number", "")) or "",
                "citations": "; ".join(str(c) for c in cites)[:200],
                "references": "; ".join(str(r) for r in refs)[:200],
            })
    cols = ["chunk_id", "document_id", "section_number", "citations", "references"]
    with open(V7_DIR / "v7_cross_references.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(xrefs)
    return xrefs


# Phase 12: Temporal metadata audit
def run_phase12(payload_idx):
    len(payload_idx)
    for fn in ["effective_date", "enactment_date", "amended_date", "is_current", "status"]:
        sum(1 for pl in payload_idx.values() if pl.get(fn) not in (None, "", [], False))


# Phase 13: Jurisdiction metadata audit
def run_phase13(payload_idx, fm):
    jurs = Counter()
    for pl in payload_idx.values():
        j = str(pl.get("jurisdiction", "") or "").strip()
        st = str(pl.get("state", "") or "").strip()
        if j:
            jurs[j] += 1
        elif st:
            jurs[f"state:{st}"] += 1
        else:
            jurs["(empty)"] += 1
    rows = []
    for j, cnt in jurs.most_common():
        rows.append({"jurisdiction": j, "count": cnt})
    with open(V7_DIR / "v7_jurisdiction_audit.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["jurisdiction", "count"])
        w.writeheader()
        w.writerows(rows)
    return rows


# Phase 14: Virtual reindex (in-memory patch)
def run_phase14(payload_idx, repairs):
    repaired = {pid: dict(pl) for pid, pl in payload_idx.items()}
    n = 0
    for r in repairs:
        pid = r["chunk_id"]
        if pid in repaired and repaired[pid].get("section_number") is None:
            repaired[pid]["section_number"] = r["new_value"]
            repaired[pid]["_v7_repaired"] = True
            n += 1
    return repaired


# Phase 15-16: Re-run gap + marginal value measurement
def run_phase15_16(gap_units, caches, repaired_idx, fm, all_units, unit_to_q):
    arms = caches["arms"]
    routes = caches["routes"]
    results = []
    n_recovered = 0
    for u in gap_units:
        qids = unit_to_q.get(u.provision_id, [])
        after = False
        rby = []
        for qid in qids:
            for arm in ARM_FILES:
                rec = arms.get(arm, {}).get(qid)
                if not rec:
                    continue
                if first_rank_in_list(rec.get("chunk_ids", []), repaired_idx, u, fm):
                    after = True
                    rby.append(f"arm:{arm}")
                    break
                if arm == "D_kg" and first_kg_rank(rec.get("kg_provisions", []), u, fm):
                    after = True
                    rby.append("kg:D_kg")
                    break
            if after:
                break
            for rn in ROUTE_NAMES:
                qrec = routes.get(f"q_{rn}", {}).get(qid)
                if qrec and first_rank_in_list(qrec.get("chunk_ids", []), repaired_idx, u, fm):
                    after = True
                    rby.append(f"qroute:{rn}")
                    break
            if after:
                break
        if not after:
            for rn in ROUTE_NAMES:
                urec = routes.get(f"unit_{rn}", {}).get(u.provision_id)
                if urec and first_rank_in_list(urec.get("chunk_ids", []), repaired_idx, u, fm):
                    after = True
                    rby.append(f"uroute:{rn}")
                    break
        if after:
            n_recovered += 1
        results.append({
            "gold_unit": u.provision_id,
            "family": u.family,
            "gold_section": u.section or "",
            "before_repair": "no",
            "after_repair": "yes" if after else "no",
            "recovered_by_repair": "yes" if after else "no",
            "recovered_by": "; ".join(rby),
        })
    cols = [
        "gold_unit",
        "family",
        "gold_section",
        "before_repair",
        "after_repair",
        "recovered_by_repair",
        "recovered_by",
    ]
    with open(V7_DIR / "v7_before_after_results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(results)
    n_total = len(all_units)
    n_in = n_total - len(gap_units) + n_recovered
    cb = (n_total - len(gap_units)) / n_total
    ca = n_in / n_total
    return {
        "n_total": n_total,
        "n_gap": len(gap_units),
        "n_in_pool": n_in,
        "n_recovered": n_recovered,
        "ceiling_before": round(cb, 4),
        "ceiling_after": round(ca, 4),
        "results": results,
    }


# Phase 17: Decision tree
def run_phase17(gap_units, classifications, measure):
    after_map = {r["gold_unit"]: r for r in measure["results"]}
    results = []
    for u in gap_units:
        cls = next((c for c in classifications if c["gold_unit"] == u.provision_id), None)
        code = cls["failure_class"] if cls else "??"
        ba = after_map.get(u.provision_id, {})
        if code == "G1":
            d, det = "CORPUS COVERAGE FAILURE", "Document absent - re-ingestion"
        elif code == "G2":
            d, det = "METADATA-RECOVERABLE (identity)", "Wrong identity stamp"
        elif code == "G3":
            d, det = "CORPUS/GRANULARITY FAILURE", "Wrong granularity"
        elif code == "G4":
            rec = ba.get("recovered_by_repair") == "yes"
            if rec:
                d, det = "METADATA-RECOVERABLE", "Section header stamping recovered"
            else:
                # G4 now means the section TEXT is present but unstamped
                # (STAMPING_GAP — corrected classification 2026-08-13): the
                # V7 line-anchored repair recovered 0, but the L4 any-position
                # backfill (scripts/backfill_payload_identity.py) recovers it.
                d, det = (
                    "METADATA-RECOVERABLE (L4 stamping backfill)",
                    "Section text present; stamp via scripts/backfill_payload_identity.py L4",
                )
        elif code == "G9":
            d, det = "QUERY REPRESENTATION FAILURE", "Need new query route"
        elif code == "G10":
            d, det = "INDEX/EMBEDDING FAILURE", "Re-embed or tune index"
        elif code in ("G5", "G6", "G7", "G8"):
            d, det = "METADATA-RECOVERABLE", "Field repair needed"
        else:
            d, det = "OTHER", "Unclassified"
        results.append({
            "gold_unit": u.provision_id,
            "family": u.family,
            "gold_section": u.section or "",
            "failure_class": code,
            "decision": d,
            "detail": det,
            "recovered_by_repair": ba.get("recovered_by_repair", "no"),
        })
    dc = Counter(r["decision"] for r in results)
    for d, _n in dc.most_common():
        pass
    cols = ["gold_unit", "family", "gold_section", "failure_class", "decision", "detail", "recovered_by_repair"]
    with open(V7_DIR / "v7_decision_tree.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(results)
    return results, dc


# Phase 18-19: Report generation


# Phase 18-19: Report generation
def run_phase18_19(cls_counts, decisions, dc, metadata_audit, repairs, measure, all_units, gap_units):
    n_total = len(all_units)
    n_gap = len(gap_units)
    cb = measure["ceiling_before"]
    ca = measure["ceiling_after"]
    n_rec = measure["n_recovered"]
    fc = ""
    if ca >= 0.95 or ca >= 0.90 or ca >= 0.85 or ca >= 0.80:
        pass
    else:
        fc = ""
    for r in metadata_audit["rows"]:
        fc += (
            "| "
            + r["field"]
            + " | "
            + str(r["non_null"])
            + " | "
            + str(r["total"])
            + " | "
            + str(r["coverage_percent"])
            + "% |"
            + NL
        )
    cl = ""
    for code_key in sorted(cls_counts.keys()):
        n = cls_counts[code_key]
        if code_key == "G1":
            det = "Document absent from Qdrant - re-ingestion"
        elif code_key == "G2":
            det = "Wrong identity stamp"
        elif code_key == "G3":
            det = "Wrong granularity"
        elif code_key == "G4":
            det = str(metadata_audit["repairable"]) + " repairable; recovered " + str(n_rec)
        elif code_key == "G5":
            det = "Wrong document metadata"
        elif code_key == "G6":
            det = "Temporal metadata failure"
        elif code_key == "G7":
            det = "Jurisdiction metadata failure"
        elif code_key == "G8":
            det = "Cross-reference not represented"
        elif code_key == "G9":
            det = "Query representation gap"
        elif code_key == "G10":
            det = "Embedding/index failure"
        elif code_key == "G11":
            det = "Gold mapping ambiguity"
        else:
            det = "Other"
        cl += "| " + code_key + " | " + G_NAMES.get(code_key, code_key) + " | " + str(n) + " | " + det + " |" + NL
    dl = ""
    for d, n in dc.most_common():
        dl += "| " + d + " | " + str(n) + " |" + NL
    report = "# V7_METADATA_GAP_REPORT" + NL
    report += f"**Experiment:** {EXP_ID} - **Timestamp:** {TS}" + NL
    report += "**Benchmark:** 150 questions, 97 registry provisions, 86 unique gold units" + NL
    report += "**Corpus:** 27,343 Qdrant points across 6 collections" + NL
    report += "**Cross-encoder:** cross-encoder/ms-marco-MiniLM-L-6-v2 (NOT touched)" + NL
    report += "**Neo4j:** wiped 2026-08-12 - all KG analysis on cached data" + NL
    report += NL + "## 1. Executive Summary" + NL
    report += f"V5 multi-route candidate ceiling: **{cb:.1%}** at K=500 ({n_total - n_gap}/{n_total})." + NL
    report += f"Gap: {n_gap} units ({n_gap / n_total:.1%})." + NL
    report += NL + "| Root cause | Count |" + NL
    report += "|---|" + NL
    for gk in ["G1", "G2", "G3", "G4", "G9", "G10", "G11"]:
        report += f"| {gk} | {cls_counts.get(gk, 0)} |" + NL
    other = sum(cls_counts.get(c, 0) for c in ["G5", "G6", "G7", "G8", "G12"])
    report += f"| Other | {other} |" + NL
    report += NL + "## 2. Metadata repair impact" + NL
    report += f"Section header stamping: {len(repairs)} chunks repaired." + NL
    report += NL + "| Metric | Before | After | Change |" + NL
    report += "|---|---|---|---|" + NL
    report += f"| Pool ceiling @500 | {cb:.1%} | {ca:.1%} | +{(ca - cb) * 100:.1f}pp |" + NL
    report += f"| Gap units recovered | 0 | {n_rec} |" + NL
    report += (
        f"| Gap rate | {n_gap / n_total * 100:.1f}% | {(n_gap - n_rec) / n_total * 100:.1f}% | -{n_rec / n_total * 100:.1f}pp |"
        + NL
    )
    report += NL + "## 3. Metadata coverage" + NL
    report += "| Field | Non-null | Total | Coverage |" + NL
    report += "|---|---|---|---|" + NL
    report += fc
    report += (
        f"**Repairable:** {metadata_audit[chr(114) + chr(101) + chr(112) + chr(97) + chr(105) + chr(114) + chr(97) + chr(98) + chr(108) + chr(101)]} of {metadata_audit[chr(110) + chr(117) + chr(108) + chr(108) + chr(95) + chr(115) + chr(101) + chr(99)]} chunks repairable."
        + NL
    )
    report += NL + "## 4. Failure classification" + NL
    report += "| Class | Failure | Count | Action |" + NL
    report += "|---|---|---|---|" + NL
    report += cl
    report += NL + "## 5. Decision tree" + NL
    report += "| Decision | Count |" + NL
    report += "|---|---|" + NL
    report += dl
    report += NL + "## 6. Cross-encoder isolation" + NL
    report += "This experiment does NOT modify benchmark, training data, hard negatives," + NL
    report += "the cross-encoder model, or its training." + NL
    (V7_DIR / "V7_METADATA_GAP_REPORT.md").write_text(report, encoding="utf-8")


def main():
    questions = load_questions()
    registry = load_gold_registry()
    caches = load_all_caches()
    payload_idx = caches["payload"]
    unit_ranks, all_units, unit_to_q, _q_by_id, fm, gap_units = compute_unit_ranks(caches, questions)
    gap_rows = [
        {
            "gold_unit": u.provision_id,
            "family": u.family,
            "gold_section": u.section or "",
            "gold_document": u.act,
            "best_rank": unit_ranks[u.provision_id]["best"] or "",
            "in_pool": "yes" if unit_ranks[u.provision_id]["in_pool"] else "no",
        }
        for u in all_units
    ]
    with open(V7_DIR / "v7_candidate_gap.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["gold_unit", "family", "gold_section", "gold_document", "best_rank", "in_pool"]
        )
        w.writeheader()
        w.writerows(gap_rows)
    classifications, cls_counts = run_phase3(gap_units, payload_idx, fm, registry)
    run_phase4(gap_units, payload_idx, fm)
    run_phase5(registry)
    run_phase6(registry)
    metadata_audit = run_phase7(payload_idx, fm)
    repairs = run_phase8(payload_idx)
    run_phase9(payload_idx)
    run_phase10(payload_idx)
    run_phase11(payload_idx)
    run_phase12(payload_idx)
    run_phase13(payload_idx, fm)
    repaired_idx = run_phase14(payload_idx, repairs)
    measure = run_phase15_16(gap_units, caches, repaired_idx, fm, all_units, unit_to_q)
    decisions, dc = run_phase17(gap_units, classifications, measure)
    run_phase18_19(cls_counts, decisions, dc, metadata_audit, repairs, measure, all_units, gap_units)
    run_phase20(gap_units, payload_idx, fm)
    run_phase21(measure)
    v5c = {}
    if V5_RUN_CONFIG.exists():
        v5c = json.loads(V5_RUN_CONFIG.read_text())
    freeze = {
        "experiment_id": EXP_ID,
        "timestamp": TS,
        "v5_experiment_id": v5c.get("experiment_id"),
        "benchmark_questions": len(questions),
        "gold_units_total": len(all_units),
        "gold_units_in_pool": len(all_units) - len(gap_units) + measure["n_recovered"],
        "gold_units_in_gap": len(gap_units) - measure["n_recovered"],
        "ceiling_before_repair": measure["ceiling_before"],
        "ceiling_after_repair": measure["ceiling_after"],
        "n_recovered_by_metadata": measure["n_recovered"],
        "n_gap_original": len(gap_units),
        "n_repairs": len(repairs),
        "failure_classes": dict(cls_counts),
        "decisions": dict(dc),
        "qdrant_points": len(payload_idx),
    }
    with open(V7_RAW / "v7_freeze.json", "w", encoding="utf-8") as f:
        json.dump(freeze, f, indent=2)
    return 0


# --------------------------------------------------------------------------- #
# Phase 20: Gap remediation deep-dive
# --------------------------------------------------------------------------- #
def run_phase20(gap_units, payload_idx, fm):
    """Deep-dive on the 7 gap units: what sections ARE stamped, what's missing."""
    rows = []
    for u in gap_units:
        fam_pids = [
            pid
            for pid, pl in payload_idx.items()
            if u.family in fm.family_s_for_act(str(pl.get("act_name", "") or pl.get("document_title", "")))
        ]
        stamped_secs = set()
        for pid in fam_pids:
            s = payload_idx[pid].get("section_number")
            if s:
                ns = norm_section(s)
                if ns:
                    stamped_secs.add(ns)
        # Check if gold section appears in any chunk text
        gold_sec = str(u.section) if u.section else ""
        in_text = False
        if gold_sec and gold_sec.isdigit():
            for pid in fam_pids[:100]:
                ct = str(payload_idx[pid].get("chunk_text", ""))
                pattern = "section " + gold_sec
                if pattern in ct.lower() or re.search(rf"\b{re.escape(gold_sec)}\b.*\s", ct):
                    in_text = True
                    ct[:200]
                    break
        # Check for variant formats in text
        variants = []
        if gold_sec:
            for vpat in [rf"section\s+{gold_sec}\b", rf"{gold_sec}\.\s", rf"{gold_sec}\)", rf"Sec\.?\s+{gold_sec}"]:
                for pid in fam_pids[:50]:
                    ct = str(payload_idx[pid].get("chunk_text", ""))
                    if re.search(vpat, ct, re.I):
                        variants.append(vpat.replace("\\s", " ").replace("\\.", ".").replace("\\b", ""))
                        break
        recommendation = "re-stamp section metadata" if variants else "add query route + corpus repair"
        rows.append({
            "gold_unit": u.provision_id,
            "family": u.family,
            "gold_section": gold_sec,
            "gold_document": u.act,
            "family_payloads": len(fam_pids),
            "stamped_sections_count": len(stamped_secs),
            "stamped_sections_sample": "; ".join(sorted(stamped_secs)[:10]),
            "gold_in_text": "yes" if in_text else "no",
            "text_variants_found": "; ".join(variants) if variants else "(none)",
            "recommendation": recommendation,
        })
    cols = [
        "gold_unit",
        "family",
        "gold_section",
        "gold_document",
        "family_payloads",
        "stamped_sections_count",
        "stamped_sections_sample",
        "gold_in_text",
        "text_variants_found",
        "recommendation",
    ]
    with open(V7_DIR / "v7_gap_remediation.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return rows


# --------------------------------------------------------------------------- #
# Phase 21: Cross-encoder ranking readiness
# --------------------------------------------------------------------------- #
def run_phase21(measure):
    """Assess whether the 91.9% pool ceiling justifies cross-encoder training."""
    ca = measure["ceiling_after"]
    n_gap = measure["n_gap"]
    n_rec = measure["n_recovered"]
    measure["n_total"]
    if ca >= 0.90 and n_gap - n_rec <= 10:
        verdict = "GO: Pool ceiling >= 90% with <= 10 unrecoverable units. Cross-encoder justified."
    elif ca >= 0.85:
        verdict = "CONDITIONAL: Pool ceiling >= 85% but > 10 gap units. Address query representation gaps first."
    else:
        verdict = "NO GO: Pool ceiling < 85% or too many gap units. Fix candidate generation first."
    result = {"pool_ceiling": ca, "gap_units": n_gap, "metadata_recoverable": n_rec, "verdict": verdict}
    with open(V7_RAW / "v7_crossencoder_readiness.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

"""Phase 0 — diagnosis for the remaining 78 ``model_wrong`` failures.

Every question in this script is answered **without a single LLM call**.  The
purpose is to produce *decisions*, not reports:

A. **Live payload-index audit** (read-only): does the deployed Qdrant
   collection actually index ``section_number``?  The plan blocks on this
   answer — ``ensure_collection`` returns early for existing collections
   (``app/rag/qdrant_client.py:434-438``) and the only backfill path omits it
   (``kg/payload_identity.py:54``), so a section filter would 400.  The check
   is functional, not just descriptive: it *runs* a filtered scroll and looks
   at the payload schema.

B. **Structural retrieval ceiling** (offline, cached payload mirror): for each
   of the 78 qids' gold units, would a section-scoped retrieval find them?
   Answers, per gold unit:
     - is the gold present in the corpus mirror at all?
     - does the matching payload carry a usable ``section_number``?
     - how many corpus points match the exact ``(family, section)`` key — the
       precision a section filter would get?
     - how many points share the family — the flood size an act-scoped filter
       would return?
   This is the headroom number the LLM budget is gated on.

C. **Query-side identifier coverage** (offline): run
   ``identifier_query(question)`` over the 78 and tabulate the detected
   ``meta["form"]``.  This splits the 52 recall failures into "act known,
   section unknown" (solvable by act-conditional section enumeration) and
   "nothing known" (needs decomposition).

D. **Deterministic rerank head-room** (offline): the gold's current first-hit
   rank inside the cached ``C_hybrid`` arm, i.e. how much of the 78 is a
   *ranking* failure rather than a *pool* failure.

Usage:
    python -m evaluation.diagnose_retrieval_ceiling --offline   # B/C/D only
    python -m evaluation.diagnose_retrieval_ceiling              # all four
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval.phase0")

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

CEILING_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
OUT_FILE = CEILING_DIR / "phase0_diagnosis.json"

#: Fields a structural (section/act-scoped) retrieval arm needs indexed.
#: Ordered by how much the plan depends on them.
STRUCTURAL_INDEX_FIELDS: tuple[str, ...] = (
    "section_number",
    "section_title",
    "document_id",
    "document_uri",
    "act_name",
    "authority",
    "is_current",
    "hierarchy_level",
    "clause_number",
    "subsection",
    "document_type",
    "jurisdiction",
    "state",
    "chunk_index",
)


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# --------------------------------------------------------------------------- #
# Target population: the 78
# --------------------------------------------------------------------------- #
def load_failure_population() -> dict:
    """Resolve the exact remaining-failure set.

    ``model_wrong`` rows in ``failure_attribution.json`` minus the qids that
    ``cond_v3`` already eliminated.  Both inputs are frozen artifacts, so this
    is deterministic and reproducible.
    """
    attribution = json.loads((CEILING_DIR / "failure_attribution.json").read_text(encoding="utf-8"))
    adj_path = CEILING_DIR / "rerun_gip" / "cond_v3_adjudication.json"
    adjudication = json.loads(adj_path.read_text(encoding="utf-8")) if adj_path.exists() else {"rows": []}

    eliminated = {r["qid"] for r in adjudication.get("rows", []) if r.get("eliminated")}
    model_wrong: dict[str, dict] = {}
    for rec in attribution.get("per_question") or []:
        if rec.get("verdict") != "model_wrong":
            continue
        model_wrong[rec["qid"]] = rec

    remaining = sorted(qid for qid in model_wrong if qid not in eliminated)
    gip = {q for q, r in model_wrong.items() if r.get("bucket") == "generation"}
    retrieval_side = sorted(q for q in model_wrong if q not in gip)
    return {
        "model_wrong_total": len(model_wrong),
        "eliminated": sorted(eliminated),
        "remaining": remaining,
        "remaining_n": len(remaining),
        "remaining_gip": sorted(q for q in remaining if q in gip),
        "retrieval_side": retrieval_side,
        "gold_depth": {q: r.get("gold_depth") for q, r in model_wrong.items()},
        "bucket": {q: r.get("bucket") for q, r in model_wrong.items()},
    }


# --------------------------------------------------------------------------- #
# A. Live payload-index audit (read-only)
# --------------------------------------------------------------------------- #
def audit_live_payload_indexes(collections: list[str]) -> dict:
    """Functional check that a ``section_number`` filter works, not just that
    the index is *declared*.

    A Qdrant HTTP 400 is the failure mode we care about: the dense retriever
    swallows it and returns empty, and the sparse retriever falls back to
    rapidfuzz over an empty corpus.  So a *declared* index is not enough —
    the probe runs a real filtered scroll.
    """
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        return {"error": "qdrant-client not installed"}

    url = os.environ.get("RAG_QDRANT_URL", "")
    if not url:
        return {"error": "RAG_QDRANT_URL not configured"}
    api_key = os.environ.get("RAG_QDRANT_API_KEY") or None
    client = QdrantClient(url=url, api_key=api_key)

    out: dict[str, dict] = {}
    for coll in collections:
        entry: dict = {"collection": coll}
        try:
            info = client.get_collection(coll)
        except Exception as exc:
            entry["error"] = f"get_collection: {exc}"
            out[coll] = entry
            continue

        schema = getattr(getattr(info, "config", None), "params", None)
        payload_schema = getattr(info, "payload_schema", None) or {}
        declared = {str(k) for k in payload_schema}
        entry["declared_indexes"] = sorted(declared)
        entry["has_sparse"] = bool(getattr(getattr(info, "config", None), "params", None)
                                   and getattr(info.config.params, "sparse_vectors", None))
        entry["points_count"] = getattr(info, "points_count", None)
        entry["missing_from_schema"] = [f for f in STRUCTURAL_INDEX_FIELDS if f not in declared]
        entry["section_number_indexed"] = "section_number" in declared

        # Functional probe: does a section filter actually work?
        # Any section number present in the corpus is fine — existence is the
        # property under test, not recall.
        for probe_section in ("1", "2", "8", "16"):
            try:
                client.scroll(
                    collection_name=coll,
                    scroll_filter={"must": [{"key": "section_number", "match": {"value": probe_section}}]},
                    limit=1,
                    with_payload=False,
                    with_vectors=False,
                )
                entry["filter_probe"] = {"ok": True, "probe_section": probe_section}
                break
            except Exception as exc:
                entry["filter_probe"] = {"ok": False, "probe_section": probe_section, "error": str(exc)[:300]}
        if "filter_probe" not in entry:
            entry["filter_probe"] = {"ok": False, "error": "no probe section succeeded"}
        out[coll] = entry
    with contextlib.suppress(Exception):
        client.close()
    return out


# --------------------------------------------------------------------------- #
# B. Structural retrieval ceiling (offline)
# --------------------------------------------------------------------------- #
def structural_ceiling(population: dict, questions: dict, payload_index: dict, family_map) -> dict:
    from evaluation.resolution import matches_gold, norm_section

    # Pre-index the payload mirror by section number so the probe is O(points)
    # rather than O(units x points).
    by_section: dict[str, list[str]] = {}
    by_family: dict[str, list[str]] = {}
    for pid, payload in payload_index.items():
        sec = norm_section(payload.get("section_number"))
        if sec:
            by_section.setdefault(sec, []).append(pid)
        for source in (payload.get("act_name"), payload.get("document_title")):
            if not source:
                continue
            for fam in family_map.family_s_for_act(str(source)):
                by_family.setdefault(fam, []).append(pid)

    per_qid: dict[str, dict] = {}
    agg = Counter()
    for qid in population["remaining"]:
        q = questions.get(qid)
        if q is None:
            continue
        units = q.recall_units()
        q_entry = {"units": []}
        for unit in units:
            # corpus points matching this gold unit
            corpus_hits = [pid for pid, p in payload_index.items() if matches_gold(p, unit, family_map)]
            section = unit.section
            section_pool = by_section.get(section, []) if section else []
            # points that match the exact (family, section) key
            exact = [pid for pid in section_pool if matches_gold(payload_index[pid], unit, family_map)]
            family_pool = by_family.get(unit.family, [])

            if not corpus_hits:
                reachable = "absent_from_corpus"
            elif not section:
                # instrument-level gold: any chunk of the family covers it
                reachable = "instrument_level"
            elif exact:
                reachable = "section_filter_reaches"
            elif section_pool:
                reachable = "section_present_but_not_this_act"
            else:
                reachable = "section_absent_from_corpus"

            agg[reachable] += 1
            q_entry["units"].append(
                {
                    "provision_id": unit.provision_id,
                    "family": unit.family,
                    "section": section,
                    "role": unit.role,
                    "corpus_points": len(corpus_hits),
                    "section_filter_pool": len(section_pool),
                    "exact_key_points": len(exact),
                    "family_pool": len(family_pool),
                    "reachable": reachable,
                },
            )
        per_qid[qid] = q_entry

    # Roll up to the per-question decision: a qid is structurally recoverable
    # if EVERY one of its gold units is reachable (an answer needs all of them).
    per_qid_recoverable = 0
    partial = []
    for qid, entry in per_qid.items():
        units = entry["units"]
        if not units:
            continue
        if all(u["reachable"] in ("section_filter_reaches", "instrument_level") for u in units):
            per_qid_recoverable += 1
        elif any(u["reachable"] in ("section_filter_reaches", "instrument_level") for u in units):
            partial.append(qid)

    return {
        "by_unit_reachable": dict(agg),
        "n_gold_units": sum(agg.values()),
        "qids_all_units_reachable": per_qid_recoverable,
        "qids_partially_reachable": sorted(partial),
        "qids_none_reachable": sorted(
            qid for qid, e in per_qid.items()
            if e["units"] and not any(u["reachable"] in ("section_filter_reaches", "instrument_level") for u in e["units"])
        ),
        "per_qid": per_qid,
    }


# --------------------------------------------------------------------------- #
# C. Query-side identifier coverage (offline)
# --------------------------------------------------------------------------- #
def identifier_coverage(population: dict, questions: dict) -> dict:
    from app.rag.retrieval.identifier import identifier_query

    forms: Counter = Counter()
    per_qid: dict[str, dict] = {}
    for qid in population["remaining"]:
        q = questions.get(qid)
        if q is None:
            continue
        built, meta = identifier_query(q.question)
        form = meta.get("form", "none")
        # "act-only" is the mode an act-conditional section arm would serve;
        # "act+section" a direct section filter would serve.
        if form == "act":
            mode = "act_conditional_arm_serves"
        elif form == "act+section":
            mode = "section_filter_serves"
        elif form == "section":
            mode = "section_only_serves"
        else:
            mode = "needs_decomposition"
        forms[mode] += 1
        per_qid[qid] = {
            "form": form,
            "act": meta.get("act"),
            "section": meta.get("section"),
            "built_query": built,
            "arm_mode": mode,
        }
    return {"by_arm_mode": dict(forms), "per_qid": per_qid}


# --------------------------------------------------------------------------- #
# D. Deterministic rerank head-room (offline)
# --------------------------------------------------------------------------- #
def rank_headroom(population: dict, questions: dict, payload_index: dict, family_map, arm: str) -> dict:
    """Gold's first-hit rank inside the cached arm: pool failure vs rank failure."""
    from evaluation.resolution import chunks_cover_gold

    raw = {r["question_id"]: r for r in _load_jsonl(CEILING_DIR / "raw" / f"{arm}.jsonl")}
    buckets: Counter = Counter()
    in_pool = 0
    per_qid: dict[str, dict] = {}
    for qid in population["remaining"]:
        q = questions.get(qid)
        rec = raw.get(qid)
        if q is None:
            continue
        pool_size = len(rec.get("chunk_ids") or []) if rec else 0
        cover = chunks_cover_gold(list((rec or {}).get("chunk_ids") or []), q.recall_units(), payload_index, family_map)
        unit_ranks = cover["units"]
        best = min((r["rank"] for r in unit_ranks.values() if r.get("rank") is not None), default=None)
        if best is None:
            buckets["gold_absent_from_pool"] += 1
        elif best <= 10:
            buckets["gold_in_top10"] += 1
            in_pool += 1
        elif best <= 20:
            buckets["gold_11_20"] += 1
        elif best <= 50:
            buckets["gold_21_50"] += 1
        else:
            buckets["gold_below_50"] += 1
        per_qid[qid] = {
            "best_gold_rank": best,
            "pool_size": pool_size,
            "unit_ranks": {k: v.get("rank") for k, v in unit_ranks.items()},
        }
    return {"arm": arm, "by_rank_bucket": dict(buckets), "n_with_gold_in_pool": in_pool, "per_qid": per_qid}


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 0 diagnosis for the remaining 78 failures (no LLM calls)")
    ap.add_argument("--offline", action="store_true", help="skip the live Qdrant audit (sections B/C/D only)")
    ap.add_argument("--skip-rank", action="store_true", help="skip section D (gold rank in the cached arm)")
    ap.add_argument("--out", default=str(OUT_FILE))
    args = ap.parse_args()

    from app import create_app
    from evaluation.benchmark import load_questions
    from evaluation.resolution import FamilyMap
    from app.rag.collections import DOMAIN_COLLECTIONS

    app = create_app()
    with app.app_context():
        from evaluation.bench_p0_1_packing import _load_payload_index

        family_map = FamilyMap()
        questions = {q.question_id: q for q in load_questions()}
        population = load_failure_population()
        payload_index = _load_payload_index()

        report: dict = {
            "population": {k: v for k, v in population.items() if k != "per_qid"},
            "payload_mirror_points": len(payload_index),
        }
        logger.info(
            "population: %d model_wrong, %d already eliminated, %d remaining (%d gip + %d retrieval-side)",
            report["population"]["model_wrong_total"],
            len(population["eliminated"]),
            population["remaining_n"],
            len(population["remaining_gip"]),
            len(population["retrieval_side"]),
        )

        # --- Sections B, C, D: fully offline, always run ------------------- #
        ceiling = structural_ceiling(population, questions, payload_index, family_map)
        report["structural_ceiling"] = ceiling
        logger.info("B. structural ceiling by gold unit: %s", ceiling["by_unit_reachable"])
        logger.info(
            "B. qids with ALL units reachable by a section filter: %d / %d",
            ceiling["qids_all_units_reachable"],
            population["remaining_n"],
        )

        coverage = identifier_coverage(population, questions)
        report["identifier_coverage"] = coverage
        logger.info("C. identifier coverage by arm mode: %s", coverage["by_arm_mode"])

        if not args.skip_rank:
            headroom = rank_headroom(population, questions, payload_index, family_map, "C_hybrid")
            report["rank_headroom"] = headroom
            logger.info("D. gold rank buckets in cached C_hybrid: %s", headroom["by_rank_bucket"])

        # --- Section A: live, read-only, gated behind --offline ------------ #
        if not args.offline:
            collections = sorted(set(DOMAIN_COLLECTIONS.values()))
            from app.shared.config import cfg

            collections.append(cfg.qdrant_collection)
            collections = sorted(set(collections))
            logger.info("A. auditing live payload indexes on %s", collections)
            report["live_index_audit"] = audit_live_payload_indexes(collections)
            for coll, entry in report["live_index_audit"].items():
                logger.info(
                    "A. %s: section_number_indexed=%s missing=%d probe=%s",
                    coll,
                    entry.get("section_number_indexed"),
                    len(entry.get("missing_from_schema", [])),
                    entry.get("filter_probe", {}).get("ok"),
                )
        else:
            report["live_index_audit"] = {"skipped": "--offline"}

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("wrote %s", path)

    sys.stdout.write("\n" + "=" * 72 + "\n")
    sys.stdout.write("PHASE 0 DIAGNOSIS - no LLM calls\n")
    sys.stdout.write("=" * 72 + "\n")
    sys.stdout.write(f"remaining failures to address : {population['remaining_n']}\n")
    sys.stdout.write(f"  gold-in-prompt slice         : {len(population['remaining_gip'])}\n")
    sys.stdout.write(f"  retrieval-side slice         : {len(population['retrieval_side'])}\n")
    sys.stdout.write("\n")
    sys.stdout.write("B. structural ceiling (would a section/act filter reach the gold?)\n")
    for k, v in sorted(ceiling["by_unit_reachable"].items(), key=lambda kv: -kv[1]):
        sys.stdout.write(f"   {k:<34} {v:>4} gold units\n")
    sys.stdout.write(
        f"   -> qids with ALL units reachable : {ceiling['qids_all_units_reachable']} / {population['remaining_n']}\n"
    )
    sys.stdout.write(f"   -> qids partially reachable      : {len(ceiling['qids_partially_reachable'])}\n")
    sys.stdout.write(f"   -> qids none reachable           : {len(ceiling['qids_none_reachable'])}\n")
    sys.stdout.write("\n")
    sys.stdout.write("C. query-side identifier coverage (what a structural arm could key on)\n")
    for k, v in sorted(coverage["by_arm_mode"].items(), key=lambda kv: -kv[1]):
        sys.stdout.write(f"   {k:<34} {v:>4} qids\n")
    sys.stdout.write("\n")
    if "rank_headroom" in report:
        sys.stdout.write("D. gold rank inside the cached C_hybrid arm\n")
        for k, v in sorted(report["rank_headroom"]["by_rank_bucket"].items(), key=lambda kv: -kv[1]):
            sys.stdout.write(f"   {k:<34} {v:>4} qids\n")
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

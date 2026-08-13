"""Mine (gold, hard-negative) training pairs for the legal cross-encoder.

Reads the frozen multi-route candidate caches (V5 arms + identifier/route
caches, all offline) and, per benchmark question, extracts:

    positives  — pool chunks that cover a relevant gold unit (matches_gold,
                  sections_covered-aware)
    negatives  — pool chunks that match NO gold unit, preferring same-family
                  chunks with a different section_number and high retrieval
                  rank (the natural hard-negative source the V5 report
                  identified: "gold units vs same-family wrong-section chunks")

Output: evaluation/out/cache/ce_training_pairs.jsonl — one JSON object per
question with ``query``, ``positives``/``negatives`` lists of {id, text},
plus ``gold_units``.  A companion stats JSON reports pool/positive/negative
counts and the per-question negative budget actually used.

Deterministic: reads only frozen caches (no live Qdrant / Neo4j / LLM).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

RAW = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "raw"
ROUTES = PROJECT_ROOT / "evaluation" / "out" / "cache" / "v5_routes"
CACHE = PROJECT_ROOT / "evaluation" / "out" / "cache"
OUT = CACHE / "ce_training_pairs.jsonl"
STATS = CACHE / "ce_training_stats.json"

DEPTH = 500

#: Arms + routes whose chunk_ids form the multi-route candidate pool.
ARM_FILES = ["A_dense", "B_sparse", "C_hybrid", "O_dense", "O_sparse", "X_exact"]
ROUTE_NAMES = ["C_identifier", "E_document", "F_identifier_only", "G_concept",
               "H_authority_action", "I_provision_type", "J_parent"]

#: Max negatives per question; same-family negatives are always preferred.
MAX_NEGATIVES = 8

#: Max positives per question (pool is in rank order, so the first hits are
#: the highest-ranked gold chunks — the ones the reranker must learn to
#: promote).  Keeps the pair set balanced and rank-focused for CE training.
MAX_POSITIVES = 8


def load_jsonl(path: Path) -> list[dict]:
    out = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    return out


def main() -> int:
    from evaluation.benchmark import load_questions
    from evaluation.resolution import FamilyMap, matches_gold

    # payload index
    payload: dict[str, dict] = {}
    with open(CACHE / "payload_index.jsonl", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            payload[str(rec["id"])] = rec["payload"]

    # arm + route caches keyed by question_id
    arms: dict[str, dict[str, dict]] = {}
    for name in ARM_FILES:
        recs = {r["question_id"]: r for r in load_jsonl(RAW / f"{name}.jsonl")}
        arms[name] = recs
    routes: dict[str, dict[str, dict]] = {}
    for name in ROUTE_NAMES:
        routes[f"q_{name}"] = {r["key"]: r for r in load_jsonl(ROUTES / f"q_{name}.jsonl")}

    fm = FamilyMap()
    questions = load_questions()
    records = []
    stats = Counter()
    total_pos = total_neg = 0

    for q in questions:
        qid = q.question_id
        rel = q.relevant_units()
        if not rel:
            continue
        # union pool (dedup, preserve order)
        pool: list[str] = []
        seen: set[str] = set()
        for source in list(arms.values()):
            rec = source.get(qid)
            if not rec:
                continue
            for cid in rec.get("chunk_ids", [])[:DEPTH]:
                cid = str(cid)
                if cid not in seen:
                    seen.add(cid)
                    pool.append(cid)
        for rname, rcache in routes.items():
            rec = rcache.get(qid)
            if not rec:
                continue
            for cid in rec.get("chunk_ids", [])[:DEPTH]:
                cid = str(cid)
                if cid not in seen:
                    seen.add(cid)
                    pool.append(cid)

        # positives: chunks covering any relevant gold unit
        positives = []
        matched_units = set()
        for cid in pool:
            pl = payload.get(cid)
            if pl is None:
                continue
            for u in rel:
                if matches_gold(pl, u, fm):
                    positives.append({"id": cid, "text": str(pl.get("chunk_text", ""))[:1500]})
                    matched_units.add(u.provision_id)
                    break

        # hard negatives: same-family wrong-section chunks first, then any
        # high-rank non-gold chunk; never a chunk that covers gold
        gold_fams = {u.family for u in rel}
        gold_sections = {u.section for u in rel if u.section}
        neg_scores: list[tuple[int, str, dict]] = []  # (rank_penalty, id, payload)
        for rank, cid in enumerate(pool):
            pl = payload.get(cid)
            if pl is None:
                continue
            if any(matches_gold(pl, u, fm) for u in rel):
                continue
            fams = set(fm.family_s_for_act(str(pl.get("act_name") or pl.get("document_title") or "")))
            same_fam = bool(fams & gold_fams)
            sec = str(pl.get("section_number") or "").strip()
            # same family + different section => strongest hard negative
            # (penalty 0), same family same section (unlikely after the gold
            # check) => weak, no-family => weak
            penalty = 2 if not same_fam else (1 if sec and sec in gold_sections else 0)
            neg_scores.append((penalty, rank, pl))
        neg_scores.sort(key=lambda x: (x[0], x[1]))
        negatives = [
            {"id": cid, "text": str(pl.get("chunk_text", ""))[:1500]}
            for cid, _rank, pl in neg_scores[:MAX_NEGATIVES]
        ]
        # Rank-focused positive sample: the first MAX_POSITIVES gold chunks
        # in pool order (highest-ranked first).
        positives = positives[:MAX_POSITIVES]

        if not positives:
            stats["questions_no_positives"] += 1
        records.append({
            "question_id": qid,
            "query": q.question,
            "gold_units": [u.provision_id for u in rel],
            "matched_units": sorted(matched_units),
            "positives": positives,
            "negatives": negatives,
        })
        total_pos += len(positives)
        total_neg += len(negatives)

    with open(OUT, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    summary = {
        "questions": len(records),
        "questions_with_positives": len([r for r in records if r["positives"]]),
        "total_positive_pairs": total_pos,
        "total_negative_pairs": total_neg,
        "ratio": round(total_neg / max(total_pos, 1), 2),
        "notes": (
            "negatives: same-family wrong-section pool chunks preferred, "
            "then high-rank non-gold chunks; all from frozen multi-route pool @500"
        ),
    }
    STATS.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

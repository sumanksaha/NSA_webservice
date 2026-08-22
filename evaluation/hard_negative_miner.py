"""Hard-negative mining pipeline — mine K=500 candidate pools for legal CE training.

For every benchmark question, retrieves K=500 candidates and identifies:
  - Gold chunks (positives) via matches_gold
  - Hard negatives ranked by legal similarity to the gold provision

Negative selection prioritises:
  1. Same Act, same section family (adjacent sections)
  2. Same Act, different section
  3. Same legal domain, similar terminology
  4. High semantic score but wrong provision (CE decoys)

Output: evaluation/out/cache/hard_negative_mining.jsonl — per-question
records with positives, three-tier negatives, and failure classifications.

Supports two modes:
  • Live mode (default): re-retrieves through the production pipeline
  • Offline mode (--offline): uses frozen K=500 caches if available

Usage:
    python -m evaluation.hard_negative_miner
    python -m evaluation.hard_negative_miner --offline
    python -m evaluation.hard_negative_miner --top-k 500 --max-negatives 20
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

OUT_DIR = PROJECT_ROOT / "evaluation" / "out"
CACHE_DIR = OUT_DIR / "cache"
CEILING_V5 = OUT_DIR / "ceiling_v5"
CHECKPOINT = CEILING_V5 / "hard_negative_mining.jsonl"
STATS_FILE = CACHE_DIR / "hard_negative_mining_stats.json"

# --------------------------------------------------------------------------- #
# Tier thresholds (Section 9 of the master plan)
# --------------------------------------------------------------------------- #
# Tier 1: Random negatives — control baseline
# Tier 2: Semantic hard negatives — same topic/wording/Act
# Tier 3: Adversarial legal negatives — same Act/chapter/section family


def _stop_words() -> set[str]:
    return {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "to",
        "in",
        "for",
        "under",
        "what",
        "which",
        "who",
        "how",
        "is",
        "are",
        "does",
        "do",
        "be",
        "by",
        "on",
        "at",
        "with",
        "from",
        "as",
        "that",
        "this",
        "its",
        "it",
        "not",
        "shall",
        "may",
        "act",
        "section",
        "sec",
        "rule",
        "order",
        "regulation",
    }


def word_overlap(a: str, b: str) -> float:
    """Jaccard-like word overlap between two texts."""
    stop = _stop_words()
    wa = {w for w in re.findall(r"[a-z0-9]+", str(a).lower()) if w not in stop}
    wb = {w for w in re.findall(r"[a-z0-9]+", str(b).lower()) if w not in stop}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def section_proximity(gold_sec: str | None, neg_sec: str | None) -> float:
    """1.0 for same section, decreasing for adjacent sections."""
    if not gold_sec or not neg_sec:
        return 0.0
    try:
        g, n = int(gold_sec), int(neg_sec)
        diff = abs(g - n)
        if diff == 0:
            return 1.0
        if diff <= 2:
            return 0.7
        if diff <= 5:
            return 0.4
        return 0.1
    except ValueError:
        return 0.0


def legal_similarity_score(
    gold_payload: dict,
    neg_payload: dict,
    family_map,
    gold_unit,
) -> dict[str, float]:
    """Compute legal similarity features between a gold chunk and a negative.

    Returns a dict of feature scores used for tier assignment and ranking.
    """
    from evaluation.resolution import norm_section

    gold_sec = norm_section(gold_payload.get("section_number"))
    neg_sec = norm_section(neg_payload.get("section_number"))

    # Family match
    neg_fams = set(
        family_map.family_s_for_act(str(neg_payload.get("act_name") or neg_payload.get("document_title") or ""))
    )
    same_family = gold_unit.family in neg_fams
    same_section = bool(gold_sec and neg_sec and gold_sec == neg_sec)
    sec_prox = section_proximity(gold_sec, neg_sec)

    # Text similarity
    gold_text = str(gold_payload.get("chunk_text", "") or "")
    neg_text = str(neg_payload.get("chunk_text", "") or "")
    word_sim = word_overlap(gold_text, neg_text)

    # Document-level overlap
    same_doc = str(gold_payload.get("document_title", "")).lower() == str(neg_payload.get("document_title", "")).lower()

    # Subsection overlap (G5: only meaningful ANDed with same_section — a
    # standalone match is noise because values repeat across sections).
    gold_sub = str(gold_payload.get("subsection", "") or "")
    neg_sub = str(neg_payload.get("subsection", "") or "")
    same_subsection = bool(gold_sub and neg_sub and gold_sub == neg_sub)

    # Regulation clause-number overlap (G6, 2026-08-17): the dotted clause
    # number (``2.4.15``) is the identity of regulation fragments that have
    # NO section_number.  ``same_clause`` gives tier-3 a regulation-level
    # discriminator without forcing Act sections onto regulations.
    gold_clause = str(gold_payload.get("clause_number", "") or "")
    neg_clause = str(neg_payload.get("clause_number", "") or "")
    same_clause = bool(gold_clause and neg_clause and gold_clause == neg_clause)

    # Authority match
    gold_auth = str(gold_payload.get("authority", "")).lower()
    neg_auth = str(neg_payload.get("authority", "")).lower()
    same_authority = bool(gold_auth and neg_auth and gold_auth == neg_auth)

    return {
        "same_family": float(same_family),
        "same_section": float(same_section),
        "section_proximity": sec_prox,
        "word_overlap": word_sim,
        "same_document": float(same_doc),
        "same_subsection": float(same_subsection),
        "same_clause": float(same_clause),
        "same_authority": float(same_authority),
    }


def assign_tier(features: dict[str, float]) -> int:
    """Assign a negative to a tier based on legal similarity features.

    Tier 1 (random): no meaningful legal similarity
    Tier 2 (semantic hard): same family OR high word overlap
    Tier 3 (adversarial): same section family, adjacent sections, high similarity
    """
    # Tier 3: same family + same section OR section proximity >= 0.7
    if features["same_family"] and (features["same_section"] or features["section_proximity"] >= 0.7):
        return 3
    # Tier 3 also: same family + same subsection (section-anchored; the
    # subsection value alone is never enough — G5) OR same family + same
    # dotted clause number (regulation fragments with no section — G6).
    if features["same_family"] and features["same_subsection"] and features["same_section"]:
        return 3
    if features["same_family"] and features["same_clause"]:
        return 3
    # Tier 2: same family OR high word overlap (>= 0.3)
    if features["same_family"] or features["word_overlap"] >= 0.3:
        return 2
    # Tier 2: same document
    if features["same_document"]:
        return 2
    # Tier 1: everything else
    return 1


def hard_negative_rank(features: dict[str, float], pool_rank: int) -> float:
    """Composite score for ranking negatives by difficulty.

    Higher = harder (more likely to confuse the reranker).
    """
    return (
        3.0 * features["same_family"]
        + 2.0 * features["same_section"]
        + 1.5 * features["section_proximity"]
        + 1.0 * features["same_subsection"]
        + 1.0 * features["same_clause"]
        + 0.5 * features["word_overlap"]
        + 0.3 * features["same_document"]
        - 0.001 * pool_rank  # slight preference for higher-ranked negatives
    )


def mine_question(
    q,
    raw_chunks: list,  # RetrievedChunk objects or dicts
    payload_index: dict[str, dict],
    family_map,
    max_negatives: int = 20,
    subsection_filter: bool = False,
) -> dict | None:
    """Mine hard negatives for a single question from its K=500 pool.

    Args:
        q: benchmark question
        raw_chunks: retrieved candidate chunks (K=500 pool)
        payload_index: chunk_id → payload map
        family_map: FamilyMap instance
        max_negatives: max negatives kept per question
        subsection_filter: P2 (G5): keep only negatives that share the gold's
            section AND subsection (never subsection alone — values repeat
            across sections).  Falls back to same-section-different-subsection
            negatives when no AND-match exists (fssai has 33% subsection
            coverage), preserving tier-3 recall.

    Returns a dict with query, positives, tiered negatives, and metadata.
    Returns None if no gold provision is resolvable.
    """
    from evaluation.resolution import matches_gold

    rel = q.relevant_units()
    if not rel:
        return None

    # Resolve gold chunks in the pool
    positives = []
    negative_candidates = []

    for rank, chunk in enumerate(raw_chunks):
        # Get chunk_id — works for both RetrievedChunk and dict
        cid = chunk.chunk_id if hasattr(chunk, "chunk_id") else chunk.get("chunk_id", "")
        payload = payload_index.get(cid)
        if payload is None:
            # Try to build a minimal payload from the chunk itself
            payload = {}
            if hasattr(chunk, "text"):
                payload = {
                    "chunk_text": chunk.text,
                    "section_number": getattr(chunk, "section_number", None),
                    "act_name": getattr(chunk, "act_name", ""),
                    "document_title": getattr(chunk, "document_title", ""),
                }
            elif isinstance(chunk, dict):
                payload = {
                    "chunk_text": chunk.get("text", ""),
                    "section_number": chunk.get("section_number"),
                    "act_name": chunk.get("act_name", ""),
                    "document_title": chunk.get("document_title", ""),
                }

        # Check if this chunk covers any gold unit
        covers_gold = False
        for unit in rel:
            if matches_gold(payload, unit, family_map):
                positives.append({
                    "chunk_id": cid,
                    "text": str(payload.get("chunk_text", ""))[:1500],
                    "rank": rank,
                    "gold_unit": unit.provision_id,
                    "section": payload.get("section_number"),
                    "act_name": payload.get("act_name", ""),
                })
                covers_gold = True
                break

        if not covers_gold:
            negative_candidates.append({
                "chunk_id": cid,
                "payload": payload,
                "rank": rank,
            })

    if not positives:
        return None

    # For each gold unit, find the best hard negatives
    all_negatives = []
    seen_ids: set[str] = set()

    for unit in rel:
        gold_payload = {}
        for pos in positives:
            if pos["gold_unit"] == unit.provision_id:
                gold_payload = payload_index.get(pos["chunk_id"], pos.get("text", {}))
                if isinstance(gold_payload, str):
                    gold_payload = {"chunk_text": gold_payload}
                break

        for neg in negative_candidates:
            cid = neg["chunk_id"]
            if cid in seen_ids:
                continue
            features = legal_similarity_score(gold_payload, neg["payload"], family_map, unit)
            tier = assign_tier(features)
            score = hard_negative_rank(features, neg["rank"])

            all_negatives.append({
                "chunk_id": cid,
                "text": str(neg["payload"].get("chunk_text", ""))[:1500],
                "rank": neg["rank"],
                "tier": tier,
                "score": score,
                "features": features,
                "gold_unit": unit.provision_id,
                "section": neg["payload"].get("section_number"),
                "act_name": neg["payload"].get("act_name", ""),
                "document_title": neg["payload"].get("document_title", ""),
            })
            seen_ids.add(cid)

    # P2 subsection filter (G5): same_section AND same_subsection, with a
    # same-section-only fallback when no AND-match exists per gold unit.
    if subsection_filter:
        kept: list[dict] = []
        for unit in rel:
            unit_negs = [n for n in all_negatives if n["gold_unit"] == unit.provision_id]
            and_matches = [
                n for n in unit_negs if n["features"].get("same_section") and n["features"].get("same_subsection")
            ]
            if and_matches:
                kept.extend(and_matches)
            else:
                # Fallback: same-section-different-chunk (no subsection match
                # required) — fssai has 32.6% subsection coverage.
                kept.extend([n for n in unit_negs if n["features"].get("same_section")])
        all_negatives = kept

    # Sort by difficulty score, take top max_negatives
    all_negatives.sort(key=lambda x: x["score"], reverse=True)

    # Ensure tier diversity: at least some from each tier
    tier_counts = {1: 0, 2: 0, 3: 0}
    selected = []
    # First pass: take top from each tier
    for neg in all_negatives:
        t = neg["tier"]
        if tier_counts[t] < max_negatives // 3 + 2:
            selected.append(neg)
            tier_counts[t] += 1
            if len(selected) >= max_negatives:
                break
    # Second pass: fill remaining with best overall
    if len(selected) < max_negatives:
        remaining_ids = {n["chunk_id"] for n in selected}
        for neg in all_negatives:
            if neg["chunk_id"] not in remaining_ids:
                selected.append(neg)
                if len(selected) >= max_negatives:
                    break

    # Tier summary
    tier_summary = {1: [], 2: [], 3: []}
    for neg in selected:
        tier_summary[neg["tier"]].append(neg["chunk_id"])

    return {
        "question_id": q.question_id,
        "query": q.question,
        "gold_units": [u.provision_id for u in rel],
        "pool_size": len(raw_chunks),
        "positives": positives,
        "negatives": selected,
        "tier_distribution": {
            "tier_1_random": len(tier_summary[1]),
            "tier_2_semantic": len(tier_summary[2]),
            "tier_3_adversarial": len(tier_summary[3]),
        },
    }


def load_checkpoint() -> dict[str, dict]:
    """Load existing mining checkpoint."""
    done: dict[str, dict] = {}
    if CHECKPOINT.exists():
        with open(CHECKPOINT, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    done[rec["question_id"]] = rec
    return done


def mine_live(
    questions: list,
    payload_index: dict,
    family_map,
    top_k: int = 500,
    max_neg: int = 20,
    subsection_filter: bool = False,
) -> dict[str, dict]:
    """Mine via live Qdrant retrieval at K=top_k."""
    from app import create_app
    from app.rag.retrieval import (
        DenseRetriever,
        HybridRetriever,
        QueryClassifier,
        QueryParser,
        SparseRetriever,
    )
    from app.rag.retrieval.identifier import identifier_query

    # Bound torch threads before the app import (which pulls in torch models).
    from evaluation.ranking_loss_trainer import configure_threads

    configure_threads()

    app = create_app()
    results = {}
    with app.app_context():
        classifier = QueryClassifier()
        parser = QueryParser()
        dense_cache: dict[str, DenseRetriever] = {}
        sparse_cache: dict[str, SparseRetriever] = {}

        def get_hybrid(collection: str) -> HybridRetriever:
            if collection not in dense_cache:
                from app.rag.retrieval.factory import build_dense_retriever

                dense_cache[collection] = build_dense_retriever(collection)
            if collection not in sparse_cache:
                from app.rag.retrieval.factory import build_sparse_retriever

                sparse_cache[collection] = build_sparse_retriever(collection)
            return HybridRetriever(dense=dense_cache[collection], sparse=sparse_cache[collection], reranker=None)

        for i, q in enumerate(questions):
            collection = (q.collections or ["fssai_legal_768"])[0]
            try:
                hybrid = get_hybrid(collection)
                qtype = classifier.classify(q.question)
                parsed = parser.parse(q.question, qtype) or {}
                ident_q, _meta = identifier_query(q.question)
                result = hybrid.retrieve(q.question, top_k=top_k, filters=parsed, identifier_query=ident_q)
                mined = mine_question(
                    q,
                    result.chunks,
                    payload_index,
                    family_map,
                    max_neg,
                    subsection_filter=subsection_filter,
                )
                if mined:
                    results[q.question_id] = mined
            except Exception:
                continue
            if (i + 1) % 10 == 0:
                pass
    return results


def mine_offline(
    questions: list,
    payload_index: dict,
    family_map,
    top_k: int = 500,
    max_neg: int = 20,
    subsection_filter: bool = False,
) -> dict[str, dict]:
    """Mine from the payload index only (no live Qdrant).

    For each question:
    1. Find all payload points covering each gold unit (positives).
    2. Find same-family chunks as hard negatives, scored by legal similarity.
    3. Assign negatives to three tiers (random / semantic / adversarial).

    This is the offline equivalent of mine_live — it works entirely from
    the cached payload index and gold registry, so it can run without
    Qdrant access (e.g. when another experiment is using the cluster).

    Pre-builds a family→chunk index for O(1) per-question lookups.
    """
    from evaluation.resolution import payload_to_keys

    # Pre-build family index: family -> list of (pid, payload) for fast lookup
    family_index: dict[str, list[tuple[str, dict]]] = {}
    # Pre-build positive index: (family, section) -> list of (pid, payload)
    positive_index: dict[tuple[str, str | None], list[tuple[str, dict]]] = {}
    for pid, payload in payload_index.items():
        source = str(payload.get("act_name") or payload.get("document_title") or "")
        for fam in family_map.family_s_for_act(source):
            family_index.setdefault(fam, []).append((pid, payload))
        # Index by (family, section) for fast gold matching
        for fam, sec in payload_to_keys(payload, family_map):
            positive_index.setdefault((fam, sec), []).append((pid, payload))

    results = {}
    n_done = 0
    for q in questions:
        rel = q.relevant_units()
        if not rel:
            continue

        # Find all payload points covering each gold unit via the pre-built index
        positives = []
        seen_pos: set[str] = set()
        for unit in rel:
            lookup_key = (unit.family, unit.section)
            for pid, payload in positive_index.get(lookup_key, []):
                if pid in seen_pos:
                    continue
                seen_pos.add(pid)
                positives.append({
                    "chunk_id": pid,
                    "text": str(payload.get("chunk_text", ""))[:1500],
                    "rank": -1,
                    "gold_unit": unit.provision_id,
                    "section": payload.get("section_number"),
                    "act_name": payload.get("act_name", ""),
                })
                if len(positives) >= 8:
                    break

        if not positives:
            continue

        # Find hard negatives from the same family via the pre-built index
        gold_fams = {u.family for u in rel}
        primary_unit = rel[0]
        primary_payload = None
        for pos in positives:
            if pos["gold_unit"] == primary_unit.provision_id:
                primary_payload = payload_index.get(pos["chunk_id"], {})
                break
        if primary_payload is None:
            primary_payload = {}

        negatives = []
        seen_ids: set[str] = set(pos["chunk_id"] for pos in positives)

        # Collect candidates only from relevant families
        candidates: list[tuple[str, dict]] = []
        for fam in gold_fams:
            candidates.extend(family_index.get(fam, []))

        for pid, payload in candidates:
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            features = legal_similarity_score(primary_payload, payload, family_map, primary_unit)
            tier = assign_tier(features)
            score = hard_negative_rank(features, 0)

            negatives.append({
                "chunk_id": pid,
                "text": str(payload.get("chunk_text", ""))[:1500],
                "rank": -1,
                "tier": tier,
                "score": score,
                "features": features,
                "gold_unit": primary_unit.provision_id,
                "section": payload.get("section_number"),
                "act_name": payload.get("act_name", ""),
                "document_title": payload.get("document_title", ""),
            })

        negatives.sort(key=lambda x: x["score"], reverse=True)

        # P2 subsection filter (G5) — same logic as mine_question: same_section
        # AND same_subsection, falling back to same-section-only.
        if subsection_filter:
            and_matches = [
                n for n in negatives if n["features"].get("same_section") and n["features"].get("same_subsection")
            ]
            if and_matches:
                negatives = and_matches
            else:
                negatives = [n for n in negatives if n["features"].get("same_section")]

        # Ensure tier diversity: take top from each tier first, then fill
        tier_counts = {1: 0, 2: 0, 3: 0}
        selected = []
        for neg in negatives:
            t = neg["tier"]
            if tier_counts[t] < max_neg // 3 + 2:
                selected.append(neg)
                tier_counts[t] += 1
                if len(selected) >= max_neg:
                    break
        if len(selected) < max_neg:
            selected_ids = {n["chunk_id"] for n in selected}
            for neg in negatives:
                if neg["chunk_id"] not in selected_ids:
                    selected.append(neg)
                    if len(selected) >= max_neg:
                        break

        tier_summary = {1: 0, 2: 0, 3: 0}
        for neg in selected:
            tier_summary[neg["tier"]] += 1

        results[q.question_id] = {
            "question_id": q.question_id,
            "query": q.question,
            "gold_units": [u.provision_id for u in rel],
            "pool_size": len(payload_index),
            "positives": positives[:8],
            "negatives": selected,
            "tier_distribution": {
                "tier_1_random": tier_summary[1],
                "tier_2_semantic": tier_summary[2],
                "tier_3_adversarial": tier_summary[3],
            },
        }
        n_done += 1
        if n_done % 30 == 0:
            pass

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Hard-negative mining pipeline")
    parser.add_argument("--offline", action="store_true", help="Use frozen caches instead of live Qdrant")
    parser.add_argument("--top-k", type=int, default=500, help="Retrieval depth per question")
    parser.add_argument("--max-negatives", type=int, default=20, help="Max negatives per question")
    parser.add_argument(
        "--threads",
        type=int,
        default=4,
        help="Torch intra-op thread cap for live mode (default 4; offline mode is pure Python)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Only mine this many questions (testing/spot checks on a laptop)"
    )
    parser.add_argument(
        "--subsection-filter",
        action="store_true",
        help="P2 (G5): keep only same_section AND same_subsection negatives "
        "(fallback: same-section-only when no AND-match exists).",
    )
    args = parser.parse_args()

    # Bound threads before any torch import (live mode pulls in app modules).
    from evaluation.ranking_loss_trainer import configure_threads

    configure_threads(args.threads)

    from evaluation.benchmark import load_questions
    from evaluation.resolution import FamilyMap

    if args.offline:
        # Load payload index directly from cache (bypass Qdrant rebuild)
        pi_path = CACHE_DIR / "payload_index.jsonl"
        payload_index = {}
        if pi_path.exists():
            with open(pi_path, encoding="utf-8") as f:
                for line in f:
                    rec = json.loads(line)
                    payload_index[str(rec["id"])] = rec["payload"]
        else:
            from evaluation.report_ceiling import load_payload_index

            payload_index = load_payload_index()
    else:
        from evaluation.report_ceiling import load_payload_index

        payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = load_questions()

    done = load_checkpoint()
    todo = [q for q in questions if q.question_id not in done]
    if args.limit:
        todo = todo[: args.limit]

    if args.offline:
        results = mine_offline(
            todo,
            payload_index,
            family_map,
            args.top_k,
            args.max_negatives,
            subsection_filter=args.subsection_filter,
        )
    else:
        results = mine_live(
            todo,
            payload_index,
            family_map,
            args.top_k,
            args.max_negatives,
            subsection_filter=args.subsection_filter,
        )

    # Merge and write
    done.update(results)
    with open(CHECKPOINT, "w", encoding="utf-8") as f:
        for rec in done.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Stats
    total_pos = sum(len(r["positives"]) for r in done.values())
    total_neg = sum(len(r["negatives"]) for r in done.values())
    tier_dist = {1: 0, 2: 0, 3: 0}
    for r in done.values():
        for n in r["negatives"]:
            tier_dist[n["tier"]] = tier_dist.get(n["tier"], 0) + 1

    stats = {
        "questions": len(done),
        "total_positives": total_pos,
        "total_negatives": total_neg,
        "tier_distribution": {
            "tier_1_random": tier_dist.get(1, 0),
            "tier_2_semantic": tier_dist.get(2, 0),
            "tier_3_adversarial": tier_dist.get(3, 0),
        },
        "avg_negatives_per_question": round(total_neg / max(len(done), 1), 1),
        "mode": "offline" if args.offline else "live",
    }
    STATS_FILE.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

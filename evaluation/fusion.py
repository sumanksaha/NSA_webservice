"""Candidate-fusion repair experiment (offline — 2026-08-12).

Root-cause finding: the hybrid+KG arms tail-concatenate KG provisions AFTER
the top-20 vector chunks, so Recall@K<=20 / MRR / nDCG structurally cannot
credit KG evidence (H14/H2).  This module REPAIRS the fusion at the rank
level: it RRF-fuses the *cached* dense (A), sparse (B) and KG (D/E) results
into one interleaved ranked candidate list per question and scores it with
the standard metric pipeline.

No corpus, embedding, KG or benchmark change — and no re-retrieval: the
experiment re-ranks candidates the frozen arms already produced, so the only
variable is the fusion method.

Arms produced (written to ``evaluation/out/raw/<arm>.jsonl``):
    C_rrf_sanity     RRF(dense, sparse)                — offline check vs live C
    G_ds_kg_rrf      RRF(dense, sparse, KG contract)   — repaired full fusion
    E_ds_kg_rrf      RRF(dense, sparse, KG expansion)  — repaired E
    H_dense_kg_rrf   RRF(dense, KG contract)           — KG on top of dense alone

Each record carries ``fused_items`` (pre-ranked candidates) so
``evaluation.metrics.build_ranked_items`` scores the true fused ranking.

Usage:
    python -m evaluation.fusion [--force]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("eval.fusion")

#: Offline fusion arms -> (input arm names for dense/sparse/KG lists).
#: The optional 4th element is ``dedupe_kg`` — drop KG items whose
#: (family, section) is already covered by a chunk item before fusing, so a
#: redundant graph provision cannot occupy a fused top-k slot.
FUSION_ARM_DEFS: dict[str, tuple[str, str, str | None, bool]] = {
    # arm -> (dense_raw, sparse_raw, kg_raw, dedupe_kg) — kg_raw None = no KG list
    "C_rrf_sanity": ("A_dense", "B_sparse", None, False),
    "G_ds_kg_rrf": ("A_dense", "B_sparse", "D_kg_retrieval", False),
    "E_ds_kg_rrf": ("A_dense", "B_sparse", "E_dense_sparse_kg", False),
    "H_dense_kg_rrf": ("A_dense", None, "D_kg_retrieval", False),
    # Deduplicated variants (2026-08-12): KG provisions redundant with the
    # vector top-k are dropped before RRF, freeing slots for novel candidates.
    "G_ds_kg_rrf_dedup": ("A_dense", "B_sparse", "D_kg_retrieval", True),
    "H_dense_kg_rrf_dedup": ("A_dense", None, "D_kg_retrieval", True),
}

RRF_K = 60.0
FUSED_TOP_K = 20


def dedupe_kg_items(chunk_items: list, kg_items: list) -> list:
    """Drop KG items whose ``(family, section)`` a chunk item already covers.

    A graph provision that merely re-surfaces what the vector top-k already
    returned is redundant — keeping it in the fused list only wastes a slot
    a novel candidate could fill.  Returns a new KG list (inputs untouched).
    """
    covered: set[tuple] = {(i.family, i.section) for i in chunk_items}
    if not covered:
        return kg_items
    return [i for i in kg_items if (i.family, i.section) not in covered]


def rrf_fuse_items(*item_lists, rrf_k: float = RRF_K, top_k: int = FUSED_TOP_K) -> list:
    """Reciprocal-Rank-Fuse ranked item lists into one ranked list.

    Items are keyed by ``(kind, key, family, section)``; an item present in
    several lists accumulates RRF credit (agreement boost), and the fused
    order is by total score descending.  Items are returned in fused order.
    """
    from evaluation.metrics import RankedItem

    scores: dict[tuple, float] = {}
    first: dict[tuple, RankedItem] = {}
    for items in item_lists:
        for rank, item in enumerate(items):
            key = (item.kind, item.key, item.family, item.section)
            scores[key] = scores.get(key, 0.0) + 1.0 / (rank + 1 + rrf_k)
            first.setdefault(key, item)
    ordered = sorted(scores, key=scores.get, reverse=True)[:top_k]  # type: ignore[arg-type]
    return [first[k] for k in ordered]


def item_to_dict(item) -> dict:
    return {
        "kind": item.kind,
        "key": item.key,
        "family": item.family,
        "section": item.section,
    }


def build_fused_arm(
    arm: str,
    dense_rec: dict,
    sparse_rec: dict | None,
    kg_rec: dict | None,
    payload_index: dict,
    family_map,
    top_k: int = FUSED_TOP_K,
) -> dict:
    """Build one fused-arm record from the cached dense/sparse/kg results."""
    from evaluation.metrics import build_ranked_items

    dense_items = build_ranked_items(dense_rec, payload_index, family_map) if dense_rec else []
    sparse_items = build_ranked_items(sparse_rec, payload_index, family_map) if sparse_rec else []
    kg_items = build_ranked_items(kg_rec, payload_index, family_map) if kg_rec else []

    if arm == "C_rrf_sanity":
        fused = rrf_fuse_items(dense_items, sparse_items, top_k=top_k)
        kg_provisions = []
        kg_source = None
        retriever = "rrf(dense,sparse)"
    elif arm in ("G_ds_kg_rrf", "G_ds_kg_rrf_dedup"):
        if arm.endswith("_dedup"):
            kg_items = dedupe_kg_items(dense_items + sparse_items, kg_items)
        fused = rrf_fuse_items(dense_items, sparse_items, kg_items, top_k=top_k)
        kg_provisions = (kg_rec or {}).get("kg_provisions", [])
        kg_source = "contract"
        retriever = "rrf(dense,sparse,kg)" + ("+dedup" if arm.endswith("_dedup") else "")
    elif arm == "E_ds_kg_rrf":
        fused = rrf_fuse_items(dense_items, sparse_items, kg_items, top_k=top_k)
        kg_provisions = (kg_rec or {}).get("kg_provisions", [])
        kg_source = "expansion"
        retriever = "rrf(dense,sparse,kgexp)"
    elif arm in ("H_dense_kg_rrf", "H_dense_kg_rrf_dedup"):
        if arm.endswith("_dedup"):
            kg_items = dedupe_kg_items(dense_items, kg_items)
        fused = rrf_fuse_items(dense_items, kg_items, top_k=top_k)
        kg_provisions = (kg_rec or {}).get("kg_provisions", [])
        kg_source = "contract"
        retriever = "rrf(dense,kg)" + ("+dedup" if arm.endswith("_dedup") else "")
    else:  # pragma: no cover - registry-bound
        raise ValueError(f"unknown fusion arm: {arm}")

    return {
        "chunk_ids": [i.key for i in fused if i.kind == "chunk"],
        "kg_provisions": kg_provisions,
        "kg_source": kg_source,
        "fused_items": [item_to_dict(i) for i in fused],
        "latency_ms": 0,
        "error": None,
        "retriever": retriever,
    }


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from evaluation.config import CACHE_DIR, RAW_DIR, OUT_DIR
    from evaluation.benchmark import load_questions
    from evaluation.resolution import FamilyMap, build_payload_index
    from evaluation.report import load_raw

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    with app.app_context():
        collections = [
            app.config.get("RAG_QDRANT_COLLECTION", "fssai_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_ENV", "env_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_COMMERCIAL", "commercial_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_ANIMAL", "animal_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_WB_STATE", "wb_state_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_CRIMINAL", "criminal_legal_768"),
        ]
        payload_index = build_payload_index(
            lambda coll: _store(coll), list(dict.fromkeys(collections)), force=args.force
        )
        family_map = FamilyMap()
        questions = load_questions()

        cached = {a: load_raw(a) for a in ("A_dense", "B_sparse", "D_kg_retrieval", "E_dense_sparse_kg")}
        for a, recs in cached.items():
            if not recs:
                logger.error("missing cached arm %s — run evaluation.run_retrieval first", a)
                return 1
            logger.info("cached %s: %d questions", a, len(recs))

        for arm, (dense_arm, sparse_arm, kg_arm, _dedupe) in FUSION_ARM_DEFS.items():
            out_path = RAW_DIR / f"{arm}.jsonl"
            n = 0
            with open(out_path, "w", encoding="utf-8") as out:
                for q in questions:
                    dense_rec = cached[dense_arm].get(q.question_id) if dense_arm else None
                    sparse_rec = cached[sparse_arm].get(q.question_id) if sparse_arm else None
                    kg_rec = cached[kg_arm].get(q.question_id) if kg_arm else None
                    rec = build_fused_arm(
                        arm, dense_rec, sparse_rec, kg_rec, payload_index, family_map
                    )
                    rec["arm"] = arm
                    rec["question_id"] = q.question_id
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n += 1
            logger.info("fusion arm %s written: %d questions", arm, n)

    logger.info("fusion phase done")
    return 0


def _store(collection: str):
    from app.rag.qdrant_client import QdrantStore

    return QdrantStore(collection_name=collection)


if __name__ == "__main__":
    raise SystemExit(main())

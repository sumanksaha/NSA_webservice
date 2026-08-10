"""Live probe: BM25 sparse vectors (fastembed) against the real Qdrant cluster.

Uses a throwaway ``bench_sparse_probe`` collection (created with the sparse
vector config, dropped at the end) so the production ``fssai_legal_768`` index
is never touched.  Verifies the real qdrant-client models path:
ensure_collection(sparse_enabled=True) -> upsert named dense+sparse vectors ->
dense search (using=dense) -> sparse BM25 search -> server-side hybrid
prefetch + Fusion.RRF.

Run:  python scripts/_probe_sparse_bm25.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402
from app.rag.qdrant_client import QdrantStore  # noqa: E402
from app.rag.sparse_embedding import SparseEmbeddingService  # noqa: E402

COLLECTION = "bench_sparse_probe"

D1 = "Food Safety and Standards (Alcoholic Beverages) Regulations, 2018: no alcoholic beverage shall be sold without a licence."
D2 = "The Food Authority shall specify standards for contaminants, toxins and residues in food products under Section 16."
Q = "alcoholic beverage licence"


def main() -> int:
    failures = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        status = "OK  " if ok else "FAIL"
        print(f"[{status}] {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            failures.append(name)

    app = create_app()

    # Config (RAG_QDRANT_URL, RAG_SPARSE_MODEL, ...) is read from
    # current_app at call time — run all checks inside an app context.
    with app.app_context():
        store = QdrantStore(collection_name=COLLECTION)
        sparse = SparseEmbeddingService()

        # 0. fastembed BM25 availability (downloads the model on first use).
        check("fastembed BM25 available", sparse.is_available(), f"model={sparse.model_name}")
        if not sparse.is_available():
            check("sparse embed", False, "cannot continue without fastembed")
            _drop(store)
            return 1

        # 1. Create the collection WITH the sparse vector (mirrors ingestion).
        check("ensure_collection(sparse_enabled=True)", store.ensure_collection(create_payload_indexes=False, sparse_enabled=True))
        check("has_sparse_vectors()", store.has_sparse_vectors())
        if not store.has_sparse_vectors():
            _drop(store)
            return 1

        # 2. Upsert named dense + sparse vectors.  Qdrant point IDs must be
        # UUIDs or unsigned integers (the real pipeline uses uuid4 chunk_ids).
        import uuid

        from app.rag.qdrant_client import Point

        sparse_vectors = sparse.embed_batch([D1, D2])
        dense = [[0.1] * 768, [0.2] * 768]
        points = [
            Point(id=str(uuid.uuid4()), vector=dense[0], sparse_vector=sparse_vectors[0], payload={"document_id": "d1", "chunk_text": D1, "document_type": "regulation"}),
            Point(id=str(uuid.uuid4()), vector=dense[1], sparse_vector=sparse_vectors[1], payload={"document_id": "d2", "chunk_text": D2, "document_type": "act"}),
        ]
        try:
            n = store.upsert_points(points)
            check("upsert named vectors", n == 2, f"{n} points")
        except Exception as exc:
            check("upsert named vectors", False, str(exc))
            _drop(store)
            return 1

        # 3. Dense search on a named-vector collection (using=dense).
        try:
            hits = store.search_points([0.1] * 768, top_k=2)
            check("dense search (named vectors)", len(hits) >= 1, f"{len(hits)} hits")
        except Exception as exc:
            check("dense search (named vectors)", False, str(exc))

        # 4. Sparse BM25 search.
        try:
            q_sparse = sparse.embed_sparse(Q)
            hits = store.search_sparse(q_sparse, top_k=2)
            check("sparse BM25 search", len(hits) >= 1, f"{len(hits)} hits, top={hits[0]['id'] if hits else '-'}")
        except Exception as exc:
            check("sparse BM25 search", False, str(exc))

        # 5. Server-side hybrid fusion (prefetch + RRF).
        try:
            hits = store.hybrid_search([0.1] * 768, q_sparse, top_k=2)
            check("hybrid prefetch+RREF search", len(hits) >= 1, f"{len(hits)} fused hits")
        except Exception as exc:
            check("hybrid prefetch+RREF search", False, str(exc))

        _drop(store)
    print()
    print(f"VERDICT: {'ALL CHECKS PASSED' if not failures else f'{len(failures)} FAILURES: {failures}'}")
    return 0 if not failures else 1


def _drop(store: QdrantStore) -> None:
    try:
        client = store._get_client()
        if client is not None:
            client.delete_collection(COLLECTION)
            print("[cleanup] dropped bench_sparse_probe")
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup] drop failed: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())

"""Drop existing Qdrant collection, re-create, and ingest all PDFs with full enrichment.

Run:  python scripts/_reindex_full_enrichment.py [corpus_dir]

Default corpus_dir: FSSAI_rules documents
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")

    corpus_dir = Path(argv[0]) if argv else Path("FSSAI_rules documents")

    from app import create_app
    from app.rag.ingestion import make_ingestion_pipeline
    from app.rag.qdrant_client import QdrantStore

    app = create_app()

    with app.app_context():
        # 1. Drop the existing collection
        store = QdrantStore()
        print(f"Collection: {store.collection_name}", flush=True)

        from qdrant_client import QdrantClient
        client = QdrantClient(
            url=app.config["RAG_QDRANT_URL"],
            api_key=app.config["RAG_QDRANT_API_KEY"] or None,
        )
        if client.collection_exists(store.collection_name):
            print("Dropping existing collection...", flush=True)
            client.delete_collection(store.collection_name)
            print("Collection dropped.", flush=True)

        # 2. Re-create with sparse-vector schema via ensure_collection
        print("Creating collection with sparse vectors...", flush=True)
        store.ensure_collection(create_payload_indexes=True, sparse_enabled=True)
        assert client.collection_exists(store.collection_name), "Collection creation failed!"
        info = client.get_collection(store.collection_name)
        print(f"Collection created: {info.points_count} points, sparse={bool(info.config.params.sparse_vectors)}", flush=True)

        # 3. Build pipeline with full enrichment
        print(f"\nBuilding pipeline with RAG_FULL_ENRICHMENT={app.config.get('RAG_FULL_ENRICHMENT')}", flush=True)
        pipeline = make_ingestion_pipeline(full_enrichment=True)

        # 4. Ingest all PDFs
        files = sorted(p for p in corpus_dir.glob("*.pdf") if p.is_file())
        print(f"Ingesting {len(files)} PDFs...\n", flush=True)

        total_files = 0
        total_ok = 0
        total_chunks = 0
        start = time.monotonic()

        for path in files:
            t0 = time.monotonic()
            try:
                res = pipeline.ingest_file(
                    path,
                    document={"document_uri": str(path)},
                )
                dt = time.monotonic() - t0
                total_files += 1
                total_ok += 1 if res.ok else 0
                total_chunks += res.chunk_count
                print(
                    f"  {dt:6.1f}s  {'OK' if res.ok else 'FAIL':4s}  "
                    f"chunks={res.chunk_count:5d}  dup={res.duplicate_chunks:3d}  "
                    f"pts={res.points_upserted:5d}  {path.name}",
                    flush=True,
                )
                if res.errors:
                    for e in res.errors:
                        print(f"         ERROR: {e}", flush=True)
            except Exception as exc:
                dt = time.monotonic() - t0
                total_files += 1
                print(f"  {dt:6.1f}s  FAIL  {path.name}: {exc}", flush=True)

        total_elapsed = time.monotonic() - start
        print(f"\n--- DONE: {total_files} files, {total_ok} OK, {total_chunks} total chunks in {total_elapsed:.0f}s ---", flush=True)

        # 5. Final verification
        info = client.get_collection(store.collection_name)
        print(f"Final collection: {info.points_count} points", flush=True)
        print(f"Sparse vectors: {bool(info.config.params.sparse_vectors)}", flush=True)

        # Scroll a few points to check enrichment fields
        pts = store.scroll_points(limit=3)
        for p in pts:
            payload = p["payload"]
            enrichment_fields = {
                "quality_score": payload.get("quality_score"),
                "document_classification": payload.get("document_classification"),
                "document_authority": payload.get("document_authority"),
                "citations": len(payload.get("citations", [])),
                "references": len(payload.get("references", [])),
                "entities": len(payload.get("entities", [])),
            }
            print(f"  Enrichment: {enrichment_fields}", flush=True)

    return 0 if total_ok == total_files else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] if len(sys.argv) > 1 else None))
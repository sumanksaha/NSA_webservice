"""Agent A §6.2 integration tests — delete + re-index after content changes.

``test_reindexing.py`` covers the reindex lifecycle: a document is ingested,
removed from Qdrant (by document id or by individual chunk ids), then
re-indexed — either a full rebuild (fresh dedup state, simulating an
operator re-index) or a *content-change* reindex (the file's text changed, so
the new SHA-256 fingerprint is not a duplicate and the new chunks replace
the old ones).

The store is backed by the same in-memory Qdrant client double used by
``test_corpus_ingestion_e2e.py``, so deletion and search verification run
through the public ``QdrantStore`` API with no optional dependencies.
"""

from __future__ import annotations

from test_corpus_ingestion_e2e import FakeEmbedder, InMemoryQdrantClient

from app.rag.dedup import ChunkDeduper
from app.rag.ingestion import IngestionPipeline
from app.rag.qdrant_client import QdrantStore
from app.rag.qdrant_indexer import QdrantIndexer

_DIM = 768

_DOC_V1 = (
    "The Food Safety and Standards Act, 2006\n\n"
    "Section 3\n\n"
    "3(1) The Food Authority shall ensure food safety and standards.\n\n"
    "Section 14\n\n"
    "14(1) The Central Government may make rules for the purposes of this Act.\n\n"
)

# Same structure, changed wording — a content update, not a duplicate.
_DOC_V2 = (
    "The Food Safety and Standards Act, 2006\n\n"
    "Section 3\n\n"
    "3(1) The Food Authority shall ensure food safety and standards.\n\n"
    "Section 14\n\n"
    "14(1) The Central Government may make rules to carry out the provisions of this Act.\n\n"
)


def _setup() -> tuple[IngestionPipeline, QdrantStore, QdrantIndexer]:
    client = InMemoryQdrantClient()
    store = QdrantStore(client=client, collection_name="fssai_legal_768", vector_size=_DIM)
    indexer = QdrantIndexer(store=store, embedder=FakeEmbedder(), chunker=None)
    pipeline = IngestionPipeline(indexer=indexer)
    return pipeline, store, indexer


def _point_ids(store: QdrantStore) -> list[str]:
    return [p["id"] for p in store.scroll_points(limit=1000)]


def _query_hits(store: QdrantStore, text: str, top_k: int = 5) -> list[dict]:
    return store.search_points(FakeEmbedder().embed_text(text), top_k=top_k)


class TestReindexing:
    def test_delete_document_then_full_reindex(self):
        """Remove all of a document's chunks, then rebuild from scratch."""
        pipeline, store, indexer = _setup()
        first = pipeline.ingest_text(_DOC_V1, {"document_id": "doc-1"})
        assert first.ok
        assert len(_point_ids(store)) == first.chunk_count

        # Remove the whole document from Qdrant.
        removed = indexer.remove_document("doc-1")
        assert removed == 1  # one targeted filter-delete operation
        assert _point_ids(store) == []

        # Full rebuild with a FRESH deduper (operator re-index): the same text
        # is re-embedded and searchable again.
        fresh_pipeline, fresh_store, _ = _setup()
        second = fresh_pipeline.ingest_text(_DOC_V1, {"document_id": "doc-1"})
        assert second.ok
        assert second.duplicate is False
        assert len(_point_ids(fresh_store)) == second.chunk_count
        assert _query_hits(fresh_store, "The Food Authority shall ensure food safety and standards.")

    def test_remove_individual_chunks_by_id(self):
        """remove_chunks deletes only the targeted points."""
        pipeline, store, indexer = _setup()
        result = pipeline.ingest_text(_DOC_V1, {"document_id": "doc-1"})
        assert result.ok
        ids = _point_ids(store)
        assert len(ids) == result.chunk_count

        indexer.remove_chunks(ids[:2])
        remaining = _point_ids(store)
        assert len(remaining) == result.chunk_count - 2
        assert set(remaining).isdisjoint(set(ids[:2]))

    def test_content_change_replaces_old_chunks(self):
        """Delete + re-index a changed document: v1 chunks are fully replaced."""
        pipeline, store, indexer = _setup()

        first = pipeline.ingest_text(_DOC_V1, {"document_id": "doc-1"})
        assert first.ok
        assert len(_point_ids(store)) == first.chunk_count

        # The file changed on disk: remove all v1 chunks, then re-index the
        # new content with a fresh dedup state (full rebuild of the document).
        indexer.remove_document("doc-1")
        fresh_pipeline = IngestionPipeline(indexer=indexer, deduper=ChunkDeduper())
        second = fresh_pipeline.ingest_text(_DOC_V2, {"document_id": "doc-1"})
        assert second.ok
        assert second.duplicate is False
        assert second.points_upserted == second.chunk_count

        # Same section structure -> same chunk count, but ALL points are new
        # (the changed content replaced the old version; nothing stale remains).
        new_ids = set(_point_ids(store))
        assert len(new_ids) == first.chunk_count

        # The changed 14(1) wording is now searchable on top for its own text.
        hits = _query_hits(store, "14(1) The Central Government may make rules to carry out the provisions of this Act.")
        assert hits
        top = hits[0]
        assert top["payload"]["document_id"] == "doc-1"
        assert "carry out the provisions" in top["payload"]["chunk_text"]

        # The v1 wording no longer exists anywhere in the index.
        v1_hits = _query_hits(store, "14(1) The Central Government may make rules for the purposes of this Act.")
        assert not any("for the purposes of this Act" in h["payload"]["chunk_text"] for h in v1_hits)


# End of test_reindexing.py

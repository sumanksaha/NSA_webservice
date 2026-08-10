"""Tests for the Agent A Phase 1 dedup module (app/rag/dedup.py).

Pins the SHA-256 content-hash contract (Day 5): stable, whitespace-insensitive
document fingerprints, and document/chunk-level dedup with payload
``content_hash`` stamping.  Fully self-contained — no Qdrant or DB required.
"""

from __future__ import annotations

import hashlib

from app.rag.chunker import Chunk
from app.rag.dedup import ChunkDeduper, ContentHasher, MemoryHashStore, normalize_for_hash


def _make_chunk(index, text):
    return Chunk(
        chunk_id=f"c{index}",
        document_id="doc-1",
        chunk_index=index,
        chunk_text=text,
    )


class TestNormalizeForHash:
    def test_collapses_whitespace_runs(self):
        assert normalize_for_hash("Section  55\n\tof the\nAct.") == "Section 55 of the Act."

    def test_strips_edges(self):
        assert normalize_for_hash("  text  ") == "text"


class TestContentHasher:
    def test_compute_returns_sha256_hexdigest(self):
        digest = ContentHasher.compute("Section 55 of the Act")
        assert len(digest) == 64
        assert digest == hashlib.sha256(b"Section 55 of the Act").hexdigest()

    def test_compute_is_whitespace_insensitive(self):
        # Cosmetic formatting must not defeat dedup.
        assert ContentHasher.compute("Section  55\nof the Act") == ContentHasher.compute("Section 55 of the Act")

    def test_compute_differs_for_different_text(self):
        assert ContentHasher.compute("Section 55") != ContentHasher.compute("Section 56")

    def test_compute_handles_empty(self):
        assert ContentHasher.compute("") == hashlib.sha256(b"").hexdigest()


class TestChunkDeduper:
    def test_document_hash_and_duplicate_check(self):
        deduper = ChunkDeduper()
        assert deduper.is_duplicate_document("some legal text") is False
        file_hash = deduper.document_hash("some legal text")
        assert len(file_hash) == 64
        deduper.record(content_hashes=[file_hash])
        assert deduper.is_duplicate_document("some legal text") is True

    def test_filter_new_returns_all_when_unseen(self):
        deduper = ChunkDeduper()
        chunks = [_make_chunk(0, "alpha"), _make_chunk(1, "beta")]
        new_chunks, duplicates = deduper.filter_new(chunks)
        assert len(new_chunks) == 2
        assert duplicates == []
        # Every new chunk is stamped with its content_hash.
        for chunk in new_chunks:
            assert len(chunk.content_hash) == 64
        assert deduper.chunk_hash(chunks[0]) == chunks[0].content_hash

    def test_filter_new_skips_seen_hashes(self):
        deduper = ChunkDeduper()
        first_doc = [_make_chunk(0, "same"), _make_chunk(1, "same"), _make_chunk(2, "uniqueA")]
        new_chunks, duplicates = deduper.filter_new(first_doc)
        assert len(new_chunks) == 3  # first occurrence of every hash is new
        assert duplicates == []
        # The pipeline records hashes AFTER a successful upsert.
        deduper.record(chunks=new_chunks)
        # A second document shares a chunk with the first.
        second_doc = [_make_chunk(3, "same"), _make_chunk(4, "uniqueB")]
        new_chunks, duplicates = deduper.filter_new(second_doc)
        assert len(new_chunks) == 1
        assert new_chunks[0].chunk_text == "uniqueB"
        assert len(duplicates) == 1

    def test_record_marks_chunk_hashes_as_seen(self):
        deduper = ChunkDeduper()
        chunks = [_make_chunk(0, "alpha")]
        assert deduper.is_duplicate_document("alpha") is False
        deduper.record(chunks=chunks)
        # Recording a chunk registers its hash in the store.
        assert deduper.store.contains(deduper.chunk_hash(chunks[0])) is True

    def test_memory_store_len(self):
        store = MemoryHashStore()
        store.add("a")
        store.add_many(["b", "c"])
        assert len(store) == 3

    def test_injected_hasher_and_store(self):
        store = MemoryHashStore(initial={"known"})
        deduper = ChunkDeduper(store=store)
        assert deduper.store is store
        assert store.contains("known") is True

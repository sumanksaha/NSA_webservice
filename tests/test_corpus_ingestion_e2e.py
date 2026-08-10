"""Agent A §6.2 integration tests — full pipeline from raw document to Qdrant.

``test_corpus_ingestion_e2e.py`` drives the complete Day 4 flow — real
``DocumentLoaderFactory`` (txt) -> real ``DocumentCleaner`` -> real legal
paragraph engine chunking -> fake embedder -> ``QdrantStore`` backed by an
in-memory Qdrant client double — and then **verifies the index**: points are
searchable, an exact-text query returns the ingested chunk on top, payload
filters work, dedup skips re-ingests, and enrichment adapters stamp the
payloads.

This complements ``test_ingestion_pipeline.py`` (which pins the pipeline's
sync-call contract) with the §6.2 "verification" surface: everything the
pipeline writes into the store can be read back out via the public
``QdrantStore`` API.

The in-memory client implements only what ``QdrantStore`` calls
(``ping`` / ``collection_exists`` / ``create_collection`` /
``create_payload_index`` / ``upsert`` / ``search`` / ``delete`` / ``scroll``)
so no optional dependency (``qdrant-client``) is needed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np

from app.rag.dedup import ChunkDeduper
from app.rag.ingestion import IngestionPipeline, ingest_corpus_dir
from app.rag.qdrant_client import QdrantStore
from app.rag.qdrant_indexer import QdrantIndexer

_SAMPLE_TEXT = (
    "The Food Safety and Standards Act, 2006\n\n"
    "Section 3\n\n"
    "3(1)(a) The Food Authority shall ensure food safety and standards.\n"
    "3(1)(b) The Food Authority shall coordinate with the State authorities.\n\n"
    "Section 14\n\n"
    "14(1) The Central Government may make rules for the purposes of this Act.\n\n"
)

_DIM = 768


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


def _matches(payload: dict[str, Any], flt: Any) -> bool:
    """Apply a Qdrant filter (plain dict OR qdrant-client model object).

    ``QdrantStore.delete_points`` builds ``FilterSelector`` / ``PointIdsList``
    model objects when ``qdrant-client`` is installed and plain dicts when it
    is absent — this helper accepts either shape so the test double works in
    both environments.
    """
    must = getattr(flt, "must", None)
    if must is None and isinstance(flt, dict):
        must = (flt or {}).get("must", [])
    for clause in must or []:
        key = getattr(clause, "key", None)
        if key is None and isinstance(clause, dict):
            key = clause.get("key")
        match = getattr(clause, "match", None)
        value = getattr(match, "value", None) if match is not None else None
        if value is None and isinstance(clause, dict):
            value = (clause.get("match") or {}).get("value")
        if payload.get(key) != value:
            return False
    return True


class InMemoryQdrantClient:
    """Minimal QdrantClient double: upsert/search/delete/scroll in memory.

    Search ranks by cosine similarity so the round-trip assertion ("the
    chunk we upserted comes back on top for its own text") is real.
    """

    def __init__(self) -> None:
        self._points: dict[str, tuple[list[float], dict[str, Any]]] = {}
        self._collections: set[str] = set()
        self.created_collections: list[str] = []

    # -- health / collection management ------------------------------------ #
    def ping(self) -> bool:
        return True

    def collection_exists(self, collection_name: str) -> bool:
        return collection_name in self._collections

    def create_collection(self, collection_name: str, vectors_config: Any = None) -> None:
        self._collections.add(collection_name)
        self.created_collections.append(collection_name)

    def create_payload_index(self, **kwargs: Any) -> bool:
        return True

    # -- points ------------------------------------------------------------- #
    def upsert(self, collection_name: str, points: list[Any]) -> None:
        for point in points:
            self._points[point.id] = (list(point.vector), dict(point.payload))

    def delete(self, collection_name: str, points_selector: Any) -> int:
        if hasattr(points_selector, "points"):  # PointIdsList model object
            ids = set(str(p) for p in points_selector.points)
        elif isinstance(points_selector, dict) and "points" in points_selector:
            ids = set(points_selector["points"])
        elif hasattr(points_selector, "filter"):  # FilterSelector model object
            ids = {pid for pid, (_v, pl) in self._points.items() if _matches(pl, points_selector.filter)}
        elif isinstance(points_selector, dict) and "filter" in points_selector:
            ids = {pid for pid, (_v, pl) in self._points.items() if _matches(pl, points_selector["filter"])}
        else:
            return 0
        for pid in list(ids):
            self._points.pop(pid, None)
        return len(ids)

    def search(
        self,
        collection_name: str,
        query_vector: list[float],
        limit: int = 10,
        with_payload: bool = True,
        with_vectors: bool = False,
        **kwargs: Any,
    ) -> list[Any]:
        # Honour the payload filter QdrantStore passes as query_filter/
        # search_filter (plain dict shape from ``_build_filter``).
        flt = kwargs.get("query_filter") or kwargs.get("search_filter")
        qv = np.asarray(query_vector, dtype="float64")
        qnorm = float(np.linalg.norm(qv)) or 1.0
        scored: list[tuple[float, str, dict[str, Any]]] = []
        for pid, (vec, payload) in self._points.items():
            if flt and not _matches(payload, flt):
                continue
            v = np.asarray(vec, dtype="float64")
            score = float(np.dot(qv, v) / (qnorm * (float(np.linalg.norm(v)) or 1.0)))
            scored.append((score, pid, payload))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            SimpleNamespace(id=pid, score=score, payload=payload)
            for score, pid, payload in scored[:limit]
        ]

    def scroll(
        self,
        collection_name: str,
        limit: int = 100,
        with_payload: bool = True,
        with_vectors: bool = False,
        **kwargs: Any,
    ) -> tuple[list[Any], Any]:
        records = [SimpleNamespace(id=pid, payload=pl) for pid, (_v, pl) in self._points.items()]
        return records[:limit], None


def _vector_for(text: str, dim: int = _DIM) -> list[float]:
    """Deterministic normalized pseudo-vector seeded by the text (idempotent)."""
    seed = abs(hash(text)) % (2**32)
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(dim).astype("float32")
    vec /= float(np.linalg.norm(vec)) or 1.0
    return vec.tolist()


class FakeEmbedder:
    """Embedder whose vectors are a deterministic function of the text."""

    vector_size = _DIM

    def embed_chunks(self, chunks):
        return [_vector_for(c.chunk_text) for c in chunks]

    def embed_text(self, text: str) -> list[float]:
        return _vector_for(text)

    def validate_vector_size(self, expected=None) -> bool:
        return True


def _make_pipeline(client: InMemoryQdrantClient, **pipeline_kwargs: Any) -> tuple[IngestionPipeline, QdrantStore, QdrantIndexer]:
    store = QdrantStore(client=client, collection_name="fssai_legal_768", vector_size=_DIM)
    indexer = QdrantIndexer(store=store, embedder=FakeEmbedder(), chunker=None)
    return IngestionPipeline(indexer=indexer, **pipeline_kwargs), store, indexer


def _all_points(store: QdrantStore) -> list[dict[str, Any]]:
    """Read every point back through the public scroll API."""
    return store.scroll_points(limit=1000)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


class TestFullPipelineRoundTrip:
    def test_raw_txt_to_searchable_qdrant(self, tmp_path):
        """Real loader -> cleaner -> engine -> embed -> store, verified by search."""
        f = tmp_path / "fss_act.txt"
        f.write_text(_SAMPLE_TEXT, encoding="utf-8")
        client = InMemoryQdrantClient()
        pipeline, store, _indexer = _make_pipeline(client)

        result = pipeline.ingest_file(str(f), {"type": "act"})
        assert result.ok
        assert result.chunk_count > 0
        assert result.points_upserted == result.chunk_count
        assert result.file_type == "txt"
        assert result.source_uri == str(f)

        # Everything that was ingested is readable back (round-trip).
        points = _all_points(store)
        assert len(points) == result.chunk_count
        assert {p["payload"]["document_id"] for p in points} == {result.document_id}

        # An exact-text query (the chunk's own text embedded) returns that
        # chunk on top with near-perfect similarity.
        target = points[0]
        qvec = FakeEmbedder().embed_text(target["payload"]["chunk_text"])
        hits = store.search_points(qvec, top_k=3)
        assert hits, "search returned no results"
        assert hits[0]["id"] == target["id"]
        assert hits[0]["score"] > 0.99
        assert hits[0]["payload"]["chunk_text"] == target["payload"]["chunk_text"]

    def test_search_filter_by_document_id(self, tmp_path):
        """Payload filtering isolates one document's chunks."""
        f = tmp_path / "doc.txt"
        f.write_text(_SAMPLE_TEXT, encoding="utf-8")
        client = InMemoryQdrantClient()
        pipeline, store, _indexer = _make_pipeline(client)
        result = pipeline.ingest_file(str(f), {"document_id": "doc-alpha", "type": "act"})
        assert result.ok

        hits = store.search_points(
            FakeEmbedder().embed_text("Food Authority"),
            top_k=10,
            filters={"document_id": "doc-alpha"},
        )
        assert len(hits) == result.chunk_count  # every chunk belongs to doc-alpha
        assert all(h["payload"]["document_id"] == "doc-alpha" for h in hits)

        # A filter matching nothing returns no results.
        none_hits = store.search_points(
            FakeEmbedder().embed_text("Food Authority"),
            top_k=10,
            filters={"document_id": "doc-nope"},
        )
        assert none_hits == []

    def test_payload_schema_fields(self, tmp_path):
        """Every upserted point carries the §5.1 payload contract."""
        f = tmp_path / "schema.txt"
        f.write_text(_SAMPLE_TEXT, encoding="utf-8")
        client = InMemoryQdrantClient()
        pipeline, store, _indexer = _make_pipeline(client)
        result = pipeline.ingest_file(str(f), {"type": "act", "authority": "FSSAI"})
        assert result.ok

        required = {
            "chunk_id", "document_id", "document_uri", "document_type", "authority",
            "chunk_index", "chunk_text", "chunk_char_count", "is_current",
            "hierarchy_level", "citations", "references", "confidence",
            "created_at", "content_hash",
        }
        for point in _all_points(store):
            payload = point["payload"]
            assert required <= set(payload), f"missing §5.1 keys: {required - set(payload)}"
            assert payload["document_type"] == "act"          # caller metadata wins
            assert payload["authority"] == "FSSAI"
            assert payload["document_uri"] == str(f)
            assert len(payload["content_hash"]) == 64          # SHA-256 dedup stamp
            assert payload["chunk_index"] >= 0
            assert payload["chunk_char_count"] == len(payload["chunk_text"])


class TestCorpusDirE2E:
    def test_multi_file_corpus_indexes_and_is_searchable(self, tmp_path):
        (tmp_path / "a.txt").write_text(_SAMPLE_TEXT, encoding="utf-8")
        (tmp_path / "b.txt").write_text(
            "Food Safety and Standards (Licensing) Regulations\n\nSection 5\n\n"
            "5(1) No person shall carry on food business without a licence.\n",
            encoding="utf-8",
        )
        (tmp_path / "ignore.md").write_text("not supported", encoding="utf-8")
        client = InMemoryQdrantClient()
        pipeline, store, _indexer = _make_pipeline(client)

        summary = ingest_corpus_dir(str(tmp_path), pipeline=pipeline)
        assert summary["total"] == 2
        assert summary["indexed"] == 2
        assert summary["failed"] == 0
        assert len(summary["results"]) == 2

        docs = {p["payload"]["document_id"] for p in _all_points(store)}
        assert len(docs) == 2  # both documents' chunks present
        # Per-file progress tracking: each result reports its own counts.
        for res in summary["results"]:
            assert res["ok"] is True
            assert res["chunk_count"] > 0
            assert res["points_upserted"] == res["chunk_count"]

    def test_duplicate_file_skipped_in_batch(self, tmp_path):
        f = tmp_path / "dup.txt"
        f.write_text(_SAMPLE_TEXT, encoding="utf-8")
        client = InMemoryQdrantClient()
        pipeline, store, _indexer = _make_pipeline(client)

        first = ingest_corpus_dir(str(tmp_path), pipeline=pipeline)
        second = ingest_corpus_dir(str(tmp_path), pipeline=pipeline)  # same deduper

        assert first["indexed"] == 1
        assert second["duplicates"] == 1
        assert second["indexed"] == 0
        assert len(_all_points(store)) == first["results"][0]["chunk_count"]  # unchanged


class TestEnrichmentE2E:
    def test_production_default_classifies_document_type(self, tmp_path):
        """The Day 9 classifier (production default) stamps §5.1 document_type."""
        from app.rag.document_classifier import DocumentClassifier

        f = tmp_path / "act.txt"
        f.write_text(_SAMPLE_TEXT, encoding="utf-8")
        client = InMemoryQdrantClient()
        pipeline, store, _indexer = _make_pipeline(client, classifier=DocumentClassifier())

        result = pipeline.ingest_file(str(f), {"document_id": "doc-1"})
        assert result.ok
        types = {p["payload"]["document_type"] for p in _all_points(store)}
        assert types <= {"act", "rule", "regulation", "notification", "circular", "case_law"}
        assert types == {"act"}  # the FSS Act title classifies as an act (§2.4.1)

    def test_full_enrichment_chain_stamps_payloads(self):
        """All Phase 2 adapters + entity extraction run end-to-end."""
        from app.rag.chunk_quality import ChunkQualityValidator
        from app.rag.citation_adapter import CitationAdapter
        from app.rag.crossref_adapter import CrossRefAdapter
        from app.rag.entity_extractor import LegalEntityExtractor
        from app.rag.metadata_adapter import MetadataAdapter

        client = InMemoryQdrantClient()
        pipeline, store, _indexer = _make_pipeline(
            client,
            metadata_adapter=MetadataAdapter(),
            citation_adapter=CitationAdapter(),
            crossref_adapter=CrossRefAdapter(),
            entity_extractor=LegalEntityExtractor(),
            quality_validator=ChunkQualityValidator(),
        )
        result = pipeline.ingest_text(_SAMPLE_TEXT, {"document_id": "doc-1"})
        assert result.ok
        assert result.quality_summary is not None
        assert result.quality_summary["failed"] == 0

        payloads = [p["payload"] for p in _all_points(store)]
        assert any("Food Safety and Standards Act" in c for p in payloads for c in p["citations"])
        assert any(p["references"] for p in payloads)
        assert all(isinstance(p["entities"], list) for p in payloads)


class TestFailurePaths:
    def test_empty_document_not_indexed(self, tmp_path):
        f = tmp_path / "blank.txt"
        f.write_text("   \n \t \n", encoding="utf-8")
        client = InMemoryQdrantClient()
        pipeline, store, _indexer = _make_pipeline(client)

        result = pipeline.ingest_file(str(f), {"document_id": "doc-blank"})
        assert not result.ok
        assert any("empty after cleaning" in e for e in result.errors)
        assert _all_points(store) == []

    def test_chunk_level_dedup_across_documents(self):
        """Shared chunk text is indexed once; the second doc reuses it."""
        doc_a = "The Food Safety and Standards Act, 2006\n\nSection 3\n\n3(1) Shared provision text.\n"
        doc_b = "The Food Safety and Standards Act, 2006\n\nSection 3\n\n3(1) Shared provision text.\n3(2) New provision text.\n"

        client = InMemoryQdrantClient()
        store = QdrantStore(client=client, collection_name="fssai_legal_768", vector_size=_DIM)
        indexer = QdrantIndexer(store=store, embedder=FakeEmbedder(), chunker=None)
        deduper = ChunkDeduper()
        pipeline = IngestionPipeline(indexer=indexer, deduper=deduper)

        first = pipeline.ingest_text(doc_a, {"document_id": "doc-A"})
        second = pipeline.ingest_text(doc_b, {"document_id": "doc-B"})
        assert first.ok and second.ok
        assert second.duplicate_chunks > 0  # the shared "3(1)" chunk was seen
        assert second.points_upserted == second.chunk_count


# End of test_corpus_ingestion_e2e.py

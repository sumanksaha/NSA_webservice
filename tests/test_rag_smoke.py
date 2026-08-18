"""Smoke tests for the Agent A RAG corpus pipeline (RAG_AGENT_A_SCOPE §6.3).

The five §6.3 smoke checks, runnable without a Qdrant server or
sentence-transformers (mock-injection pattern):

1. Qdrant connection        — ``QdrantStore.ping()`` → ``True``
2. Embedding generation     — ``embed_text("Section 55 of FSS Act")`` → 768-dim
3. Chunking pipeline        — real legal engine → 3+ chunks with section numbers
4. Qdrant upsert + search   — upsert 10 chunks → search → top-3 results
5. Document classification  — FSS Act text → §5.1 ``document_type`` + authority

Chunking and classification run the REAL engines (offline-capable); Qdrant
and embedding use constructor-injected mocks so the suite passes in any
environment (matching the Day 1–2 test conventions).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.rag.document_classifier import DocumentClassifier
from app.rag.embedding_service import EmbeddingService
from app.rag.qdrant_client import Point, QdrantStore

# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class _FakeEncoder:
    """SentenceTransformer stand-in: returns a fixed 768-dim vector."""

    def encode(self, texts):
        if isinstance(texts, str):
            return [[0.05] * 768]
        return [[0.05] * 768 for _ in texts]

    def get_sentence_embedding_dimension(self):
        return 768


class _FakeQdrantClient:
    """In-memory QdrantClient stand-in with upsert + search."""

    def __init__(self, points=None):
        self._points = {p.id: p for p in (points or [])}
        self.upsert_calls = 0
        self.search_calls = []

    def ping(self):
        return True

    def collection_exists(self, name):
        return True

    def upsert(self, collection_name, points, **kwargs):
        self.upsert_calls += 1
        for struct in points:
            # Accepts dict structs (qdrant-client absent path) or objects.
            point_id = struct["id"] if isinstance(struct, dict) else struct.id
            vector = struct["vector"] if isinstance(struct, dict) else struct.vector
            payload = struct["payload"] if isinstance(struct, dict) else struct.payload
            self._points[point_id] = SimpleNamespace(id=point_id, vector=vector, payload=payload, score=0.9)

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        limit = kwargs.get("limit", 10)
        # Cosine-ish ordering: all vectors equal here, so return first ``limit``.
        return [self._points[p] for p in list(self._points)[:limit]]


# --------------------------------------------------------------------------- #
# Smoke test 1 — Qdrant connection
# --------------------------------------------------------------------------- #


class TestSmokeQdrantConnection:
    def test_ping_returns_true(self):
        store = QdrantStore(client=_FakeQdrantClient())
        assert store.ping() is True

    def test_ping_false_when_unavailable(self):
        store = QdrantStore()
        store._get_client = lambda: None  # no client -> degrades to False
        assert store.ping() is False


# --------------------------------------------------------------------------- #
# Smoke test 2 — Embedding generation
# --------------------------------------------------------------------------- #


class TestSmokeEmbedding:
    def test_embed_text_returns_768_dim_vector(self):
        service = EmbeddingService(encoder=_FakeEncoder())
        vector = service.embed_text("Section 55 of FSS Act")
        assert len(vector) == 768
        assert all(isinstance(x, float) for x in vector)

    def test_vector_size_matches_index(self):
        service = EmbeddingService(encoder=_FakeEncoder())
        assert service.vector_size == 768
        assert service.validate_vector_size(768) is True

    def test_embed_chunks_accepts_chunk_objects(self):
        from app.rag.chunker import Chunk

        service = EmbeddingService(encoder=_FakeEncoder())
        chunks = [Chunk(chunk_id="c1", document_id="d1", chunk_index=0, chunk_text="Section 55 text")]
        vectors = service.embed_chunks(chunks)
        assert len(vectors) == 1
        assert len(vectors[0]) == 768


# --------------------------------------------------------------------------- #
# Smoke test 3 — Chunking pipeline (real legal engine)
# --------------------------------------------------------------------------- #


class TestSmokeChunking:
    def test_chunks_with_section_numbers(self):
        from app.rag.chunker import Chunker
        from app.services.legal_engine import get_legal_engine

        engine = get_legal_engine()()
        chunker = Chunker(engine=engine)
        text = (
            "The Food Safety and Standards Act, 2006\n\n"
            "Section 3(1)\n\n"
            "3(1)(a) The Food Authority shall ensure food safety.\n"
            "3(1)(b) The Food Authority shall coordinate with State authorities.\n\n"
            "Section 55\n\n"
            "55(1) Penalty for non-compliance with the provisions of this Act.\n\n"
            "Section 56\n\n"
            "56(1) Offences by companies.\n"
        )
        chunks = chunker.chunk_text(text, {"document_id": "doc-1", "type": "act"})
        assert len(chunks) >= 3
        assert any(c.section_number == "3" for c in chunks)
        assert any(c.section_number == "55" for c in chunks)
        assert all(isinstance(c.hierarchy_level, int) for c in chunks)


# --------------------------------------------------------------------------- #
# Smoke test 4 — Qdrant upsert + search
# --------------------------------------------------------------------------- #


class TestSmokeUpsertSearch:
    def test_upsert_ten_search_top_three(self):
        client = _FakeQdrantClient()
        store = QdrantStore(client=client)
        points = [
            Point(
                id=f"c{i}",
                vector=[0.05] * 768,
                payload={"document_id": "doc-1", "chunk_text": f"Section {50 + i} content.", "section_number": str(50 + i)},
            )
            for i in range(10)
        ]
        assert store.upsert_points(points) == 10
        assert client.upsert_calls == 1
        results = store.search_points([0.05] * 768, top_k=3)
        assert len(client.search_calls) == 1
        assert client.search_calls[0]["limit"] == 3
        assert len(results) == 3
        assert all("payload" in r and "score" in r for r in results)


# --------------------------------------------------------------------------- #
# Smoke test 5 — Document classification (real extractors)
# --------------------------------------------------------------------------- #


class TestSmokeClassification:
    def test_fss_act_classification(self):
        classifier = DocumentClassifier()
        result = classifier.classify(
            "The Food Safety and Standards Act, 2006\n\n"
            "An Act to consolidate the laws relating to food safety and standards.\n"
            "Ministry of Health and Family Welfare, Government of India.\n"
        )
        assert result.document_type in {"act", "rule", "regulation", "notification", "circular", "case_law"}
        assert "Ministry of Health" in result.authority

    def test_payload_smoke_shape(self):
        classifier = DocumentClassifier()
        payload = classifier.payload(
            "The Food Safety and Standards Act, 2006\nMinistry of Health and Family Welfare"
        )
        assert "document_type" in payload
        assert "authority" in payload

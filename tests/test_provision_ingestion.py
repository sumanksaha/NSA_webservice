"""Tests for provision-extraction ingestion wiring (ADR-0009 §2.3).

Covers the adapter annotating chunks in place, the ``Chunk`` payload fields
round-tripping through the Qdrant indexer, the fail-closed behaviour, and the
``make_ingestion_pipeline`` flag gate.  No Qdrant, network, or embedder.
"""

from __future__ import annotations

from app.rag.chunker import Chunk
from app.rag.dedup import ChunkDeduper
from app.rag.ingestion import IngestionPipeline, make_ingestion_pipeline
from app.rag.provision_extractor import ProvisionExtractorAdapter
from app.rag.provision_extractor import adapter as adapter_module
from app.rag.qdrant_indexer import ChunkIngestionResult, QdrantIndexer

FSS_ACT = "Food Safety and Standards Act, 2006"

_TEXTS = [
    (
        "Food Safety and Standards Act, 2006\n\n"
        "26. Responsibilities of the food business operator.— Every food business operator shall "
        "ensure that the articles of food satisfy the requirements of this Act."
    ),
    ("31. Licensing and registration.— No person shall commence any food business except under a licence."),
]


def _chunks() -> list[Chunk]:
    return [Chunk(chunk_id=f"c{i}", document_id="d1", chunk_index=i, chunk_text=text) for i, text in enumerate(_TEXTS)]


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #


class TestAdapter:
    def test_annotates_chunks_in_place(self):
        chunks = _chunks()
        count = ProvisionExtractorAdapter().extract(chunks, act_name=FSS_ACT, document_title=FSS_ACT)
        assert count == 2
        assert chunks[0].provision_spans[0]["provision_id"] == "fssai:s26"
        assert chunks[0].provision_modality == "obligation"
        assert chunks[1].provision_spans[0]["provision_id"] == "fssai:s31"
        assert chunks[1].provision_modality == "prohibition"

    def test_chunk_ids_untouched(self):
        chunks = _chunks()
        ProvisionExtractorAdapter().extract(chunks, act_name=FSS_ACT, document_title=FSS_ACT)
        assert [chunk.chunk_id for chunk in chunks] == ["c0", "c1"]

    def test_fail_closed_never_raises(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("extractor exploded")

        monkeypatch.setattr(adapter_module, "isolate_chunks", _boom)
        chunks = _chunks()
        assert ProvisionExtractorAdapter().extract(chunks, act_name=FSS_ACT) == 0
        assert chunks[0].provision_spans == []


# --------------------------------------------------------------------------- #
# Payload contract
# --------------------------------------------------------------------------- #


class TestChunkPayload:
    def test_to_payload_includes_provision_fields(self):
        chunk = _chunks()[0]
        chunk.provision_spans = [{"provision_id": "fssai:s26", "section": "26", "subsection": []}]
        chunk.provision_confidence = 0.87
        chunk.provision_modality = "obligation"
        payload = chunk.to_payload()
        assert payload["provision_spans"] == [{"provision_id": "fssai:s26", "section": "26", "subsection": []}]
        assert payload["provision_confidence"] == 0.87
        assert payload["provision_modality"] == "obligation"

    def test_payload_round_trips_through_indexer(self):
        chunk = _chunks()[0]
        chunk.provision_spans = [{"provision_id": "fssai:s26", "section": "26", "subsection": []}]
        chunk.provision_confidence = 0.87
        chunk.provision_modality = "obligation"
        rebuilt = QdrantIndexer._chunk_from_payload(chunk.to_payload())
        assert rebuilt.provision_spans == chunk.provision_spans
        assert rebuilt.provision_confidence == 0.87
        assert rebuilt.provision_modality == "obligation"


# --------------------------------------------------------------------------- #
# Ingestion pipeline wiring
# --------------------------------------------------------------------------- #


class _FakeChunker:
    def __init__(self, chunks):
        self._chunks = chunks

    def chunk_text(self, text, document=None):
        return self._chunks


class _FakeIndexer:
    def __init__(self, chunks):
        self._chunks = chunks
        self.synced = []

    @property
    def chunker(self):
        return _FakeChunker(self._chunks)

    def sync_chunks(self, chunks):
        self.synced.append(list(chunks))
        return ChunkIngestionResult(document_id="d1", chunk_count=len(chunks), points_upserted=len(chunks))


class TestPipelineWiring:
    def test_pipeline_stamps_chunks_before_sync(self):
        chunks = _chunks()
        indexer = _FakeIndexer(chunks)
        pipeline = IngestionPipeline(
            indexer=indexer,
            deduper=ChunkDeduper(),
            provision_extractor=ProvisionExtractorAdapter(),
        )
        result = pipeline.ingest_text(
            "irrelevant",
            {"document_id": "d1", "act_name": FSS_ACT, "title": FSS_ACT, "pre_cleaned": True},
        )
        assert result.points_upserted == 2
        synced = indexer.synced[0]
        assert synced[0].provision_spans[0]["provision_id"] == "fssai:s26"
        assert synced[1].provision_modality == "prohibition"

    def test_make_ingestion_pipeline_wires_adapter_when_enabled(self, monkeypatch):
        monkeypatch.setenv("PROVISION_EXTRACTOR_ENABLED", "true")
        pipeline = make_ingestion_pipeline()
        assert isinstance(pipeline._provision_extractor, ProvisionExtractorAdapter)

    def test_make_ingestion_pipeline_omits_adapter_when_disabled(self, monkeypatch):
        monkeypatch.setenv("PROVISION_EXTRACTOR_ENABLED", "false")
        pipeline = make_ingestion_pipeline()
        assert pipeline._provision_extractor is None

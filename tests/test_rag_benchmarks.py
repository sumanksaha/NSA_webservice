"""Tests for the RAG performance benchmark harness (Phase 3 — Day 13).

``scripts/benchmark_rag.py`` is a custom timing harness (no pytest-benchmark
dependency) measuring chunking / embedding / vector-store throughput.  These
tests pin the harness itself with the established mock-injection pattern: fake
chunker/encoder/store produce deterministic metrics, real components are used
where they are cheap (the legal paragraph engine), and nothing requires
``qdrant-client`` / ``sentence-transformers`` / a live cluster.
"""

from __future__ import annotations

import json

import scripts.benchmark_rag as bench
from app.rag.chunker import Chunk
from app.rag.qdrant_client import Point, QdrantStore

from test_corpus_ingestion_e2e import InMemoryQdrantClient

_SAMPLE = (
    "The Food Safety and Standards Act, 2006\n\n"
    "Section 3\n\n"
    "3(1)(a) The Food Authority shall ensure food safety and standards.\n"
    "3(1)(b) The Food Authority shall coordinate with the State authorities.\n\n"
)


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class _FakeChunker:
    def __init__(self, chunks=None):
        self._chunks = chunks or [
            Chunk(chunk_id="c0", document_id="bench", chunk_index=0, chunk_text="alpha section"),
            Chunk(chunk_id="c1", document_id="bench", chunk_index=1, chunk_text="beta section"),
        ]

    def chunk_text(self, text, document=None):
        return list(self._chunks)


class _SlowChunker(_FakeChunker):
    """Chunker with an artificial delay so per-run timings are measurable."""

    def chunk_text(self, text, document=None):
        import time

        time.sleep(0.01)
        return super().chunk_text(text, document)


class _CountingEncoder:
    """Encoder double: 768-dim deterministic vectors, records call count."""

    vector_size = 768

    def __init__(self, dim: int = 768):
        self._dim = dim
        self.calls = 0

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, texts):
        import numpy as np

        self.calls += 1
        rng = np.random.default_rng(7)
        return rng.standard_normal((len(list(texts)), self._dim)).astype("float32")


class _NoStore:
    """Store double whose collection cannot be ensured (unconfigured Qdrant)."""

    collection_name = "fssai_legal_768"
    vector_size = 768

    def ensure_collection(self, create_payload_indexes=True):
        raise RuntimeError("Qdrant is unavailable: RAG_QDRANT_URL not configured")


# --------------------------------------------------------------------------- #
# Sample text
# --------------------------------------------------------------------------- #


class TestSampleText:
    def test_build_sample_text_size_and_structure(self):
        text = bench.build_sample_text(5_000)
        assert len(text) >= 5_000
        assert "Section 1" in text
        assert "Food Safety and Standards Act, 2006" in text
        assert text.count("Section") >= 5

    def test_split_into_buckets(self):
        buckets = bench._split_into(_SAMPLE, 4)
        assert len(buckets) <= 4
        assert all(isinstance(b, str) and b for b in buckets)
        # Round trip: joining the buckets recovers every word.
        assert " ".join(" ".join(b.split()) for b in buckets) == " ".join(_SAMPLE.split())


# --------------------------------------------------------------------------- #
# Section benchmarks
# --------------------------------------------------------------------------- #


class TestMeasureChunking:
    def test_fake_chunker_returns_positive_rates(self):
        report = bench.measure_chunking(_SAMPLE, chunker=_FakeChunker(), iterations=3)
        assert report["mode"] == "real"
        assert report["iterations"] == 3
        assert report["total_chunks"] == 6
        assert report["chunks_per_sec"] > 0
        assert report["chars_per_sec"] > 0
        assert report["avg_latency_ms"] >= 0
        assert report["input_chars"] == len(_SAMPLE)

    def test_real_engine_chunks_sample(self):
        """The real LegalParagraphEngine path is benchmarked (cheap, offline)."""
        report = bench.measure_chunking(_SAMPLE, chunker=None, iterations=1)
        assert report["mode"] == "real"
        assert report["total_chunks"] >= 1
        assert report["avg_chunks_per_run"] >= 1

    def test_delayed_chunker_latency_is_measured(self):
        report = bench.measure_chunking(_SAMPLE, chunker=_SlowChunker(), iterations=2)
        assert report["avg_latency_ms"] >= 9  # the 10ms sleep is captured


class TestMeasureEmbedding:
    def test_synthetic_mode_when_no_encoder_available(self, monkeypatch):
        """Without sentence-transformers the harness reports synthetic mode."""
        monkeypatch.setattr(bench, "_build_encoder", lambda: (bench._SyntheticEncoder(), "synthetic"))
        report = bench.measure_embedding(["text one", "text two"], encoder=None, iterations=2)
        assert report["mode"] == "synthetic"
        assert report["dim"] == 768
        assert report["vectors_per_sec"] > 0
        assert report["batch_size"] == 2
        assert report["total_vectors"] == 4

    def test_injected_encoder_is_real_mode(self):
        encoder = _CountingEncoder()
        report = bench.measure_embedding(["alpha", "beta", "gamma"], encoder=encoder, iterations=2)
        assert report["mode"] == "real"
        assert report["dim"] == 768
        assert report["total_vectors"] == 6
        assert report["vectors_per_sec"] > 0
        assert encoder.calls == 2  # one encode per iteration (batched)

    def test_all_vectors_match_dimension(self):
        encoder = _CountingEncoder(dim=384)
        report = bench.measure_embedding(["a", "b"], encoder=encoder, iterations=1)
        assert report["dim"] == 384


class TestMeasureStore:
    def test_skipped_when_qdrant_unavailable(self):
        report = bench.measure_store([], [], store=_NoStore(), iterations=2)
        assert report["mode"] == "skipped"
        assert "unavailable" in report["reason"]

    def test_live_with_in_memory_client(self):
        store = QdrantStore(client=InMemoryQdrantClient(), collection_name="bench_coll", vector_size=768)
        points = [Point(id=f"p{i}", vector=[0.1] * 768, payload={"document_id": "d1", "chunk_index": i}) for i in range(5)]
        report = bench.measure_store(points, [0.1] * 768, store=store, iterations=3)
        assert report["mode"] == "live"
        assert report["points_per_batch"] == 5
        assert report["upserts_per_sec"] > 0
        assert report["avg_search_latency_ms"] >= 0
        assert report["collection"] == "bench_coll"


# --------------------------------------------------------------------------- #
# Orchestrator + CLI
# --------------------------------------------------------------------------- #


class TestRunBenchmarks:
    def test_full_report_shape_and_json_safety(self):
        store = QdrantStore(client=InMemoryQdrantClient(), collection_name="bench_coll", vector_size=768)
        report = bench.run_benchmarks(
            _SAMPLE,
            chunker=_FakeChunker(),
            encoder=_CountingEncoder(),
            store=store,
            iterations=2,
            batch_size=4,
        )
        assert set(report) == {"sample", "chunking", "embedding", "vector_store", "environment"}
        assert report["sample"]["chars"] == len(_SAMPLE)
        assert report["chunking"]["mode"] == "real"
        assert report["embedding"]["mode"] == "real"
        assert report["vector_store"]["mode"] == "live"
        assert report["environment"]["RAG_VECTOR_SIZE"] == "768"
        # The whole report is JSON-serializable (the CLI prints it as-is).
        json.loads(json.dumps(report, default=str))

    def test_default_store_section_skips_gracefully(self):
        """No Qdrant config -> vector_store reports skipped, report still valid."""
        report = bench.run_benchmarks(
            _SAMPLE,
            chunker=_FakeChunker(),
            encoder=_CountingEncoder(),
            store=_NoStore(),
            iterations=1,
            batch_size=2,
        )
        assert report["vector_store"]["mode"] == "skipped"
        assert report["chunking"]["total_chunks"] > 0

    def test_default_components_build_real_report(self):
        """Real chunker + injected encoder still produce every section (offline).

        The encoder is injected so this test never downloads/loads a real
        sentence-transformers model (repo convention: no network in tests).
        """
        report = bench.run_benchmarks(
            _SAMPLE,
            chunker=None,  # real LegalParagraphEngine
            encoder=_CountingEncoder(),
            store=_NoStore(),
            iterations=1,
            batch_size=4,
            sample_chars=2_000,
        )
        assert report["chunking"]["mode"] == "real"
        assert report["embedding"]["mode"] == "real"
        assert report["vector_store"]["mode"] == "skipped"


class TestMain:
    def test_output_writes_json_report(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(bench, "run_benchmarks", lambda **kw: {"ok": True, "section": "value"})
        out = tmp_path / "report.json"
        code = bench.main(["--output", str(out)])
        assert code == 0
        assert json.loads(out.read_text(encoding="utf-8")) == {"ok": True, "section": "value"}
        captured = capsys.readouterr()
        assert '"ok": true' in captured.out  # also printed to stdout

    def test_missing_text_file_is_usage_error(self, tmp_path):
        code = bench.main(["--text", str(tmp_path / "nope.txt")])
        assert code == 2

    def test_benchmark_failure_is_error(self, monkeypatch):
        def boom(**kwargs):
            raise RuntimeError("embedding blew up")

        monkeypatch.setattr(bench, "run_benchmarks", boom)
        assert bench.main([]) == 1

    def test_invalid_iterations_is_usage_error(self):
        assert bench.main(["--iterations", "0"]) == 2


# End of test_rag_benchmarks.py

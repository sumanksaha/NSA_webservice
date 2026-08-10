"""RAG pipeline performance benchmark harness (Agent A, Phase 3 — Day 13).

Measures the three throughput/latency surfaces of the corpus pipeline with a
custom timing harness (``pytest-benchmark`` is deliberately NOT a dependency):

1. **chunking**     — real ``LegalParagraphEngine`` via ``Chunker``:
   chunks/sec + chars/sec + median latency per run.
2. **embedding**    — ``EmbeddingService.embed_batch``: vectors/sec + dim.
   Uses the REAL ``sentence-transformers`` model when installed; otherwise a
   synthetic numpy encoder measures the service-overhead path (reported as
   ``mode: "synthetic"``) so the harness never hard-fails on a dev box.
3. **vector store** — ``QdrantStore`` upsert + search latency. Runs live when
   ``RAG_QDRANT_URL`` is configured; reports ``mode: "skipped"`` otherwise.

Every component is injectable, so the unit tests
(``tests/test_rag_benchmarks.py``) exercise the harness with fakes and no
optional dependencies.  Output is a JSON report to stdout (optionally written
to a file via ``--output``).

Usage::

    python scripts/benchmark_rag.py                        # synthetic sample
    python scripts/benchmark_rag.py --text corpus/doc.txt  # real document
    python scripts/benchmark_rag.py --iterations 5 --batch-size 64 --json

Exit code 0 on success, 1 on benchmark failure, 2 on usage error.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

# Ensure the project root is on sys.path so that "from app" imports work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402


# --------------------------------------------------------------------------- #
# Sample text
# --------------------------------------------------------------------------- #


def build_sample_text(size_chars: int = 30_000) -> str:
    """Generate a synthetic legal document of roughly ``size_chars`` chars.

    Repeats a section template (marker chains, statutory citations) so the
    real paragraph engine has realistic legal structure to chunk.
    """
    template = (
        "Section {n}\n\n"
        "{n}(1) The Food Authority shall ensure that food business operators "
        "comply with the provisions of the Food Safety and Standards Act, 2006.\n"
        "{n}(2) The State Food Safety Authorities shall coordinate with the "
        "Food Authority for the purposes of this section.\n"
        "{n}(3)(a) An authorised officer may enter any food business premises "
        "for inspection and may seize samples. Section {next_n} of the Act "
        "prescribes the penalties for non-compliance.\n\n"
    )
    parts: list[str] = []
    i = 1
    while sum(len(p) for p in parts) < size_chars:
        parts.append(template.format(n=i, next_n=i + 1))
        i += 1
    return "\n".join(parts)


def _split_into(text: str, n: int) -> list[str]:
    """Split ``text`` into ``n`` roughly-equal word buckets (embedding input)."""
    words = text.split()
    if not words:
        return [""]
    size = max(1, math.ceil(len(words) / max(n, 1)))
    return [" ".join(words[i : i + size]) for i in range(0, len(words), size)]


# --------------------------------------------------------------------------- #
# Benchmark sections (each returns a JSON-safe dict)
# --------------------------------------------------------------------------- #


def measure_chunking(
    text: str,
    chunker: Any | None = None,
    iterations: int = 3,
) -> dict[str, Any]:
    """Chunk ``text`` ``iterations`` times; return throughput stats."""
    if chunker is None:
        from app.rag.chunker import Chunker

        chunker = Chunker()
    timings: list[float] = []
    chunk_counts: list[int] = []
    for _ in range(iterations):
        start = time.perf_counter()
        chunks = chunker.chunk_text(text, {"document_id": "benchmark", "type": "act"})
        timings.append(time.perf_counter() - start)
        chunk_counts.append(len(chunks))
    elapsed_s = sum(timings)
    total_chunks = sum(chunk_counts)
    return {
        "mode": "real",
        "iterations": iterations,
        "input_chars": len(text),
        "total_chunks": total_chunks,
        "avg_chunks_per_run": round(total_chunks / iterations, 2),
        "elapsed_s": round(elapsed_s, 4),
        "chunks_per_sec": round(total_chunks / elapsed_s, 2) if elapsed_s else 0.0,
        "chars_per_sec": round(len(text) * iterations / elapsed_s, 2) if elapsed_s else 0.0,
        "avg_latency_ms": round(elapsed_s / iterations * 1000, 2),
    }


class _SyntheticEncoder:
    """Deterministic numpy encoder used when sentence-transformers is absent.

    Produces 768-dim normalized vectors so ``EmbeddingService``'s
    validate/embed path is measured end-to-end (service overhead only).
    """

    def __init__(self, dim: int = 768) -> None:
        self._dim = dim

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, texts: list[str]) -> Any:
        import numpy as np

        rng = np.random.default_rng(42)
        arr = rng.standard_normal((len(list(texts)), self._dim)).astype("float32")
        return arr


def _build_encoder() -> tuple[Any, str]:
    """Return ``(encoder, mode)`` — real sentence-transformers or synthetic."""
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]

        model_name = os.environ.get("RAG_EMBEDDING_MODEL")
        if not model_name:
            from app.rag.embedding_service import EmbeddingService

            model_name = EmbeddingService().model_name
        return SentenceTransformer(model_name), "real"
    except Exception as exc:  # noqa: BLE001 - graceful degradation
        print(f"benchmark: sentence-transformers unavailable ({exc}); using synthetic encoder", file=sys.stderr)
        return _SyntheticEncoder(), "synthetic"


def measure_embedding(
    texts: list[str],
    encoder: Any | None = None,
    iterations: int = 3,
) -> dict[str, Any]:
    """Embed ``texts`` (one batch) ``iterations`` times; return vectors/sec.

    Args:
        texts: Batch of texts to embed.
        encoder: Optional pre-built encoder (injected for tests). When None,
            a real sentence-transformers model is loaded if available, else a
            synthetic numpy encoder is used (``mode: "synthetic"``).
    """
    from app.rag.embedding_service import EmbeddingService

    if encoder is None:
        encoder, mode = _build_encoder()
    else:
        mode = "real"
    service = EmbeddingService(encoder=encoder)

    timings: list[float] = []
    counts: list[int] = []
    for _ in range(iterations):
        start = time.perf_counter()
        vectors = service.embed_batch(texts)
        timings.append(time.perf_counter() - start)
        counts.append(len(vectors))
    elapsed_s = sum(timings)
    total_vectors = sum(counts)
    dim = service.vector_size
    return {
        "mode": mode,
        "model": "synthetic-numpy" if mode == "synthetic" else service.model_name,
        "iterations": iterations,
        "batch_size": len(texts),
        "total_vectors": total_vectors,
        "dim": dim,
        "elapsed_s": round(elapsed_s, 4),
        "vectors_per_sec": round(total_vectors / elapsed_s, 2) if elapsed_s else 0.0,
        "avg_batch_latency_ms": round(elapsed_s / iterations * 1000, 2),
    }


def measure_store(
    points: list[Any],
    query_vector: list[float],
    store: Any | None = None,
    iterations: int = 3,
) -> dict[str, Any]:
    """Measure Qdrant upsert throughput + search latency (or skip).

    Returns ``{"mode": "skipped"}`` when Qdrant is not configured/available.
    """
    from app.rag.qdrant_client import QdrantStore

    # Use a DEDICATED benchmark collection so a live run never touches (or
    # creates) the production ``fssai_legal_768`` collection. Operators can
    # drop the bench_* collection after a run.
    if store is None:
        store = QdrantStore(collection_name=f"fssai_legal_bench_{int(time.time())}")
    try:
        store.ensure_collection(create_payload_indexes=False)
    except Exception as exc:  # noqa: BLE001 - no client / not configured
        return {"mode": "skipped", "reason": str(exc)}

    upsert_timings: list[float] = []
    search_timings: list[float] = []
    try:
        for _ in range(iterations):
            start = time.perf_counter()
            store.upsert_points(points)
            upsert_timings.append(time.perf_counter() - start)

            start = time.perf_counter()
            store.search_points(query_vector, top_k=5)
            search_timings.append(time.perf_counter() - start)
    except Exception as exc:  # noqa: BLE001 - live store failure (e.g. offline cluster)
        return {"mode": "error", "reason": str(exc)}

    upsert_s = sum(upsert_timings)
    return {
        "mode": "live",
        "collection": store.collection_name,
        "vector_size": store.vector_size,
        "points_per_batch": len(points),
        "iterations": iterations,
        "upserts_per_sec": round(len(points) * iterations / upsert_s, 2) if upsert_s else 0.0,
        "avg_upsert_latency_ms": round(upsert_s / iterations * 1000, 2),
        "avg_search_latency_ms": round(sum(search_timings) / iterations * 1000, 2),
    }


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


def run_benchmarks(
    text: str | None = None,
    *,
    chunker: Any | None = None,
    encoder: Any | None = None,
    store: Any | None = None,
    iterations: int = 3,
    batch_size: int = 32,
    sample_chars: int = 30_000,
    store_points: int = 50,
) -> dict[str, Any]:
    """Run all three benchmark sections; return the JSON-safe report.

    All components are injectable for tests; defaults build the real ones.
    """
    text = text or build_sample_text(sample_chars)
    batch = _split_into(text, batch_size)

    chunking = measure_chunking(text, chunker=chunker, iterations=iterations)

    embedding = measure_embedding(batch, encoder=encoder, iterations=iterations)

    # Vector-store benchmark: rebuild lightweight points from the chunked
    # output (flat dim-matching vectors — this measures store latency, not
    # embedding quality). A default ``QdrantStore`` skips when unconfigured.
    from app.rag.qdrant_client import Point, QdrantStore

    # Dedicated benchmark collection (never the production one).
    if store is None:
        store = QdrantStore(collection_name=f"fssai_legal_bench_{int(time.time())}")
    dim = store.vector_size
    flat = [0.1] * dim
    chunks = chunker.chunk_text(text, {"document_id": "benchmark", "type": "act"}) if chunker else []
    points = [Point(id=c.chunk_id, vector=flat, payload=c.to_payload()) for c in chunks[:store_points]]
    vector_store = measure_store(points, flat, store=store, iterations=iterations)

    return {
        "sample": {"chars": len(text), "embedding_pieces": len(batch)},
        "chunking": chunking,
        "embedding": embedding,
        "vector_store": vector_store,
        "environment": _environment(),
    }


def _environment() -> dict[str, Any]:
    """Capture versions / config relevant to interpreting the numbers."""
    env: dict[str, Any] = {
        "RAG_VECTOR_SIZE": os.environ.get("RAG_VECTOR_SIZE", "768"),
        "RAG_EMBEDDING_MODEL": os.environ.get("RAG_EMBEDDING_MODEL", ""),
        "RAG_QDRANT_URL": "set" if os.environ.get("RAG_QDRANT_URL") else "",
    }
    for pkg in ("qdrant-client", "sentence-transformers", "torch", "numpy"):
        try:
            mod = __import__(pkg.replace("-", "_"))
            env[pkg] = getattr(mod, "__version__", "installed")
        except Exception:  # noqa: BLE001
            env[pkg] = "not installed"
    return env


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark the RAG corpus pipeline (chunking / embedding / vector store).",
    )
    parser.add_argument(
        "--text",
        help="Optional legal document file to benchmark (default: synthetic sample).",
    )
    parser.add_argument(
        "--sample-chars",
        type=int,
        default=30_000,
        help="Synthetic sample size in chars (used when --text is omitted).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Runs per benchmark section (default 3).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Pieces the embedding batch is split into (default 32).",
    )
    parser.add_argument(
        "--json",
        dest="pretty_json",
        action="store_true",
        help="Pretty-print the JSON report.",
    )
    parser.add_argument(
        "--output",
        help="Write the JSON report to a file (also printed to stdout).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")

    args = build_parser().parse_args(argv)
    if args.iterations < 1 or args.batch_size < 1:
        print("error: --iterations and --batch-size must be >= 1", file=sys.stderr)
        return 2

    text: str | None = None
    if args.text:
        path = Path(args.text)
        if not path.is_file():
            print(f"error: not a file: {args.text}", file=sys.stderr)
            return 2
        text = path.read_text(encoding="utf-8", errors="replace")

    try:
        report = run_benchmarks(
            text=text,
            iterations=args.iterations,
            batch_size=args.batch_size,
            sample_chars=args.sample_chars,
        )
    except Exception as exc:  # noqa: BLE001 - surface as a clear CLI error
        print(f"error: benchmark failed: {exc}", file=sys.stderr)
        return 1

    indent = 2 if args.pretty_json else None
    output = json.dumps(report, indent=indent, default=str)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"report written to {args.output}", file=sys.stderr)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

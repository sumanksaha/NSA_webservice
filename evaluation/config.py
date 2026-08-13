"""Experiment configuration — frozen parameters + reproducible hashes.

Everything that affects an experimental result is declared here so the
deliverables can state the exact configuration used (reproducibility).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_DIR = PROJECT_ROOT / "benchmark"
BENCHMARK_FILE = BENCHMARK_DIR / "benchmark_v1.0.jsonl"
GOLD_PROVISIONS_FILE = BENCHMARK_DIR / "gold_provisions_v1.0.json"
GOLD_SOURCES_FILE = BENCHMARK_DIR / "gold_sources_v1.0.json"
BENCHMARK_METADATA_FILE = BENCHMARK_DIR / "benchmark_metadata_v1.0.json"

OUT_DIR = PROJECT_ROOT / "evaluation" / "out"
RAW_DIR = OUT_DIR / "raw"
CACHE_DIR = OUT_DIR / "cache"

# --------------------------------------------------------------------------- #
# Retrieval parameters (kept constant across all arms — §5 of the protocol)
# --------------------------------------------------------------------------- #
TOP_K = 20                 # maximum retrieval depth used by every arm
RERANK_CANDIDATE_K = 50    # candidate pool size before reranking (ARM F)
RERANK_FINAL_K = 20        # final evidence depth after reranking
HYBRID_RRF_K = 60.0        # production RRF constant (HybridRetriever default)
SPARSE_THRESHOLD = 0.0     # BM25 path: no score threshold (rank-based fusion)
RETRIEVAL_KS = (1, 3, 5, 10, 20)  # K values reported for every arm

# Gains used for nDCG (per evaluation_rubric_v1.0.md §1).
GAIN_PRIMARY = 2.0
GAIN_ACCEPTABLE = 1.0
GAIN_SUPPORTING = 0.0

# Bootstrap / significance settings.
BOOTSTRAP_ITERATIONS = 10_000
BOOTSTRAP_SEED = 20260811
MCNAMER_ALPHA = 0.05

# --------------------------------------------------------------------------- #
# ARM registry (order matters — used by every phase)
# --------------------------------------------------------------------------- #
ARMS = [
    "A_dense",
    "B_sparse",
    "C_dense_sparse",
    "D_kg_retrieval",
    "E_dense_sparse_kg",
    "F_dense_sparse_kg_rerank",
]

# Offline fusion-repair arms (2026-08-12): RRF-fused rankings built from the
# cached dense/sparse/KG results by evaluation.fusion — no re-retrieval.
FUSION_ARMS = [
    "C_rrf_sanity",
    "G_ds_kg_rrf",
    "E_ds_kg_rrf",
    "H_dense_kg_rrf",
    # Provision-dedup variants (2026-08-12): redundant KG items are dropped
    # before fusing so novel candidates get the freed slots.
    "G_ds_kg_rrf_dedup",
    "H_dense_kg_rrf_dedup",
]

# Answer-generation conditions.  ``retrieved_kg`` = ARM F evidence + KG
# expansion provisions fused into the prompt (measures the true
# answer-level value of KG expansion — follow-up wiring 2026-08-12).
GEN_CONDITIONS = ["oracle", "retrieved", "retrieved_kg"]

#: Context slots reserved for KG provisions in the retrieved_kg condition
#: (mirrors ContextBuilder.max_chunks=10 minus the retrieved budget).
KG_CONTEXT_SLOTS = 5

# --------------------------------------------------------------------------- #
# Config hash
# --------------------------------------------------------------------------- #
def _collect_config_snapshot() -> dict:
    """Gather the exact experiment configuration as a JSON-safe dict."""
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    collections = {
        "default": os.environ.get("RAG_QDRANT_COLLECTION", ""),
        "env": os.environ.get("RAG_QDRANT_COLLECTION_ENV", ""),
        "commercial": os.environ.get("RAG_QDRANT_COLLECTION_COMMERCIAL", ""),
        "animal": os.environ.get("RAG_QDRANT_COLLECTION_ANIMAL", ""),
        "wb_state": os.environ.get("RAG_QDRANT_COLLECTION_WB_STATE", ""),
        "criminal": os.environ.get("RAG_QDRANT_COLLECTION_CRIMINAL", ""),
    }
    qdrant_url = os.environ.get("RAG_QDRANT_URL", "")
    return {
        "benchmark_version": "1.0",
        "benchmark_sha256": sha256_file(BENCHMARK_FILE),
        "top_k": TOP_K,
        "rerank_candidate_k": RERANK_CANDIDATE_K,
        "rerank_final_k": RERANK_FINAL_K,
        "rrf_k": HYBRID_RRF_K,
        "retrieval_ks": list(RETRIEVAL_KS),
        "qdrant_url": (qdrant_url[:40] + "…") if qdrant_url else "",
        "qdrant_collections": collections,
        "dense_embedding_model": os.environ.get("RAG_EMBEDDING_MODEL", ""),
        "sparse_model": os.environ.get("RAG_SPARSE_MODEL", "Qdrant/bm25"),
        "reranker_model": os.environ.get("RAG_RERANKER_MODEL", ""),
        "llm_model": os.environ.get("RAG_LLM_MODEL", ""),
        "llm_base_url": os.environ.get("OPENAI_BASE_URL", ""),
        "llm_use_stub": os.environ.get("RAG_USE_STUB_LLM", "false"),
        "kg_max_provisions": os.environ.get("RAG_KG_MAX_PROVISIONS", "5"),
        "kg_context_slots": KG_CONTEXT_SLOTS,
        "neo4j_database": os.environ.get("NEO4J_DATABASE", "neo4j"),
        "arms": ARMS,
        "fusion_arms": FUSION_ARMS,
        "fusion_rrf_k": 60.0,
        "gen_conditions": GEN_CONDITIONS,
        "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "protocol": "master-prompt v1 (frozen system, no production changes)",
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def config_hash() -> str:
    """SHA-256 of the canonical JSON of the config snapshot."""
    raw = json.dumps(_collect_config_snapshot(), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def write_run_config() -> dict:
    """Persist ``run_config.json`` with the full snapshot + hash."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = _collect_config_snapshot()
    snapshot["config_hash"] = config_hash()
    (OUT_DIR / "run_config.json").write_text(
        json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8"
    )
    return snapshot

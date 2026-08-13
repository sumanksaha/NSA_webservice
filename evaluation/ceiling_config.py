"""RANKING_CEILING_V1 — frozen experiment configuration.

Every parameter that affects the retrieval-ceiling experiment is declared
here so the deliverables can state the exact configuration (reproducibility,
protocol §1).  This module *freezes* the experiment:

* experiment ID + timestamp
* git commit/hash + benchmark SHA-256
* Qdrant collections (+ point counts) and embedding / sparse / reranker models
* Neo4j database + live graph node counts
* retrieval depths for every arm
* fusion constant (production RRF k=60)

Nothing here changes production code — it only declares the frozen surface
the diagnostic reads from.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- #
# Experiment identity (env-overridable so follow-up variants like the
# identity-backfilled V2 can run without touching the frozen V1 outputs)
# --------------------------------------------------------------------------- #
EXPERIMENT_ID = os.environ.get("RANKING_CEILING_ID", "RANKING_CEILING_V1")
EXPERIMENT_LABEL = "Retrieval ceiling — where is the gold provision?"
TIMESTAMP = time.strftime("%Y-%m-%dT%H:%M:%S%z")

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / os.environ.get("CEILING_OUT_DIR", "ceiling_v1")
# Raw retrieval caches are shared across variants (retrieval is vector-based
# and unaffected by payload identity; only resolution changes).  Default
# points at the V1 raw cache; override for a genuinely new retrieval run.
RAW_DIR = PROJECT_ROOT / "evaluation" / "out" / os.environ.get("CEILING_RAW_DIR", "ceiling_v1/raw")

# --------------------------------------------------------------------------- #
# Evaluation depths (protocol §3 — never stop at K=10)
# --------------------------------------------------------------------------- #
DEPTHS = (5, 10, 20, 50, 100, 200, 500)
NDCG_KS = (10, 20, 50)

# --------------------------------------------------------------------------- #
# Retrieval depths per arm (env-overridable — V5 runs the full 500-depth
# candidate pipeline per RANKING_CEILING_V5 protocol Task 1)
# --------------------------------------------------------------------------- #
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


DENSE_DEPTH = 500        # ARM A
SPARSE_DEPTH = 500       # ARM B
HYBRID_DEPTH = 500       # ARM C  (frozen fusion, just deeper)
KG_DEPTH = _env_int("CEILING_KG_DEPTH", 200)      # ARM D (diagnostic extends)
ORACLE_DEPTH = 500       # §13 gold-text oracle
EXACT_DEPTH = 100        # §14 exact legal identifier test

# ARM E union composition (protocol §6): dense@D + sparse@D + KG@D.
# V4 measured 200→500 slice growth: ceiling 55.6% → 65.8%; V5 runs D=500.
UNION_DENSE_DEPTH = _env_int("CEILING_UNION_DENSE_DEPTH", 200)
UNION_SPARSE_DEPTH = _env_int("CEILING_UNION_SPARSE_DEPTH", 200)
UNION_KG_DEPTH = _env_int("CEILING_UNION_KG_DEPTH", 200)

# RRF constant — must match production HybridRetriever (Cormack et al. 2009).
RRF_K = 60.0

# ARM D: production contract limit vs the diagnostic depth used here.
KG_PRODUCTION_LIMIT = 10
KG_DIAGNOSTIC_LIMIT = KG_DEPTH

# Per-question evaluation: gold units considered relevant = primary + acceptable
# (matches the existing harness).  All gold units (incl. supporting) reported
# separately as recall_all.

# --------------------------------------------------------------------------- #
# Live arms (ran by run_ceiling.py)
# --------------------------------------------------------------------------- #
LIVE_ARMS = [
    "A_dense",           # dense Qdrant @500
    "B_sparse",          # sparse / BM25 @500
    "C_hybrid",          # dense+sparse (frozen fusion) @500
    "D_kg",              # KG graph-RAG contract @200 (diagnostic depth)
    "O_dense",           # §13 oracle: gold provision title -> dense @500
    "O_sparse",          # §13 oracle: gold provision title -> sparse @500
    "X_exact",           # §14 exact identifier query -> dense@100 + sparse@100
]

# Offline arms (built by report_ceiling.py from the live caches):
#   E_union_ordered — dense@200 + sparse@200 + KG@200, ordered dedup union
#   E_union_rrf     — RRF(dense@200, sparse@200, KG@200) interleaved
#   E_union_pool    — the dedup union pool as one unranked set (ceiling)
#   O_hybrid        — RRF(O_dense, O_sparse) — §13 oracle hybrid

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head() -> dict:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10,
        )
        commit = out.stdout.strip()
    except Exception:
        commit = ""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ci"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10,
        )
        date = out.stdout.strip()
    except Exception:
        date = ""
    return {"commit": commit, "commit_date": date}


def _mask(value: str, keep: int = 24) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return value
    return value[:keep] + "…"


def _qdrant_snapshot(app) -> dict:
    """Collection names (config) + live point counts + sparse capability."""
    cfg = app.config
    names = [
        cfg.get("RAG_QDRANT_COLLECTION", "fssai_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_ENV", "env_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_COMMERCIAL", "commercial_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_ANIMAL", "animal_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_WB_STATE", "wb_state_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_CRIMINAL", "criminal_legal_768"),
    ]
    out: dict[str, dict] = {}
    from app.rag.qdrant_client import QdrantStore

    for name in dict.fromkeys(names):
        store = QdrantStore(collection_name=name)
        try:
            client = store._get_client()
            count = client.count(name, exact=True).count if client else None
        except Exception:
            count = None
        try:
            sparse = bool(store.has_sparse_vectors())
        except Exception:
            sparse = False
        out[name] = {"points": count, "has_sparse": sparse}
    return out


def _kg_snapshot() -> dict:
    """Neo4j database + live node counts (read-only)."""
    try:
        from kg.queries import LegalKGQueries

        q = LegalKGQueries()
        rows = q._execute("MATCH (n) RETURN labels(n)[0] AS lbl, count(*) AS c")
        counts = {r.get("lbl"): r.get("c") for r in rows}
    except Exception as exc:  # noqa: BLE001 - best-effort freeze
        counts = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "database": os.environ.get("NEO4J_DATABASE", "neo4j"),
        "uri": _mask(os.environ.get("NEO4J_URI", ""), 40),
        "node_counts": counts,
    }


def collect_freeze(app) -> dict:
    """Assemble the full immutable experiment freeze."""
    from evaluation.config import BENCHMARK_FILE

    benchmark_dir = PROJECT_ROOT / "benchmark"
    freeze = {
        "experiment_id": EXPERIMENT_ID,
        "label": EXPERIMENT_LABEL,
        "timestamp": TIMESTAMP,
        "git": git_head(),
        "benchmark": {
            "version": "1.0",
            "sha256": sha256_file(BENCHMARK_FILE),
            "n_questions": sum(1 for _ in open(BENCHMARK_FILE, encoding="utf-8")),
        },
        "python": platform.python_version(),
        "platform": platform.platform(),
        "qdrant": {
            "url": _mask(os.environ.get("RAG_QDRANT_URL", ""), 40),
            "collections": _qdrant_snapshot(app),
        },
        "embedding_model": app.config.get("RAG_EMBEDDING_MODEL", ""),
        "sparse_model": app.config.get("RAG_SPARSE_MODEL", "Qdrant/bm25"),
        "reranker_model": app.config.get("RAG_RERANKER_MODEL", ""),
        "vector_size": app.config.get("RAG_VECTOR_SIZE", 768),
        "neo4j": _kg_snapshot(),
        "depths": {
            "evaluation": list(DEPTHS),
            "dense": DENSE_DEPTH,
            "sparse": SPARSE_DEPTH,
            "hybrid": HYBRID_DEPTH,
            "kg_diagnostic": KG_DIAGNOSTIC_LIMIT,
            "kg_production": KG_PRODUCTION_LIMIT,
            "oracle": ORACLE_DEPTH,
            "exact_identifier": EXACT_DEPTH,
            "union": {
                "dense": UNION_DENSE_DEPTH,
                "sparse": UNION_SPARSE_DEPTH,
                "kg": UNION_KG_DEPTH,
            },
        },
        "fusion": {"rrf_k": RRF_K, "method": "Reciprocal Rank Fusion (Cormack 2009), rank-based 1/(rank+k)"},
        "llm": {"used": False, "note": "retrieval-only experiment — no generative LLM calls"},
        "protocol": "master retrieval-ceiling prompt v1 — no production code modified",
    }
    freeze["freeze_hash"] = hashlib.sha256(
        json.dumps(freeze, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return freeze


def write_freeze(app) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    freeze = collect_freeze(app)
    (OUT_DIR / "run_config.json").write_text(
        json.dumps(freeze, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return freeze
